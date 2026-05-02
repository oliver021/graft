"""
Pure data models. No DB, no I/O, no side effects.

ScanResult is the return type of scan() and the central data container.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy import insert

from code_indexer.schema import (
    expressions_table,
    files_table,
    references_table,
    symbols_table,
)

# ---------------------------------------------------------------------------
# Row-level dataclasses  (mirror the schema columns)
# ---------------------------------------------------------------------------

@dataclass
class FileRow:
    id: str
    path: str
    language: str
    content_hash: str
    scanned_at: str


@dataclass
class SymbolRow:
    id: str
    file_id: str
    name: str
    kind: str                        # function | method | class | lambda
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    signature: dict[str, Any] | None = None
    parent_id: str | None = None


@dataclass
class ExpressionRow:
    id: str
    file_id: str
    kind: str
    source_text: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    depth: int = 0
    symbol_id: str | None = None
    parent_id: str | None = None
    extra: dict[str, Any] | None = None


@dataclass
class ReferenceRow:
    id: str
    file_id: str
    expression_id: str
    callee_name: str
    from_symbol_id: str | None = None
    to_symbol_id: str | None = None   # always None until resolution pass


# ---------------------------------------------------------------------------
# ScanResult — the public return type of scan()
# ---------------------------------------------------------------------------

_DIALECT_MAP = {
    "sqlite": sqlite.dialect(),
    "postgresql": postgresql.dialect(),
    "postgres": postgresql.dialect(),
    "mysql": mysql.dialect(),
}


@dataclass
class ScanResult:
    """
    Pure, immutable result of a scan() call.

    Contains all extracted rows ready to be serialized to SQL, dicts, or
    inserted directly via a SQLAlchemy engine.
    """

    file: FileRow
    symbols: list[SymbolRow] = field(default_factory=list)
    expressions: list[ExpressionRow] = field(default_factory=list)
    references: list[ReferenceRow] = field(default_factory=list)
    # Fix #4: number of ERROR nodes found during parsing (0 = clean parse)
    parse_error_count: int = 0
    # Fix #10: True when extraction stopped early due to MAX_EXPRESSIONS_PER_FILE
    expressions_truncated: bool = False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return all rows as plain Python dicts, keyed by table name."""
        return {
            "files": [self.file.__dict__],
            "symbols": [s.__dict__ for s in self.symbols],
            "expressions": [e.__dict__ for e in self.expressions],
            "references": [r.__dict__ for r in self.references],
        }

    def to_sql(self, dialect: str = "sqlite") -> str:
        """
        Return a string of SQL INSERT statements for the given dialect.

        The output is a complete, self-contained SQL script that can be
        executed directly against a fresh database (after schema creation).

        Args:
            dialect: one of "sqlite", "postgresql", "postgres", "mysql"

        Returns:
            Multi-line SQL string with one INSERT per row.
        """
        if dialect not in _DIALECT_MAP:
            raise ValueError(
                f"Unsupported dialect '{dialect}'. "
                f"Choose from: {list(_DIALECT_MAP.keys())}"
            )
        sqlalchemy_dialect = _DIALECT_MAP[dialect]
        statements: list[str] = []

        # SQLAlchemy's literal_binds cannot render JSON column values.
        # We build INSERT statements as raw SQL strings, quoting values
        # ourselves. This is safe because all values come from our own
        # structured extraction (not user-supplied arbitrary SQL).
        json_columns = {"signature", "extra"}

        def _sql_literal(col_name: str, value: Any) -> str:
            """Render a single column value as a SQL literal."""
            if value is None:
                return "NULL"
            if col_name in json_columns:
                # Serialize to JSON string, then SQL-quote it
                serialized = json.dumps(value, ensure_ascii=False)
                return "'" + serialized.replace("'", "''") + "'"
            if isinstance(value, bool):
                return "1" if value else "0"
            if isinstance(value, int):
                return str(value)
            if isinstance(value, float):
                return repr(value)
            # String: SQL-quote by doubling single quotes
            return "'" + str(value).replace("'", "''") + "'"

        def _compile(table, rows: list[dict[str, Any]]) -> None:
            table_name = table.name
            col_names = [c.name for c in table.columns]
            for row in rows:
                cols = ", ".join(col_names)
                vals = ", ".join(_sql_literal(c, row.get(c)) for c in col_names)
                statements.append(f'INSERT INTO "{table_name}" ({cols}) VALUES ({vals});')

        _compile(files_table, [self.file.__dict__])
        _compile(symbols_table, [s.__dict__ for s in self.symbols])
        _compile(expressions_table, [e.__dict__ for e in self.expressions])
        _compile(references_table, [r.__dict__ for r in self.references])

        return "\n".join(statements)

    def to_json(self, indent: int = 2) -> str:
        """Return all rows as a JSON string. Useful for debugging."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # ------------------------------------------------------------------
    # Direct DB insertion (requires SQLAlchemy engine)
    # ------------------------------------------------------------------

    def insert_into(self, engine: Any, *, replace: bool = False) -> None:
        """
        Insert all rows into the database using the provided SQLAlchemy engine.

        This is a convenience method — the library has no DB dependency
        itself, but callers that already have an engine can use this.

        Args:
            engine:  SQLAlchemy engine (sync).
            replace: When True, delete any existing rows for this file_id before
                     inserting — safe re-indexing without PK collision (fix #8).
                     When False (default), raises IntegrityError if the file was
                     already indexed with the same content hash.
        """
        from sqlalchemy import Connection, delete, select  # local import keeps DB optional

        with engine.begin() as conn:
            conn: Connection

            # Fix #8: remove stale rows so re-indexing never hits a PK collision.
            # We delete by PATH (not file_id) so that a re-index after content
            # changes (new hash → new UUID) also removes the old file row.
            # CASCADE on FK columns handles children in PostgreSQL; we delete
            # children explicitly first for SQLite where FK enforcement is off.
            if replace:
                old_ids_q = select(files_table.c.id).where(
                    files_table.c.path == self.file.path
                )
                conn.execute(references_table.delete().where(
                    references_table.c.file_id.in_(old_ids_q)
                ))
                conn.execute(expressions_table.delete().where(
                    expressions_table.c.file_id.in_(old_ids_q)
                ))
                conn.execute(symbols_table.delete().where(
                    symbols_table.c.file_id.in_(old_ids_q)
                ))
                conn.execute(files_table.delete().where(
                    files_table.c.path == self.file.path
                ))

            conn.execute(
                files_table.insert(),
                [self.file.__dict__],
            )
            if self.symbols:
                conn.execute(
                    symbols_table.insert(),
                    [s.__dict__ for s in self.symbols],
                )
            if self.expressions:
                conn.execute(
                    expressions_table.insert(),
                    [e.__dict__ for e in self.expressions],
                )
            if self.references:
                conn.execute(
                    references_table.insert(),
                    [r.__dict__ for r in self.references],
                )

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def functions(self) -> list[SymbolRow]:
        return [s for s in self.symbols if s.kind in ("function", "method")]

    def calls(self) -> list[ExpressionRow]:
        return [e for e in self.expressions if e.kind == "call"]

    def __repr__(self) -> str:
        return (
            f"ScanResult(file={self.file.path!r}, "
            f"symbols={len(self.symbols)}, "
            f"expressions={len(self.expressions)}, "
            f"references={len(self.references)})"
        )
