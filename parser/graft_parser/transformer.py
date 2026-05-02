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
    OrderBy,
    OrderByItem,
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
        # items[0] is always EntityPath, items[1] is always the alias IDENT.
        # Remaining items vary by which of the 8 grammar alternatives matched;
        # we identify each by type rather than position.
        entity_path = items[0]
        alias = str(items[1])
        condition  = None
        projection = None
        order_by   = None
        limit      = None
        for item in items[2:]:
            if isinstance(item, Condition):
                condition = item
            elif isinstance(item, Projection):
                projection = item
            elif isinstance(item, OrderBy):
                order_by = item
            elif isinstance(item, int):
                limit = item
        if projection is None:
            # Should be unreachable: every grammar alternative ends with `projection`.
            # Guard against future grammar changes producing unexpected tree shapes.
            raise GraftParseError.internal(
                "SELECT projection was not found in the parse tree"
            )
        return QueryAST(
            entity_path=entity_path,
            alias=alias,
            condition=condition,
            projection=projection,
            order_by=order_by,
            limit=limit,
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
        raise GraftParseError.unsupported(
            "Negating a compound condition with 'not'",
            hint=(
                "'not' can only negate a single comparison, e.g.  not fn.name = 'foo'. "
                "For compound negation rewrite each term: "
                "not fn.a = 'x' and not fn.b = 'y'"
            ),
        )

    def grouped_expr(self, items):
        cond: Condition = items[0]
        if len(cond.expressions) == 1:
            return cond.expressions[0]
        raise GraftParseError.unsupported(
            "Parenthesised multi-term conditions",
            hint=(
                "Parentheses around multiple conditions are not yet supported. "
                "Rewrite the condition at the top level using 'and' / 'or', e.g.: "
                "where fn.a = 'x' and fn.b = 'y'"
            ),
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

    # ── ORDER BY ─────────────────────────────────────────────────────────

    def order_by(self, items):
        return OrderBy(items=items[0])

    def orderitems_single(self, items):
        return [items[0]]

    def orderitems_chain(self, items):
        return items[0] + [items[1]]

    def order_item_default(self, items):
        return OrderByItem(field=items[0], direction="asc")

    def order_item_dir(self, items):
        return OrderByItem(field=items[0], direction=str(items[1]).lower())

    # ── LIMIT ────────────────────────────────────────────────────────────

    def limit_clause(self, items):
        return int(str(items[0]))
