"""Field resolution: FieldDef → SQLAlchemy column expression."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.sql.expression import ColumnElement

from graft_engine.entity_registry import REGISTRY, FieldDef, ResolvedPath, RegistryError
from graft_parser.ast_nodes import Field

from ._errors import CompileError
from ._tables import _tbl


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
        # DECISION 2 — CTE placeholder column
        # _field_to_col() is called during projection and condition compilation,
        # before the CTE is wired in.  At that point there is no real column to
        # return.  _resolve_field() intercepts "call_depth" before reaching here
        # and returns sa.column("call_depth") — a bare unbound column reference.
        # _assemble_with_cte() then replaces every such placeholder with the
        # actual CTE column (max_call_depth.call_depth) after the CTE is built.
        # Any code path that reaches this raise() has bypassed _resolve_field()
        # and is therefore an internal compiler wiring error.
        raise CompileError(
            "call_depth field must be resolved via the CTE path; "
            "this is an internal compiler error"
        )

    if fdef.json_path:
        json_path = "$." + ".".join(fdef.json_path)
        return func.json_extract(tbl.c[fdef.column], json_path)

    return tbl.c[fdef.column]


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
