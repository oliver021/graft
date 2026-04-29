"""
Lark transformer: raw parse tree -> GQL AST dataclasses.

Every method corresponds to a tree alias in grammar.lark.
No lark types leak past this module — only ast_nodes types are returned.
"""

from __future__ import annotations

from lark import Transformer, Token

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
    QueryAST,
)
from .errors import GraftParseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unescape(s: str) -> str:
    """Strip surrounding quotes and process backslash escapes."""
    quote = s[0]
    inner = s[1:-1]
    return (
        inner
        .replace(f"\\{quote}", quote)
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )


def _parse_number(s: str) -> int | float:
    return float(s) if "." in s else int(s)


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------

class GQLTransformer(Transformer):

    # ── Query ────────────────────────────────────────────────────────────

    def query(self, items):
        # Without where: [entity_path, IDENT, projection]  len=3
        # With where:    [entity_path, IDENT, condition, projection]  len=4
        entity_path = items[0]
        alias = str(items[1])
        if len(items) == 4:
            condition = items[2]
            projection = items[3]
        else:
            condition = None
            projection = items[2]
        return QueryAST(
            entity_path=entity_path,
            alias=alias,
            condition=condition,
            projection=projection,
        )

    # ── Entity path ──────────────────────────────────────────────────────

    def entity_root(self, items):
        return EntityPath(root=str(items[0]), traversals=[])

    def entity_chain(self, items):
        path: EntityPath = items[0]
        trav = items[1]
        return EntityPath(root=path.root, traversals=path.traversals + [trav])

    def field_traversal(self, items):
        return FieldTraversal(name=str(items[0]))

    def pred_noargs(self, items):
        return PredicateTraversal(name=str(items[0]), args=[])

    def pred_args(self, items):
        return PredicateTraversal(name=str(items[0]), args=items[1])

    # ── Arglist ──────────────────────────────────────────────────────────

    def arglist_single(self, items):
        return [items[0]]

    def arglist_chain(self, items):
        return items[0] + [items[1]]

    def arg_string(self, items):
        return _unescape(str(items[0]))

    def arg_number(self, items):
        return _parse_number(str(items[0]))

    def arg_null(self, items):
        return None

    # ── Condition ────────────────────────────────────────────────────────

    def cond_single(self, items):
        return Condition(expressions=[items[0]], operators=[])

    def cond_chain(self, items):
        cond: Condition = items[0]
        op = str(items[1]).lower()
        expr: ConditionExpr = items[2]
        return Condition(
            expressions=cond.expressions + [expr],
            operators=cond.operators + [op],
        )

    def binary_expr(self, items):
        field, op, value = items
        return ConditionExpr(field=field, op=str(op), value=value)

    def like_expr(self, items):
        field = items[0]
        value = _unescape(str(items[1]))
        return ConditionExpr(field=field, op="like", value=value)

    def not_expr(self, items):
        inner = items[0]
        if isinstance(inner, ConditionExpr):
            return ConditionExpr(
                field=inner.field,
                op=inner.op,
                value=inner.value,
                negated=not inner.negated,
            )
        raise GraftParseError(
            "'not' can only negate a simple comparison in v0.1"
        )

    def grouped_expr(self, items):
        cond: Condition = items[0]
        if len(cond.expressions) == 1:
            return cond.expressions[0]
        raise GraftParseError(
            "Parenthesised conditions with multiple terms are not supported in v0.1"
        )

    # ── Field ────────────────────────────────────────────────────────────

    def field_root(self, items):
        return Field(alias=str(items[0]), path=[str(items[1])])

    def field_chain(self, items):
        prev: Field = items[0]
        return Field(alias=prev.alias, path=prev.path + [str(items[1])])

    # ── Value ────────────────────────────────────────────────────────────

    def val_string(self, items):
        return _unescape(str(items[0]))

    def val_number(self, items):
        return _parse_number(str(items[0]))

    def val_null(self, items):
        return None

    def val_true(self, items):
        return True

    def val_false(self, items):
        return False

    def val_field(self, items):
        return items[0]

    # ── Projection ───────────────────────────────────────────────────────

    def proj_single(self, items):
        return Projection(items=[items[0]])

    def proj_chain(self, items):
        proj: Projection = items[0]
        item = items[1]
        return Projection(items=proj.items + [item])

    def proj_item(self, items):
        return items[0]

    def concat_single(self, items):
        return items[0]

    def concat_chain(self, items):
        return ConcatProjection(left=items[0], right=items[1])

    def field_proj(self, items):
        return FieldProjection(field=items[0])

    def literal_proj(self, items):
        return LiteralProjection(value=_unescape(str(items[0])))
