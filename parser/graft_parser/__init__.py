"""
graft_parser — pure GQL parser.

Public API:
    parse(query: str) -> QueryAST
    GraftParseError

All AST dataclasses are re-exported for consumer convenience.
"""

from .ast_nodes import (
    ConcatProjection,
    Condition,
    ConditionExpr,
    EntityPath,
    Field,
    FieldProjection,
    FieldTraversal,
    LiteralProjection,
    PredicateTraversal,
    Projection,
    ProjectionItem,
    QueryAST,
    Traversal,
)
from .errors import GraftParseError
from ._parser import parse

__all__ = [
    "parse",
    "GraftParseError",
    "QueryAST",
    "EntityPath",
    "FieldTraversal",
    "PredicateTraversal",
    "Traversal",
    "Field",
    "Condition",
    "ConditionExpr",
    "Projection",
    "ProjectionItem",
    "FieldProjection",
    "LiteralProjection",
    "ConcatProjection",
]
