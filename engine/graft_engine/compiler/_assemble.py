"""Final statement assembly helpers: GROUP BY, ORDER BY, LIMIT, CTE wiring."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import and_, select
from sqlalchemy.sql.expression import ColumnElement, Select

from graft_engine.entity_registry import ResolvedPath
from graft_parser.ast_nodes import OrderBy, Projection

from ._cte import _build_call_depth_cte
from ._fields import _resolve_field
from ._projection import _compile_proj_item
from ._tables import _tbl


def _is_aggregate(col_expr: ColumnElement) -> bool:
    """Return True if col_expr is an aggregate function (count, max, etc.)."""
    return isinstance(col_expr, sa.sql.functions.Function) and col_expr.name.lower() in (
        "count", "max", "min", "sum", "avg",
    )


def _needs_group_by(resolved: ResolvedPath, proj: Projection, alias: str) -> bool:
    """
    Return True if the query needs GROUP BY (callerCount in projection).

    # DECISION 3 — GROUP BY auto-injection
    # callerCount compiles to func.count(references.id), which is an aggregate.
    # SQLAlchemy / SQL requires that every non-aggregate SELECT column appears
    # in GROUP BY.  Rather than requiring users to write GROUP BY in GQL (which
    # does not exist yet), the compiler detects the aggregate field and injects
    # GROUP BY automatically over all non-aggregate projection columns.
    # Limitation: this only works correctly when callerCount is the SOLE
    # aggregate in the projection.  A second aggregate (e.g. max(paramCount))
    # would require explicit GROUP BY support in GQL before it can be handled.
    """
    from graft_parser.ast_nodes import FieldProjection
    for item in proj.items:
        if isinstance(item, FieldProjection):
            gql_name = item.field.path[0]
            fdef = resolved.resolve_field(gql_name)
            if fdef and fdef.computed == "caller_count":
                return True
    return False


def _apply_order_limit(
    stmt: Select,
    order_by: OrderBy | None,
    limit: int | None,
    alias: str,
    resolved: ResolvedPath,
) -> Select:
    """Append ORDER BY and/or LIMIT clauses to a compiled statement."""
    from graft_engine.compiler._errors import CompileError
    if order_by is not None:
        order_cols = []
        for item in order_by.items:
            col = _resolve_field(alias, item.field, resolved)
            if item.direction == "asc":
                order_cols.append(col.asc())
            elif item.direction == "desc":
                order_cols.append(col.desc())
            else:
                raise CompileError(
                    f"Invalid ORDER BY direction '{item.direction}': must be 'asc' or 'desc'"
                )
        stmt = stmt.order_by(*order_cols)
    if limit is not None:
        if limit < 1:
            raise CompileError(
                f"LIMIT must be a positive integer (≥ 1), got {limit}"
            )
        stmt = stmt.limit(limit)
    return stmt


def _assemble_with_cte(
    frm: sa.sql.selectable.FromClause,
    sel_cols: list[ColumnElement],
    where_clauses: list[ColumnElement],
    user_where: ColumnElement | None,
    terminal: Any,
    resolved: ResolvedPath,
    ast: Any,
    alias: str,
) -> Select:
    """
    Assemble a SELECT with a call_depth recursive CTE.

    The CTE is joined to the base query on symbols.id = max_call_depth.sym_id.
    The injected 'depth' field is exposed as max_call_depth.call_depth.
    """
    cte = _build_call_depth_cte()

    frm = frm.join(cte, _tbl("symbols").c["id"] == cte.c["sym_id"], isouter=True)

    # Replace any sa.column("call_depth") placeholders in sel_cols with the real CTE column
    final_cols = []
    for col in sel_cols:
        if isinstance(col, sa.sql.elements.Label):
            inner = col.element
            if isinstance(inner, sa.sql.elements.ColumnClause) and inner.key == "call_depth":
                final_cols.append(cte.c["call_depth"].label(col.key))
                continue
        final_cols.append(col)

    stmt = select(*final_cols).select_from(frm)

    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    if user_where is not None:
        stmt = stmt.where(user_where)

    return stmt
