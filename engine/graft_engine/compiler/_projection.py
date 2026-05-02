"""Compile GQL SELECT projection items to (ColumnElement, label) pairs."""

from __future__ import annotations

from sqlalchemy import cast, literal, String
from sqlalchemy.sql.expression import ColumnElement

from graft_engine.entity_registry import ResolvedPath
from graft_parser.ast_nodes import (
    ConcatProjection,
    FieldProjection,
    LiteralProjection,
    ProjectionItem,
)

from ._errors import CompileError
from ._fields import _resolve_field


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
        left_col,  left_label  = _compile_proj_item(item.left,  alias, resolved)
        right_col, right_label = _compile_proj_item(item.right, alias, resolved)
        col = cast(left_col, String) + cast(right_col, String)
        label = left_label + "_" + right_label
        return col, label

    raise CompileError(f"Unknown projection item type: {type(item)}")
