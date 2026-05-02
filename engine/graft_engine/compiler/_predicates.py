"""Apply predicate filters (WHERE / EXISTS / NOT EXISTS) to the query."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import and_, func, literal, select
from sqlalchemy.sql.expression import ColumnElement

from code_indexer.schema import expressions_table, references_table
from graft_engine.entity_registry import (
    FILTER_EXISTS,
    FILTER_NOT_EXISTS,
    FILTER_WHERE,
    PredicateApplication,
    ResolvedPath,
)

from ._errors import CompileError
from ._tables import _tbl


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
        if not app.args:
            # arity=1 in the registry should prevent this, but guard defensively
            raise CompileError(
                "arg_filter predicate requires exactly one argument (callee name)"
            )
        callee_val = app.args[0]
        col = func.json_extract(_tbl("expressions").c["extra"], "$.callee")
        if callee_val is None:
            clauses.append(col.is_(None))
        else:
            clauses.append(col == callee_val)

    elif op == "signature_match":
        if not app.args or app.args[0] is None:
            clauses.append(
                func.json_array_length(
                    func.json_extract(_tbl("symbols").c["signature"], "$.params")
                ) == 0
            )
        else:
            # Type-specific signature filtering (e.g. signature("str", "int"))
            # requires type-inference information not available in v0.1.
            raise CompileError(
                "signature() with typed arguments is not yet supported. "
                "Use signature(null) or withoutArgs() to match zero-parameter functions."
            )

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
