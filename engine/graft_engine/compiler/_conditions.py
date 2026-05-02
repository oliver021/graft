"""Compile GQL WHERE conditions to SQLAlchemy clauses."""

from __future__ import annotations

from sqlalchemy import and_, or_, not_
from sqlalchemy.sql.expression import ColumnElement

from graft_engine.entity_registry import ResolvedPath
from graft_parser.ast_nodes import Condition, ConditionExpr

from ._errors import CompileError
from ._fields import _resolve_field, _value_to_expr  # noqa: F401 (re-used by callers)


def _compile_condition_expr(
    expr: ConditionExpr,
    alias: str,
    resolved: ResolvedPath,
) -> ColumnElement:
    lhs = _resolve_field(alias, expr.field, resolved)
    rhs = _value_to_expr(expr.value, alias, resolved)

    op = expr.op.lower()

    if op == "like":
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
    if not condition.expressions:
        raise CompileError("WHERE clause contains no expressions")
    # Enforce the Condition invariant: len(operators) == len(expressions) - 1
    expected_ops = len(condition.expressions) - 1
    if len(condition.operators) != expected_ops:
        raise CompileError(
            f"Malformed WHERE clause: {len(condition.expressions)} expression(s) "
            f"but {len(condition.operators)} operator(s) "
            f"(expected {expected_ops})"
        )

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
