"""
Public API entry points.

    scan(source, language)          -> ScanResult   ← the atomic unit
    scan_file(path)                 -> ScanResult
    scan_dir(path, pattern)         -> list[ScanResult]

scan() is pure: no I/O, no DB, no side effects.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from code_indexer.languages import get_adapter, language_for_file
from code_indexer.languages.base import RawExtraction, RawExpression, RawSymbol, RawReference
from code_indexer.models import (
    ExpressionRow,
    FileRow,
    ReferenceRow,
    ScanResult,
    SymbolRow,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan(source: str, language: str, path: str = "<source>") -> ScanResult:
    """
    Parse source code and return a structured ScanResult.

    This function is pure — no I/O, no DB connection required.

    Args:
        source:   raw source code string
        language: language name, e.g. "python", "javascript"
        path:     optional logical path, stored in the file row

    Returns:
        ScanResult with .to_sql(), .to_dict(), .insert_into() methods

    Example:
        result = scan(open("main.py").read(), "python", "main.py")
        print(result.to_sql("sqlite"))
    """
    adapter = get_adapter(language)
    extraction = adapter.extract(source)
    return _build_result(source, language, path, extraction)


def scan_file(path: str | Path) -> ScanResult:
    """
    Scan a file, inferring language from its extension.

    Args:
        path: path to the source file

    Returns:
        ScanResult
    """
    path = Path(path)
    language = language_for_file(path)
    source = path.read_text(encoding="utf-8", errors="replace")
    return scan(source, language, str(path))


def scan_dir(
    directory: str | Path,
    pattern: str = "**/*",
    languages: list[str] | None = None,
) -> Iterator[ScanResult]:
    """
    Scan all recognized source files in a directory tree.

    Args:
        directory: root directory to scan
        pattern:   glob pattern relative to directory (default: all files)
        languages: optional filter, e.g. ["python"]. If None, all supported.

    Yields:
        ScanResult for each recognized file
    """
    from code_indexer.languages import language_for_file, supported_extensions

    directory = Path(directory)
    for file_path in directory.glob(pattern):
        if not file_path.is_file():
            continue
        try:
            lang = language_for_file(file_path)
        except ValueError:
            continue
        if languages and lang not in languages:
            continue
        try:
            yield scan_file(file_path)
        except Exception as e:
            # Don't let one bad file stop the whole scan
            import warnings
            warnings.warn(f"Failed to scan {file_path}: {e}", stacklevel=2)


# ---------------------------------------------------------------------------
# ScanResult builder — converts RawExtraction -> typed rows with UUIDs
# ---------------------------------------------------------------------------

def _build_result(
    source: str,
    language: str,
    path: str,
    extraction: RawExtraction,
) -> ScanResult:
    content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    # Include path in the seed so two files with identical content at different
    # paths get distinct IDs. content_hash is kept for incremental-index checks.
    file_id = _deterministic_uuid(path + ":" + content_hash)
    scanned_at = datetime.now(timezone.utc).isoformat()

    file_row = FileRow(
        id=file_id,
        path=path,
        language=language,
        content_hash=content_hash,
        scanned_at=scanned_at,
    )

    # --- Symbols ---
    # Map local_key -> UUID so expressions/references can link back
    symbol_id_map: dict[str, str] = {}
    symbol_rows: list[SymbolRow] = []

    for raw_sym in extraction.symbols:
        sym_id = _deterministic_uuid(file_id + raw_sym.local_key)
        symbol_id_map[raw_sym.local_key] = sym_id
        symbol_rows.append(SymbolRow(
            id=sym_id,
            file_id=file_id,
            name=raw_sym.name,
            kind=raw_sym.kind,
            start_byte=raw_sym.start_byte,
            end_byte=raw_sym.end_byte,
            start_line=raw_sym.start_line,
            end_line=raw_sym.end_line,
            signature=raw_sym.signature,
            parent_id=symbol_id_map.get(raw_sym.parent_local_key or ""),
        ))

    # --- Expressions ---
    expr_id_map: dict[str, str] = {}
    expression_rows: list[ExpressionRow] = []

    for raw_expr in extraction.expressions:
        expr_id = _deterministic_uuid(file_id + raw_expr.local_key)
        expr_id_map[raw_expr.local_key] = expr_id
        expression_rows.append(ExpressionRow(
            id=expr_id,
            file_id=file_id,
            symbol_id=symbol_id_map.get(raw_expr.symbol_local_key or ""),
            parent_id=expr_id_map.get(raw_expr.parent_local_key or ""),
            kind=raw_expr.kind,
            source_text=raw_expr.source_text,
            start_byte=raw_expr.start_byte,
            end_byte=raw_expr.end_byte,
            start_line=raw_expr.start_line,
            end_line=raw_expr.end_line,
            depth=raw_expr.depth,
            extra=raw_expr.extra,
        ))

    # --- References ---
    reference_rows: list[ReferenceRow] = []

    for raw_ref in extraction.references:
        ref_id = _deterministic_uuid(
            file_id + raw_ref.expression_local_key + raw_ref.callee_name
        )
        reference_rows.append(ReferenceRow(
            id=ref_id,
            file_id=file_id,
            expression_id=expr_id_map.get(raw_ref.expression_local_key, ""),
            callee_name=raw_ref.callee_name,
            from_symbol_id=symbol_id_map.get(raw_ref.from_symbol_local_key or ""),
            to_symbol_id=None,  # resolved in a separate pass
        ))

    return ScanResult(
        file=file_row,
        symbols=symbol_rows,
        expressions=expression_rows,
        references=reference_rows,
    )


def resolve_references(engine) -> int:
    """
    Resolve callee names to symbol IDs by exact name match.

    Writes to_symbol_id on all unresolved references where callee_name
    matches a symbol name in the same DB. Returns the count resolved.

    This is intentionally naïve (first-match, name-only). Scope-aware
    resolution is a future concern; even name-only matching unblocks
    callers/callees/callDepth queries for the common case.
    """
    from sqlalchemy import update, select
    from code_indexer.schema import references_table, symbols_table

    sym_subq = (
        select(symbols_table.c.id)
        .where(symbols_table.c.name == references_table.c.callee_name)
        .limit(1)
        .correlate(references_table)
        .scalar_subquery()
    )
    with engine.begin() as conn:
        result = conn.execute(
            update(references_table)
            .where(references_table.c.to_symbol_id.is_(None))
            .values(to_symbol_id=sym_subq)
        )
        return result.rowcount


def _deterministic_uuid(seed: str) -> str:
    """
    Generate a deterministic UUID v5 from a seed string.
    Same input always produces the same UUID — making scan() idempotent.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
