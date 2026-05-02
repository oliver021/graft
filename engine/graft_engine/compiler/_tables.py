"""Stable table-alias map used throughout the compiler."""

from __future__ import annotations

import sqlalchemy as sa

from code_indexer.schema import (
    files_table,
    symbols_table,
    expressions_table,
    references_table,
)
from graft_engine.compiler._errors import CompileError


_TABLES: dict[str, sa.sql.selectable.FromClause] = {
    "symbols":     symbols_table.alias("symbols"),
    "files":       files_table.alias("files"),
    "expressions": expressions_table.alias("expressions"),
    "references":  references_table.alias("references"),
    "parent_sym":  symbols_table.alias("parent_sym"),
}


def _tbl(alias: str) -> sa.sql.selectable.FromClause:
    try:
        return _TABLES[alias]
    except KeyError:
        raise CompileError(f"Unknown table alias '{alias}' in registry definition")


def _col(alias: str, column: str) -> sa.sql.expression.ColumnElement:
    return _tbl(alias).c[column]
