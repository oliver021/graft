"""Build SQLAlchemy FROM clauses from JoinDef lists."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.expression import ColumnElement

from graft_engine.entity_registry import JOIN_LEFT, JoinDef

from ._tables import _tbl


def _base_where_clauses(
    base_where: dict[str, Any],
    base_table_alias: str,
) -> list[ColumnElement]:
    """
    Translate entity.base_where to SQLAlchemy WHERE conditions.

    dict format:
      {"kind": "call"}                   → kind = 'call'
      {"kind": ["function", "method"]}   → kind IN ('function', 'method')
    """
    clauses = []
    tbl = _tbl(base_table_alias)
    for col_name, val in base_where.items():
        col = tbl.c[col_name]
        if isinstance(val, list):
            clauses.append(col.in_(val))
        else:
            clauses.append(col == val)
    return clauses


def _build_joins(
    base_alias: str,
    joins: list[JoinDef],
) -> tuple[sa.sql.selectable.FromClause, list[ColumnElement]]:
    """
    Build the SQLAlchemy FROM clause by applying JoinDefs in order.

    Returns:
        (from_clause, extra_where_clauses)

    # DECISION 1 — Join deduplication
    # When a traversal's extra_joins name a table already present in the FROM
    # (e.g. the "file.functions" traversal re-adds "symbols", which is already
    # the terminal entity's base table), both the JOIN and its extra_where are
    # silently skipped.  Emitting the JOIN would be a no-op at best; emitting
    # the extra_where would produce a contradictory WHERE condition because the
    # terminal entity's base_where already contains the equivalent filter (e.g.
    # kind IN ('function','method')).  Skipping both is the correct behaviour.
    """
    frm = _tbl(base_alias)
    seen_aliases: set[str] = {base_alias}
    where_clauses: list[ColumnElement] = []

    for jdef in joins:
        if jdef.alias in seen_aliases:
            # Already in FROM — skip join and its extra_where
            continue

        seen_aliases.add(jdef.alias)
        joined_tbl = _tbl(jdef.alias)
        left_col  = _tbl(jdef.from_table).c[jdef.from_col]
        right_col = joined_tbl.c[jdef.to_col]
        on_clause = left_col == right_col

        is_outer = jdef.kind == JOIN_LEFT
        frm = frm.join(joined_tbl, on_clause, isouter=is_outer)

        for (ew_alias, ew_col, ew_val) in jdef.extra_where:
            where_clauses.append(_tbl(ew_alias).c[ew_col] == ew_val)

    return frm, where_clauses
