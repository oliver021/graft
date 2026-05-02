"""
GQL Executor — runs a CompiledQuery against a database engine and returns QueryResult.

Pipeline:
    CompiledQuery (select + query_sql + columns)
        ↓  run(compiled, engine)          ← this module
    QueryResult

The executor never builds SQL — it only runs what the compiler produces.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from graft_engine.compiler import CompiledQuery


# ---------------------------------------------------------------------------
# QueryResult
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    """
    Result of executing a GQL query.

    Attributes:
        columns    : column names in SELECT order
        rows       : each row as a dict keyed by column name
        query_sql  : the SQL that was executed (for transparency / debugging)
        row_count  : number of rows returned
        elapsed_ms : wall-clock milliseconds for the DB round-trip
    """
    columns:    list[str]
    rows:       list[dict[str, Any]]
    query_sql:  str
    row_count:  int
    elapsed_ms: float

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_json(self) -> str:
        """Return results as a JSON array of objects."""
        return json.dumps(self.rows, indent=2, default=str)

    def to_csv(self) -> str:
        """Return results as CSV with a header row."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=self.columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.rows)
        return buf.getvalue()

    def to_table(self) -> str:
        """
        Return results as a formatted ASCII table, suitable for CLI output.

        Example:
            name          | filename        | start
            ------------- | --------------- | -----
            my_function   | src/foo.py      | 12
        """
        if not self.rows:
            cols_line = " | ".join(self.columns)
            return f"{cols_line}\n" + "-" * len(cols_line) + "\n(0 rows)"

        # Compute column widths
        widths: dict[str, int] = {c: len(c) for c in self.columns}
        for row in self.rows:
            for col in self.columns:
                val = str(row.get(col, ""))
                widths[col] = max(widths[col], len(val))

        def _fmt_row(values: list[str]) -> str:
            return " | ".join(v.ljust(widths[col]) for col, v in zip(self.columns, values))

        header = _fmt_row(self.columns)
        separator = "-+-".join("-" * widths[c] for c in self.columns)
        data_rows = [
            _fmt_row([str(row.get(c, "")) for c in self.columns])
            for row in self.rows
        ]

        lines = [header, separator] + data_rows
        lines.append(f"({self.row_count} row{'s' if self.row_count != 1 else ''})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ExecuteError
# ---------------------------------------------------------------------------

class ExecuteError(Exception):
    """Raised when the database query fails at runtime."""


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(compiled: CompiledQuery, engine: sa.Engine) -> QueryResult:
    """
    Execute a CompiledQuery against *engine* and return a QueryResult.

    Args:
        compiled : output of graft_engine.compiler.compile()
        engine   : a SQLAlchemy Engine (create_engine(...))

    Returns:
        QueryResult

    Raises:
        ExecuteError : on database errors
    """
    t0 = time.perf_counter()
    try:
        with engine.connect() as conn:
            result = conn.execute(compiled.statement)
            raw_rows = result.fetchall()
            keys = list(result.keys())
    except sa.exc.SQLAlchemyError as e:
        raise ExecuteError(f"Query execution failed: {e}") from e

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Use compiler's column names (labels) if they match; fall back to DB keys
    columns = compiled.columns if compiled.columns else keys

    # Guard against column-count mismatch between compiler labels and DB result.
    # zip() silently truncates to the shorter side, producing subtly wrong dicts.
    if raw_rows and len(columns) != len(raw_rows[0]):
        raise ExecuteError(
            f"Column count mismatch: compiler produced {len(columns)} column name(s) "
            f"but the database returned {len(raw_rows[0])} column(s). "
            f"Compiler columns: {columns}. DB columns: {list(keys)}"
        )

    rows = [
        {col: val for col, val in zip(columns, row)}
        for row in raw_rows
    ]

    return QueryResult(
        columns=columns,
        rows=rows,
        query_sql=compiled.query_sql,
        row_count=len(rows),
        elapsed_ms=round(elapsed_ms, 2),
    )
