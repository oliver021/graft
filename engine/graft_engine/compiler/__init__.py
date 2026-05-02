"""
graft_engine.compiler — QueryAST + EntityRegistry → SQLAlchemy select().

Pipeline:
    query string
        ↓  graft_parser.parse()
    QueryAST
        ↓  compile(ast)           ← this package
    CompiledQuery (select + query_sql + columns)
        ↓  executor.run()
    QueryResult

Pure function: no I/O, no DB connection, no side effects.

Public API (unchanged from the previous single-file module):
    compile(ast, registry) -> CompiledQuery
    CompiledQuery
    CompileError
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import and_, select
from sqlalchemy.sql.expression import ColumnElement, Select

from graft_engine.entity_registry import (
    REGISTRY,
    EntityRegistry,
    JoinDef,
    FILTER_WHERE,
    FILTER_EXISTS,
    FILTER_NOT_EXISTS,
    FILTER_CTE,
)
from graft_parser.ast_nodes import QueryAST

from ._errors import CompileError
from ._assemble import _apply_order_limit, _assemble_with_cte, _is_aggregate, _needs_group_by
from ._conditions import _compile_condition
from ._joins import _base_where_clauses, _build_joins
from ._predicates import _apply_predicate_exists, _apply_predicate_where
from ._projection import _compile_proj_item
from ._tables import _tbl


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class CompiledQuery:
    """
    Result of compile().

    statement  : executable SQLAlchemy select() object
    query_sql  : human-readable SQL string (literal_binds=True)
    columns    : output column names, in SELECT order
    """
    statement: Select
    query_sql: str
    columns:   list[str]


# ---------------------------------------------------------------------------
# Main compile() entry point
# ---------------------------------------------------------------------------

def compile(ast: QueryAST, registry: EntityRegistry = REGISTRY) -> CompiledQuery:  # noqa: A001
    """
    Compile a QueryAST to a SQLAlchemy select() statement.

    Args:
        ast      : parsed query (from graft_parser.parse())
        registry : entity registry (default: module-level REGISTRY singleton)

    Returns:
        CompiledQuery with .statement, .query_sql, .columns

    Raises:
        CompileError : on semantic errors (unknown field, bad type, etc.)
        RegistryError: propagated from registry on unknown entity/traversal
    """
    from graft_engine.entity_registry import RegistryError

    alias = ast.alias

    # 1. Resolve entity path
    try:
        resolved = registry.resolve_path(ast.entity_path)
    except RegistryError as e:
        raise CompileError(str(e)) from e

    terminal = resolved.terminal

    # 2. Detect special modes
    has_cte = any(
        pa.predicate.filter_kind == FILTER_CTE
        for pa in resolved.predicate_applications
    )

    # 3. Build the join list: terminal required_joins + traversal extra_joins
    all_joins: list[JoinDef] = list(terminal.required_joins) + list(resolved.extra_joins)

    frm, join_where = _build_joins(terminal.base_table, all_joins)

    # 4. Collect WHERE clauses
    where_clauses: list[ColumnElement] = []

    # 4a. Base WHERE from terminal entity
    where_clauses.extend(_base_where_clauses(terminal.base_where, terminal.base_table))

    # 4b. JOIN extra_where (already collected by _build_joins)
    where_clauses.extend(join_where)

    # 4c. TraversalDef extra_where
    for (ew_alias, ew_col, ew_val) in resolved.extra_where:
        where_clauses.append(_tbl(ew_alias).c[ew_col] == ew_val)

    # 4d. Predicate applications
    for app in resolved.predicate_applications:
        fk = app.predicate.filter_kind
        if fk == FILTER_WHERE:
            where_clauses.extend(_apply_predicate_where(app, resolved))
        elif fk == FILTER_EXISTS:
            where_clauses.append(_apply_predicate_exists(app, negated=False))
        elif fk == FILTER_NOT_EXISTS:
            where_clauses.append(_apply_predicate_exists(app, negated=True))
        elif fk == FILTER_CTE:
            pass  # handled below
        else:
            raise CompileError(f"Unknown filter_kind '{fk}'")

    # 4e. User condition (WHERE clause from query)
    user_where: ColumnElement | None = None
    if ast.condition is not None:
        user_where = _compile_condition(ast.condition, alias, resolved)

    # 5. Build SELECT columns from projection
    sel_cols:  list[ColumnElement] = []
    col_names: list[str]           = []

    for item in ast.projection.items:
        col_expr, label = _compile_proj_item(item, alias, resolved)
        sel_cols.append(col_expr.label(label))
        col_names.append(label)

    # 6. Assemble the query
    if has_cte:
        stmt = _assemble_with_cte(
            frm=frm,
            sel_cols=sel_cols,
            where_clauses=where_clauses,
            user_where=user_where,
            terminal=terminal,
            resolved=resolved,
            ast=ast,
            alias=alias,
        )
    else:
        stmt = select(*sel_cols).select_from(frm)
        if where_clauses:
            stmt = stmt.where(and_(*where_clauses))
        if user_where is not None:
            stmt = stmt.where(user_where)

        # GROUP BY for callerCount
        if _needs_group_by(resolved, ast.projection, alias):
            group_cols = [
                col_expr
                for col_expr, label in (
                    _compile_proj_item(item, alias, resolved)
                    for item in ast.projection.items
                )
                if not _is_aggregate(col_expr)
            ]
            if group_cols:
                stmt = stmt.group_by(*group_cols)

    # 7. ORDER BY + LIMIT (applied to both CTE and non-CTE paths)
    stmt = _apply_order_limit(stmt, ast.order_by, ast.limit, alias, resolved)

    # 8. Render SQL string
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    query_sql = str(
        stmt.compile(
            dialect=sqlite_dialect.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    return CompiledQuery(
        statement=stmt,
        query_sql=query_sql,
        columns=col_names,
    )


__all__ = ["compile", "CompiledQuery", "CompileError"]
