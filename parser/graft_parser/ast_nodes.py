"""
AST dataclasses produced by graft_parser.parse().

These are the normative types defined in SPECS.md §8 and CLAUDE.md
"AST Node Definitions". Consumers (graft_engine) must import only
from this module — never from lark or any parser internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


# ---------------------------------------------------------------------------
# Traversals
# ---------------------------------------------------------------------------

@dataclass
class FieldTraversal:
    """A plain traversal segment with no arguments: e.g. `.calls`, `.methods`."""
    name: str


@dataclass
class PredicateTraversal:
    """
    A predicate traversal segment with parentheses: e.g. `.withoutArgs()`,
    `.calls("eval")`, `.signature(null, "str")`.

    Args are positional. None represents the `null` literal.
    """
    name: str
    args: list[str | int | float | None] = field(default_factory=list)


Traversal = Union[FieldTraversal, PredicateTraversal]


# ---------------------------------------------------------------------------
# Entity path
# ---------------------------------------------------------------------------

@dataclass
class EntityPath:
    """
    The `from` entity expression.

    root:       one of "function" | "class" | "file" | "expression"
    traversals: zero or more chained traversal steps, in source order
    """
    root: str
    traversals: list[Traversal] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fields (qualified references)
# ---------------------------------------------------------------------------

@dataclass
class Field:
    """
    A qualified field reference like `c.name` or `c.extra.callee`.

    alias: the query alias (e.g. "c", "fn", "m")
    path:  non-empty list of path segments after the alias dot
           e.g. ["name"] or ["extra", "callee"]
    """
    alias: str
    path: list[str]


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

@dataclass
class ConditionExpr:
    """
    A single boolean predicate in the WHERE clause.

    op:      "=" | "!=" | ">" | "<" | ">=" | "<=" | "like"
    value:   string, int, float, None (null), True/False, or a Field
    negated: True when the `not` keyword precedes this expression
    """
    field: Field
    op: str
    value: str | int | float | bool | Field | None
    negated: bool = False


@dataclass
class Condition:
    """
    The full WHERE clause: a flat list of ConditionExpr joined by and/or.

    Invariant: len(operators) == len(expressions) - 1
    Operators are stored lowercase: "and" | "or"
    """
    expressions: list[ConditionExpr]
    operators: list[str]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

@dataclass
class FieldProjection:
    """A single field reference in SELECT: e.g. `c.filename`."""
    field: Field


@dataclass
class LiteralProjection:
    """A string literal in SELECT (without quotes): e.g. `"prefix"`."""
    value: str


@dataclass
class ConcatProjection:
    """String concatenation in SELECT: `c.filename + ":" + c.start`."""
    left: "ProjectionItem"
    right: "ProjectionItem"


ProjectionItem = Union[FieldProjection, LiteralProjection, ConcatProjection]


@dataclass
class Projection:
    """The SELECT clause: an ordered list of projection items."""
    items: list[ProjectionItem]


# ---------------------------------------------------------------------------
# Root AST node
# ---------------------------------------------------------------------------

@dataclass
class QueryAST:
    """
    The complete parsed query.

    entity_path: the FROM clause
    alias:       the name bound to the terminal entity (from `as <alias>`)
    condition:   the WHERE clause (None if omitted)
    projection:  the SELECT clause
    """
    entity_path: EntityPath
    alias: str
    condition: Condition | None
    projection: Projection
