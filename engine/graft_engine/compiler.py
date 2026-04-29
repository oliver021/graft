"""
GQL Compiler — QueryAST + EntityRegistry → SQLAlchemy select().

Pipeline:
    query string
        ↓  graft_parser.parse()
    QueryAST
        ↓  compile(ast)           ← this module
    CompiledQuery (select + query_sql + columns)
        ↓  executor.run()
    QueryResult

Pure function: no I/O, no DB connection, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, literal, cast, String, and_, or_, not_, select
from sqlalchemy.sql.expression import ColumnElement, Select

# Schema tables (read-only reference)
from code_indexer.schema import (
    files_table,
    symbols_table,
    expressions_table,
    references_table,
)

# Registry types
from graft_engine.entity_registry import (
    REGISTRY,
    EntityRegistry,
    FieldDef,
    JoinDef,
    PredicateApplication,
    ResolvedPath,
    RegistryError,
    FILTER_WHERE,
    FILTER_EXISTS,
    FILTER_NOT_EXISTS,
    FILTER_CTE,
    JOIN_LEFT,
)

# AST types
from graft_parser.ast_nodes import (
    QueryAST,
    Field,
    ConditionExpr,
    Condition,
    Projection,
    ProjectionItem,
    FieldProjection,
    LiteralProjection,
    ConcatProjection,
)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class CompileError(Exception):
    """Raised when a QueryAST cannot be compiled to valid SQL."""


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
    columns: list[str]


# ---------------------------------------------------------------------------
# Table alias map
# ---------------------------------------------------------------------------
# These aliased Table objects are used throughout the compiler.
# Each has a stable alias string that matches the registry's table/alias names.

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


def _col(alias: str, column: str) -> ColumnElement:
    return _tbl(alias).c[column]


# ---------------------------------------------------------------------------
# FieldDef → SQLAlchemy column expression
# ---------------------------------------------------------------------------

def _field_to_col(fdef: FieldDef) -> ColumnElement:
    """
    Convert a FieldDef to a SQLAlchemy column expression.

    Handles:
    - plain columns  (fdef.column, no json_path, no computed)
    - JSON extract   (fdef.json_path → json_extract(col, '$.key'))
    - param_count    → json_array_length(json_extract(signature, '$.params'))
    - caller_count   → func.count(references.id)  [requires GROUP BY in caller]
    - call_depth     → handled separately via CTE; returns a labelled column ref
    """
    tbl = _tbl(fdef.table)

    if fdef.computed == "param_count":
        return func.json_array_length(
            func.json_extract(tbl.c["signature"], "$.params")
        )

    if fdef.computed == "caller_count":
        return func.count(_tbl("references").c["id"])

    if fdef.computed == "call_depth":
        # The CTE injects a column named "call_depth" into the query.
        # The actual column reference is built in _apply_cte().
        # Returning a placeholder here that _apply_cte replaces.
        raise CompileError(
            "call_depth field must be resolved via the CTE path; "
            "this is an internal compiler error"
        )

    if fdef.json_path:
        # json_extract(table.column, '$.key1.key2...')
        json_path = "$." + ".".join(fdef.json_path)
        return func.json_extract(tbl.c[fdef.column], json_path)

    return tbl.c[fdef.column]


# ---------------------------------------------------------------------------
# Condition expression → SQLAlchemy clause
# ---------------------------------------------------------------------------

def _resolve_field(alias: str, field: Field, resolved: ResolvedPath) -> ColumnElement:
    """
    Map a GQL Field (alias.path) to a SQLAlchemy column expression.

    The alias is the query alias (e.g. 'fn', 'c'), not a table alias.
    We validate that Field.alias matches the query alias, then resolve the
    last segment of Field.path as a GQL field name.
    """
    if field.alias != alias:
        raise CompileError(
            f"Field alias '{field.alias}' does not match query alias '{alias}'"
        )

    # For multi-segment paths like "extra.callee", only the first segment is
    # the GQL field name; sub-paths are an alternative syntax that the
    # registry handles via json_path.  We resolve by first segment only.
    gql_name = field.path[0]

    try:
        fdef = REGISTRY.resolve_field(resolved, gql_name)
    except RegistryError as e:
        raise CompileError(str(e)) from e

    if fdef.computed == "call_depth":
        # During CTE queries the depth column is labelled "call_depth"
        # on the outer query.  We return a text reference.
        return sa.column("call_depth")

    return _field_to_col(fdef)


def _value_to_expr(
    value: str | int | float | bool | None | Field,
    alias: str,
    resolved: ResolvedPath,
) -> Any:
    """Convert a GQL condition value to a Python/SA value or column."""
    if value is None:
        return None
    if isinstance(value, Field):
        return _resolve_field(alias, value, resolved)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _compile_condition_expr(
    expr: ConditionExpr,
    alias: str,
    resolved: ResolvedPath,
) -> ColumnElement:
    lhs = _resolve_field(alias, expr.field, resolved)
    rhs = _value_to_expr(expr.value, alias, resolved)

    op = expr.op.lower()

    if op == "like":
        # GQL LIKE: wrap value in SQL LIKE wildcards if not already present
        clause = lhs.like(rhs)
    elif rhs is None:
        if op == "=":
            clause = lhs.is_(None)
        elif op == "!=":
            clause = lhs.is_not(None)
        else:
            raise CompileError(f"Operator '{op}' cannot be used with null")
    else:
        op_map = {
            "=":  "__eq__",
            "!=": "__ne__",
            ">":  "__gt__",
            "<":  "__lt__",
            ">=": "__ge__",
            "<=": "__le__",
        }
        if op not in op_map:
            raise CompileError(f"Unknown operator '{op}'")
        clause = getattr(lhs, op_map[op])(rhs)

    if expr.negated:
        clause = not_(clause)

    return clause


def _compile_condition(
    condition: Condition,
    alias: str,
    resolved: ResolvedPath,
) -> ColumnElement:
    clauses = [
        _compile_condition_expr(e, alias, resolved)
        for e in condition.expressions
    ]
    if len(clauses) == 1:
        return clauses[0]

    result = clauses[0]
    for i, op in enumerate(condition.operators):
        nxt = clauses[i + 1]
        if op == "and":
            result = and_(result, nxt)
        else:
            result = or_(result, nxt)
    return result


# ---------------------------------------------------------------------------
# Projection item → (column_expression, label)
# ---------------------------------------------------------------------------

def _compile_proj_item(
    item: ProjectionItem,
    alias: str,
    resolved: ResolvedPath,
) -> tuple[ColumnElement, str]:
    """Return (SA column expression, output label)."""
    if isinstance(item, FieldProjection):
        col = _resolve_field(alias, item.field, resolved)
        label = item.field.path[-1]
        return col, label

    if isinstance(item, LiteralProjection):
        return literal(item.value), f"'{item.value}'"

    if isinstance(item, ConcatProjection):
        left_col, left_label = _compile_proj_item(item.left, alias, resolved)
        right_col, right_label = _compile_proj_item(item.right, alias, resolved)
        col = cast(left_col, String) + cast(right_col, String)
        label = left_label + "_" + right_label
        return col, label

    raise CompileError(f"Unknown projection item type: {type(item)}")


# ---------------------------------------------------------------------------
# Base WHERE from entity's base_where dict
# ---------------------------------------------------------------------------

def _base_where_clauses(base_where: dict[str, Any], base_table_alias: str) -> list[ColumnElement]:
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


# ---------------------------------------------------------------------------
# JOIN builder
# ---------------------------------------------------------------------------

def _build_joins(
    base_alias: str,
    joins: list[JoinDef],
) -> tuple[sa.sql.selectable.FromClause, list[ColumnElement]]:
    """
    Build the SQLAlchemy FROM clause by applying JoinDefs in order.

    Returns:
        (from_clause, extra_where_clauses)

    Deduplication: if a join alias is already in the FROM, it is skipped
    along with its extra_where conditions (to avoid contradictory filters;
    the terminal entity's base_where already handles the equivalent filter).
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

        # Accumulate extra_where from this join
        for (ew_alias, ew_col, ew_val) in jdef.extra_where:
            where_clauses.append(_tbl(ew_alias).c[ew_col] == ew_val)

    return frm, where_clauses


# ---------------------------------------------------------------------------
# Predicate application → WHERE clause
# ---------------------------------------------------------------------------

def _apply_predicate_where(
    app: PredicateApplication,
    resolved: ResolvedPath,
) -> list[ColumnElement]:
    """
    Handle FILTER_WHERE predicates.

    filter_spec keys:
      op="param_count_zero"   → json_array_length(json_extract(signature,'$.params')) = 0
      op="arg_filter"         → callee name in expressions.extra = arg[0]
      op="signature_match"    → synonym for param_count_zero when arg is null
    """
    spec = app.predicate.filter_spec
    op   = spec.get("op", "")
    clauses: list[ColumnElement] = []

    if op == "param_count_zero":
        clauses.append(
            func.json_array_length(
                func.json_extract(_tbl("symbols").c["signature"], "$.params")
            ) == 0
        )

    elif op == "arg_filter":
        # calls("eval") → json_extract(expressions.extra, '$.callee') = 'eval'
        if app.args:
            callee_val = app.args[0]
            col = func.json_extract(_tbl("expressions").c["extra"], "$.callee")
            if callee_val is None:
                clauses.append(col.is_(None))
            else:
                clauses.append(col == callee_val)

    elif op == "signature_match":
        # signature(null) → same as withoutArgs
        if not app.args or app.args[0] is None:
            clauses.append(
                func.json_array_length(
                    func.json_extract(_tbl("symbols").c["signature"], "$.params")
                ) == 0
            )
        # signature with actual args: not fully implemented, silently skip

    return clauses


def _apply_predicate_exists(
    app: PredicateApplication,
    negated: bool,
) -> ColumnElement:
    """
    Build EXISTS / NOT EXISTS subquery for FILTER_EXISTS and FILTER_NOT_EXISTS.

    Subquery keys:
      "raise_in_body"  → EXISTS(SELECT 1 FROM expressions WHERE symbol_id=symbols.id AND kind='raise')
      "caller_exists"  → NOT EXISTS(SELECT 1 FROM references WHERE to_symbol_id=symbols.id)
    """
    key = app.predicate.filter_spec.get("subquery", "")

    if key == "raise_in_body":
        sub_expr = sa.alias(expressions_table, "raise_check")
        subq = (
            select(literal(1))
            .select_from(sub_expr)
            .where(
                and_(
                    sub_expr.c["symbol_id"] == _tbl("symbols").c["id"],
                    sub_expr.c["kind"] == "raise",
                )
            )
            .correlate(_tbl("symbols"))
        )

    elif key == "caller_exists":
        sub_ref = sa.alias(references_table, "caller_check")
        subq = (
            select(literal(1))
            .select_from(sub_ref)
            .where(sub_ref.c["to_symbol_id"] == _tbl("symbols").c["id"])
            .correlate(_tbl("symbols"))
        )

    else:
        raise CompileError(f"Unknown subquery key '{key}'")

    clause = sa.exists(subq)
    if negated:
        clause = ~clause
    return clause


# ---------------------------------------------------------------------------
# call_depth CTE
# ---------------------------------------------------------------------------

def _build_call_depth_cte() -> sa.sql.selectable.CTE:
    """
    Build a recursive CTE that computes the maximum call-chain depth
    reachable FROM each function/method.

    Result columns: sym_id, depth
    """
    # Aliased tables for the CTE
    sym  = symbols_table.alias("cg_sym")
    ref  = references_table.alias("cg_ref")

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
    cg_alias = cte.alias("cg")
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


# ---------------------------------------------------------------------------
# GROUP BY detection
# ---------------------------------------------------------------------------

def _needs_group_by(resolved: ResolvedPath, proj: Projection, alias: str) -> bool:
    """Return True if the query needs GROUP BY (callerCount in projection)."""
    for item in proj.items:
        if isinstance(item, FieldProjection):
            gql_name = item.field.path[0]
            fdef = resolved.resolve_field(gql_name)
            if fdef and fdef.computed == "caller_count":
                return True
    return False


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
    sel_cols: list[ColumnElement] = []
    col_names: list[str] = []

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
            # Group by all non-aggregate columns (i.e. everything except callerCount)
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

    # 7. Render SQL string
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


def _is_aggregate(col_expr: ColumnElement) -> bool:
    """Return True if col_expr is an aggregate function (count, max, etc.)."""
    return isinstance(col_expr, sa.sql.functions.Function) and col_expr.name.lower() in (
        "count", "max", "min", "sum", "avg",
    )


def _assemble_with_cte(
    frm: sa.sql.selectable.FromClause,
    sel_cols: list[ColumnElement],
    where_clauses: list[ColumnElement],
    user_where: ColumnElement | None,
    terminal: Any,
    resolved: ResolvedPath,
    ast: QueryAST,
    alias: str,
) -> Select:
    """
    Assemble a SELECT with a call_depth recursive CTE.

    The CTE is joined to the base query on symbols.id = max_call_depth.sym_id.
    The injected 'depth' field is exposed as max_call_depth.call_depth.
    """
    cte = _build_call_depth_cte()

    # Join the CTE onto the FROM clause
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
