"""Build the call_depth recursive CTE."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import func, literal, select

from code_indexer.schema import references_table, symbols_table


def _build_call_depth_cte() -> sa.sql.selectable.CTE:
    """
    Build a recursive CTE that computes the maximum call-chain depth
    reachable FROM each function/method.

    Result columns: sym_id, call_depth
    """
    sym = symbols_table.alias("cg_sym")

    # Base: every function/method starts at depth 0
    base = (
        select(
            sym.c["id"].label("sym_id"),
            literal(0).label("depth"),
        )
        .select_from(sym)
        .where(sym.c["kind"].in_(["function", "method"]))
    )

    cte = base.cte(recursive=True, name="call_graph")

    # Recursive: follow outgoing references (from_symbol_id), depth+1
    cg_alias  = cte.alias("cg")
    ref_alias = references_table.alias("cg_ref_r")
    recursive_part = (
        select(
            cg_alias.c["sym_id"],
            (cg_alias.c["depth"] + 1).label("depth"),
        )
        .select_from(cg_alias)
        .join(ref_alias, ref_alias.c["from_symbol_id"] == cg_alias.c["sym_id"])
        .where(cg_alias.c["depth"] < 50)   # safety cap
    )

    cte = cte.union_all(recursive_part)

    # Collapse to max depth per symbol
    max_depth_q = (
        select(
            cte.c["sym_id"],
            func.max(cte.c["depth"]).label("call_depth"),
        )
        .group_by(cte.c["sym_id"])
        .cte(name="max_call_depth")
    )

    return max_depth_q
