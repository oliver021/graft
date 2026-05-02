"""
Public API entry points.

    scan(source, language)          -> ScanResult   ← the atomic unit
    scan_file(path)                 -> ScanResult
    scan_dir(path, pattern)         -> Iterator[ScanResult | ScanError]

scan() is pure: no I/O, no DB, no side effects.

Resilience types
----------------
    IndexerError  — structured exception raised by scan_file() on I/O failure
    SkippedFile   — returned (not raised) when a file is deliberately skipped
                    (too large, binary content, unsupported extension)
    ScanError     — yielded by scan_dir() for files that could not be scanned;
                    never stops the iteration
"""

from __future__ import annotations

import hashlib
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

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
# Resilience constants (tunable at module level)
# ---------------------------------------------------------------------------

#: Files larger than this (bytes) are skipped rather than loaded into memory.
MAX_FILE_BYTES: int = 1 * 1024 * 1024  # 1 MB

# ---------------------------------------------------------------------------
# Resilience types
# ---------------------------------------------------------------------------

class IndexerError(Exception):
    """
    Raised by scan_file() when a file cannot be read or processed.

    Attributes
    ----------
    path    : the file that caused the problem
    reason  : human-readable explanation
    original: the underlying exception, if any
    """

    def __init__(self, path: str | Path, reason: str, original: BaseException | None = None) -> None:
        super().__init__(f"Cannot index {path}: {reason}")
        self.path     = Path(path)
        self.reason   = reason
        self.original = original


@dataclass
class SkippedFile:
    """
    Returned by scan_file() when a file is deliberately skipped.

    This is *not* an exception — it is a normal result indicating the file
    was seen but excluded before parsing (too large, binary, unsupported type).

    Attributes
    ----------
    path   : file that was skipped
    reason : human-readable explanation, e.g. "file too large (2.3 MB)"
    """
    path:   Path
    reason: str


@dataclass
class ScanError:
    """
    Yielded by scan_dir() for files that could not be scanned.

    Callers can collect these to build a summary of what failed without
    stopping the rest of the directory scan.

    Attributes
    ----------
    path      : file that caused the error
    reason    : human-readable explanation
    exception : the underlying exception
    """
    path:      Path
    reason:    str
    exception: BaseException


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


def scan_file(
    path: str | Path,
    *,
    max_bytes: int = MAX_FILE_BYTES,
) -> ScanResult | SkippedFile:
    """
    Scan a file, inferring language from its extension.

    Returns a ScanResult on success, or a SkippedFile when the file is
    deliberately excluded (too large, binary content).

    Raises:
        IndexerError: if the file cannot be read due to an I/O problem
                      (permission denied, file vanished, etc.)
        ValueError:   if the file extension is not supported (re-raised as-is)

    Args:
        path:      path to the source file
        max_bytes: files larger than this are returned as SkippedFile
                   (default: MAX_FILE_BYTES = 1 MB)
    """
    path = Path(path)
    language = language_for_file(path)  # raises ValueError for unknown ext

    # Fix #1: skip files that are too large to process safely
    try:
        size = path.stat().st_size
    except OSError as e:
        raise IndexerError(path, f"cannot stat file: {e}", e) from e

    if size > max_bytes:
        mb = size / (1024 * 1024)
        return SkippedFile(path=path, reason=f"file too large ({mb:.1f} MB; limit {max_bytes // (1024*1024)} MB)")

    # Fix #3: wrap all I/O in a structured exception
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise IndexerError(path, f"cannot read file: {e}", e) from e

    # Fix #2: detect binary content — null bytes survive errors="replace" as \x00
    if "\x00" in source[:1024]:
        return SkippedFile(path=path, reason="binary content detected (null bytes in first 1 KB)")

    return scan(source, language, str(path))


def scan_dir(
    directory: str | Path,
    pattern: str = "**/*",
    languages: list[str] | None = None,
) -> Iterator[Union[ScanResult, SkippedFile, ScanError]]:
    """
    Scan all recognized source files in a directory tree.

    Yields ScanResult for each successfully parsed file, SkippedFile for
    deliberately excluded files (too large, binary), and ScanError for files
    that raised an unexpected exception — without stopping the iteration.

    Args:
        directory: root directory to scan
        pattern:   glob pattern relative to directory (default: all files)
        languages: optional filter, e.g. ["python"]. If None, all supported.

    Yields:
        ScanResult | SkippedFile | ScanError for each file encountered
    """
    from code_indexer.languages import language_for_file, supported_extensions

    directory = Path(directory)
    for file_path in directory.glob(pattern):
        if not file_path.is_file():
            continue
        try:
            lang = language_for_file(file_path)
        except ValueError:
            continue  # unsupported extension — silently skip, not an error
        if languages and lang not in languages:
            continue
        try:
            # Fix #9: yield SkippedFile and ScanError instead of warnings.warn
            result = scan_file(file_path)
            yield result
        except IndexerError as e:
            yield ScanError(path=file_path, reason=e.reason, exception=e)
        except Exception as e:
            yield ScanError(path=file_path, reason=str(e), exception=e)


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
        # Fix #7: skip references whose expression anchor is missing from the map.
        # An empty/missing expression_id would be a broken FK that most DBs reject.
        expression_id = expr_id_map.get(raw_ref.expression_local_key)
        if not expression_id:
            warnings.warn(
                f"Skipping reference to '{raw_ref.callee_name}': "
                f"expression key '{raw_ref.expression_local_key}' not found in map",
                stacklevel=2,
            )
            continue

        ref_id = _deterministic_uuid(
            file_id + raw_ref.expression_local_key + raw_ref.callee_name
        )
        reference_rows.append(ReferenceRow(
            id=ref_id,
            file_id=file_id,
            expression_id=expression_id,
            callee_name=raw_ref.callee_name,
            from_symbol_id=symbol_id_map.get(raw_ref.from_symbol_local_key or ""),
            to_symbol_id=None,  # resolved in a separate pass
        ))

    return ScanResult(
        file=file_row,
        symbols=symbol_rows,
        expressions=expression_rows,
        references=reference_rows,
        # Fix #4 / #10: surface extraction quality flags from the adapter
        parse_error_count=extraction.parse_error_count,
        expressions_truncated=extraction.expressions_truncated,
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
