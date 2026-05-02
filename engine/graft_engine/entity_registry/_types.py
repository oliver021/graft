"""
Type definitions for the entity registry: constants, dataclasses, RegistryError.

No SQL, no I/O, no DB.  Pure data contracts consumed by the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from graft_parser.ast_nodes import EntityPath, FieldTraversal, PredicateTraversal


# ---------------------------------------------------------------------------
# GQL type constants
# ---------------------------------------------------------------------------

STRING = "string"
INT    = "int"
FLOAT  = "float"
BOOL   = "bool"
JSON   = "json"

# ---------------------------------------------------------------------------
# Filter-kind constants (used in PredicateDef.filter_kind)
# ---------------------------------------------------------------------------

FILTER_WHERE      = "where"       # direct WHERE predicate on base table
FILTER_EXISTS     = "exists"      # EXISTS (subquery)
FILTER_NOT_EXISTS = "not_exists"  # NOT EXISTS (subquery)
FILTER_CTE        = "cte"         # WITH RECURSIVE CTE (callDepth)

# ---------------------------------------------------------------------------
# Join-kind constants
# ---------------------------------------------------------------------------

JOIN_INNER = "INNER"
JOIN_LEFT  = "LEFT"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDef:
    """
    Describes one GQL-visible field and its SQL source.

    Attributes:
        gql_name   : name used in GQL queries (e.g. "filename", "paramCount")
        gql_type   : one of STRING | INT | FLOAT | BOOL | JSON
        table      : SQL table alias that holds this field
        column     : column in that table (empty string = fully computed)
        json_path  : tuple of keys for JSON sub-extraction;
                     e.g. ("extra", "callee") → extra->>'callee'
        computed   : None, or a compiler-known key:
                       "param_count"   → json_array_length(signature, '$.params')
                       "caller_count"  → COUNT(*) over references
        nullable   : whether the SQL value can be NULL
    """
    gql_name:  str
    gql_type:  str
    table:     str
    column:    str
    json_path: tuple[str, ...] = ()
    computed:  str | None = None
    nullable:  bool = True

    @property
    def is_json(self) -> bool:
        return bool(self.json_path)

    @property
    def is_computed(self) -> bool:
        return self.computed is not None


@dataclass(frozen=True)
class JoinDef:
    """
    Describes one SQL JOIN the compiler must emit.

    ON clause reads: <from_table>.<from_col> = <alias>.<to_col>

    Attributes:
        table       : physical table name to join
        alias       : SQL alias to use (use unique names for self-joins)
        from_table  : alias of the table on the left side of ON
        from_col    : column on from_table for the ON clause
        to_col      : column on the joined table/alias for the ON clause
        kind        : JOIN_INNER or JOIN_LEFT
        extra_where : additional WHERE conditions applied after the join;
                      each entry is (alias, column, value)
    """
    table:       str
    alias:       str
    from_table:  str
    from_col:    str
    to_col:      str
    kind:        str = JOIN_INNER
    extra_where: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class TraversalDef:
    """
    Field traversal: moves the terminal entity along a relation.
    e.g. function.calls, class.methods, file.imports

    Attributes:
        name            : traversal name as written in GQL
        terminal_entity : name of the EntityDef produced
        extra_joins     : joins to add on top of the terminal entity's required_joins
        extra_where     : additional WHERE conditions as (alias, column, value)
    """
    name:            str
    terminal_entity: str
    extra_joins:     tuple[JoinDef, ...] = ()
    extra_where:     tuple[tuple[str, str, str], ...] = ()


@dataclass
class PredicateDef:
    """
    Predicate traversal: filters (and optionally moves the terminal entity).
    e.g. withoutArgs(), callDepth(), calls("eval"), getDoesThrow()

    Attributes:
        name             : predicate name as written in GQL
        arity            : expected arg count; None = variadic; 0 = no args
        terminal_entity  : None → keep current entity; str → move to that entity
        filter_kind      : FILTER_WHERE | FILTER_EXISTS | FILTER_NOT_EXISTS | FILTER_CTE
        filter_spec      : compiler-interpreted dict describing the filter:
                             FILTER_WHERE      → {"table": t, "column": c, "op": op, "value": v}
                               special op "param_count_zero" → json_array_length = 0
                               special op "arg_filter"       → callee name matches arg[0]
                             FILTER_EXISTS     → {"subquery": key}
                             FILTER_NOT_EXISTS → {"subquery": key}
                             FILTER_CTE        → {"cte": key}
        injected_fields  : FieldDefs added to the terminal entity by this predicate
                           (e.g. callDepth injects "depth")
    """
    name:             str
    arity:            int | None
    terminal_entity:  str | None
    filter_kind:      str
    filter_spec:      dict[str, Any]
    injected_fields:  list[FieldDef] = dc_field(default_factory=list)


@dataclass
class EntityDef:
    """
    Complete declaration of one GQL entity.

    Attributes:
        name            : GQL entity name
        is_root         : True for the 4 root entities; False for virtual (traversal-only)
        base_table      : primary SQL table alias (always the first table in FROM)
        base_where      : always-applied WHERE conditions as {column: value_or_list}
                          a list value means IN (...), a string means = '...'
        fields          : gql_name → FieldDef
        required_joins  : JOINs always needed (e.g. files for filename)
        traversals      : gql_name → TraversalDef
        predicates      : gql_name → PredicateDef
    """
    name:           str
    is_root:        bool
    base_table:     str
    base_where:     dict[str, Any]
    fields:         dict[str, FieldDef]
    required_joins: list[JoinDef]
    traversals:     dict[str, TraversalDef]
    predicates:     dict[str, PredicateDef]

    def get_field(self, gql_name: str) -> FieldDef | None:
        return self.fields.get(gql_name)


@dataclass
class PredicateApplication:
    """A predicate as applied in a query — predicate definition + user args."""
    predicate: PredicateDef
    args:      list[str | int | float | None]


@dataclass
class ResolvedPath:
    """
    Result of EntityRegistry.resolve_path().

    Contains everything the compiler needs to build a query from
    an entity_path, without any further registry lookups.
    """
    terminal:               EntityDef
    extra_joins:            list[JoinDef]
    extra_where:            list[tuple[str, str, str]]    # (alias, col, val)
    predicate_applications: list[PredicateApplication]
    injected_fields:        dict[str, FieldDef]           # gql_name → FieldDef

    def resolve_field(self, gql_name: str) -> FieldDef | None:
        """Look up a field in the terminal entity + injected fields."""
        return self.injected_fields.get(gql_name) or self.terminal.get_field(gql_name)


class RegistryError(Exception):
    """Raised when the registry cannot resolve an entity, traversal, or field."""
