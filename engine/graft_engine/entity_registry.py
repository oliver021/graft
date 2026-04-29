"""
Entity Registry — the semantic layer between GQL and SQL.

This module is **pure data**: no SQLAlchemy execution, no DB connection,
no I/O. It maps GQL concepts (entities, fields, traversals, predicates)
to SQL facts (tables, columns, joins, filters) that the compiler turns
into actual SQLAlchemy select() calls.

Dependency rule: graft_engine → graft_parser + graft_indexer schema (read only).
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
    terminal:              EntityDef
    extra_joins:           list[JoinDef]
    extra_where:           list[tuple[str, str, str]]    # (alias, col, val)
    predicate_applications: list[PredicateApplication]
    injected_fields:       dict[str, FieldDef]           # gql_name → FieldDef

    def resolve_field(self, gql_name: str) -> FieldDef | None:
        """Look up a field in the terminal entity + injected fields."""
        return self.injected_fields.get(gql_name) or self.terminal.get_field(gql_name)


class RegistryError(Exception):
    """Raised when the registry cannot resolve an entity, traversal, or field."""


# ---------------------------------------------------------------------------
# Entity definitions
# ---------------------------------------------------------------------------
# All table references match code_indexer/schema.py.
# Aliases:
#   "symbols"    → symbols_table
#   "files"      → files_table
#   "expressions"→ expressions_table
#   "references" → references_table
#   "parent_sym" → symbols_table self-join (for method.className)
# ---------------------------------------------------------------------------


# ── Shared JoinDefs ─────────────────────────────────────────────────────────

_JOIN_FILES_FROM_SYMBOLS = JoinDef(
    table="files", alias="files",
    from_table="symbols", from_col="file_id", to_col="id",
)

_JOIN_FILES_FROM_EXPRESSIONS = JoinDef(
    table="files", alias="files",
    from_table="expressions", from_col="file_id", to_col="id",
)

_JOIN_SYMBOLS_FROM_EXPRESSIONS = JoinDef(
    table="symbols", alias="symbols",
    from_table="expressions", from_col="symbol_id", to_col="id",
)

_JOIN_EXPRESSIONS_FROM_SYMBOLS = JoinDef(
    table="expressions", alias="expressions",
    from_table="symbols", from_col="id", to_col="symbol_id",
    extra_where=(("expressions", "kind", "call"),),
)

_JOIN_PARENT_SYM = JoinDef(
    table="symbols", alias="parent_sym",
    from_table="symbols", from_col="parent_id", to_col="id",
    kind=JOIN_LEFT,
)

_JOIN_FILES_FROM_SYMBOLS_VIA_EXPR = JoinDef(
    table="files", alias="files",
    from_table="symbols", from_col="file_id", to_col="id",
)


# ── Root entity: function ────────────────────────────────────────────────────

_FUNCTION_FIELDS: dict[str, FieldDef] = {
    "name":       FieldDef("name",       STRING, "symbols", "name"),
    "filename":   FieldDef("filename",   STRING, "files",   "path"),
    "language":   FieldDef("language",   STRING, "files",   "language"),
    "start":      FieldDef("start",      INT,    "symbols", "start_line"),
    "end":        FieldDef("end",        INT,    "symbols", "end_line"),
    "kind":       FieldDef("kind",       STRING, "symbols", "kind"),
    "signature":  FieldDef("signature",  JSON,   "symbols", "signature"),
    "paramCount": FieldDef("paramCount", INT,    "symbols", "signature",
                           computed="param_count"),
}

_CALL_EXPR_FIELDS: dict[str, FieldDef] = {
    "name":      FieldDef("name",      STRING, "expressions", "extra",
                          json_path=("callee",)),
    "argCount":  FieldDef("argCount",  INT,    "expressions", "extra",
                          json_path=("arg_count",)),
    "isMethod":  FieldDef("isMethod",  BOOL,   "expressions", "extra",
                          json_path=("is_method",)),
    "enclosing": FieldDef("enclosing", STRING, "symbols",     "name"),
    "filename":  FieldDef("filename",  STRING, "files",       "path"),
    "start":     FieldDef("start",     INT,    "expressions", "start_line"),
    "end":       FieldDef("end",       INT,    "expressions", "end_line"),
    "source":    FieldDef("source",    STRING, "expressions", "source_text"),
}

_DEPTH_FIELD = FieldDef("depth", INT, "symbols", "", computed="call_depth")

_FUNCTION_ENTITY = EntityDef(
    name="function",
    is_root=True,
    base_table="symbols",
    base_where={"kind": ["function", "method"]},
    fields=_FUNCTION_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={
        "calls": TraversalDef(
            name="calls",
            terminal_entity="call_expression",
        ),
        "callers": TraversalDef(
            name="callers",
            terminal_entity="function_with_callers",
        ),
        "callees": TraversalDef(
            name="callees",
            terminal_entity="callee_function",
        ),
    },
    predicates={
        "withoutArgs": PredicateDef(
            name="withoutArgs",
            arity=0,
            terminal_entity=None,
            filter_kind=FILTER_WHERE,
            filter_spec={"op": "param_count_zero"},
        ),
        "signature": PredicateDef(
            name="signature",
            arity=None,
            terminal_entity=None,
            filter_kind=FILTER_WHERE,
            filter_spec={"op": "signature_match"},
        ),
        "getDoesThrow": PredicateDef(
            name="getDoesThrow",
            arity=0,
            terminal_entity=None,
            filter_kind=FILTER_EXISTS,
            filter_spec={"subquery": "raise_in_body"},
        ),
        "withoutCallers": PredicateDef(
            name="withoutCallers",
            arity=0,
            terminal_entity=None,
            filter_kind=FILTER_NOT_EXISTS,
            filter_spec={"subquery": "caller_exists"},
        ),
        "calls": PredicateDef(
            name="calls",
            arity=1,
            terminal_entity="call_expression",
            filter_kind=FILTER_WHERE,
            filter_spec={"op": "arg_filter", "table": "expressions",
                         "column": "extra", "json_key": "callee"},
        ),
        "callDepth": PredicateDef(
            name="callDepth",
            arity=0,
            terminal_entity=None,
            filter_kind=FILTER_CTE,
            filter_spec={"cte": "call_depth"},
            injected_fields=[_DEPTH_FIELD],
        ),
    },
)


# ── Virtual entity: call_expression ─────────────────────────────────────────

_CALL_EXPRESSION_ENTITY = EntityDef(
    name="call_expression",
    is_root=False,
    base_table="expressions",
    base_where={"kind": "call"},
    fields=_CALL_EXPR_FIELDS,
    required_joins=[
        # expressions → symbols (for enclosing function name)
        _JOIN_SYMBOLS_FROM_EXPRESSIONS,
        # expressions → files (for filename)
        _JOIN_FILES_FROM_EXPRESSIONS,
    ],
    traversals={},
    predicates={},
)


# ── Virtual entity: function_with_callers ───────────────────────────────────

_FUNCTION_WITH_CALLERS_ENTITY = EntityDef(
    name="function_with_callers",
    is_root=False,
    base_table="symbols",
    base_where={"kind": ["function", "method"]},
    fields={
        **_FUNCTION_FIELDS,
        "callerCount": FieldDef("callerCount", INT, "references", "",
                                computed="caller_count"),
    },
    required_joins=[
        _JOIN_FILES_FROM_SYMBOLS,
        JoinDef(
            table="references", alias="references",
            from_table="symbols", from_col="id", to_col="to_symbol_id",
            kind=JOIN_LEFT,
        ),
    ],
    traversals={},
    predicates={},
)


# ── Virtual entity: callee_function ─────────────────────────────────────────

_CALLEE_FUNCTION_ENTITY = EntityDef(
    name="callee_function",
    is_root=False,
    base_table="symbols",
    base_where={"kind": ["function", "method"]},
    fields=_FUNCTION_FIELDS,
    required_joins=[
        _JOIN_FILES_FROM_SYMBOLS,
        JoinDef(
            table="references", alias="references",
            from_table="symbols", from_col="id", to_col="from_symbol_id",
            kind=JOIN_INNER,
        ),
    ],
    traversals={},
    predicates={},
)


# ── Root entity: class ───────────────────────────────────────────────────────

_CLASS_FIELDS: dict[str, FieldDef] = {
    "name":     FieldDef("name",     STRING, "symbols", "name"),
    "filename": FieldDef("filename", STRING, "files",   "path"),
    "language": FieldDef("language", STRING, "files",   "language"),
    "start":    FieldDef("start",    INT,    "symbols", "start_line"),
    "end":      FieldDef("end",      INT,    "symbols", "end_line"),
}

_CLASS_ENTITY = EntityDef(
    name="class",
    is_root=True,
    base_table="symbols",
    base_where={"kind": "class"},
    fields=_CLASS_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={
        "methods": TraversalDef(
            name="methods",
            terminal_entity="method",
        ),
    },
    predicates={},
)


# ── Virtual entity: method ───────────────────────────────────────────────────
# Reached via class.methods. Requires a self-join on symbols for className.

_METHOD_ENTITY = EntityDef(
    name="method",
    is_root=False,
    base_table="symbols",
    base_where={"kind": "method"},
    fields={
        "name":      FieldDef("name",      STRING, "symbols",    "name"),
        "filename":  FieldDef("filename",  STRING, "files",      "path"),
        "language":  FieldDef("language",  STRING, "files",      "language"),
        "start":     FieldDef("start",     INT,    "symbols",    "start_line"),
        "end":       FieldDef("end",       INT,    "symbols",    "end_line"),
        "kind":      FieldDef("kind",      STRING, "symbols",    "kind"),
        # className resolved from the parent class row via the self-join
        "className": FieldDef("className", STRING, "parent_sym", "name",
                              nullable=True),
    },
    required_joins=[
        _JOIN_FILES_FROM_SYMBOLS,
        _JOIN_PARENT_SYM,
    ],
    traversals={
        "calls": TraversalDef(
            name="calls",
            terminal_entity="call_expression",
        ),
    },
    predicates={},
)


# ── Root entity: file ────────────────────────────────────────────────────────

_FILE_ENTITY = EntityDef(
    name="file",
    is_root=True,
    base_table="files",
    base_where={},
    fields={
        "filename":  FieldDef("filename",  STRING, "files", "path"),
        "language":  FieldDef("language",  STRING, "files", "language"),
        "scannedAt": FieldDef("scannedAt", STRING, "files", "scanned_at"),
    },
    required_joins=[],
    traversals={
        "functions": TraversalDef(
            name="functions",
            terminal_entity="file_function",
            extra_joins=(
                JoinDef(
                    table="symbols", alias="symbols",
                    from_table="files", from_col="id", to_col="file_id",
                    extra_where=(("symbols", "kind", "function"),
                                 ("symbols", "kind", "method")),
                ),
            ),
        ),
        "classes": TraversalDef(
            name="classes",
            terminal_entity="file_class",
            extra_joins=(
                JoinDef(
                    table="symbols", alias="symbols",
                    from_table="files", from_col="id", to_col="file_id",
                    extra_where=(("symbols", "kind", "class"),),
                ),
            ),
        ),
        "imports": TraversalDef(
            name="imports",
            terminal_entity="import_expression",
            extra_joins=(
                JoinDef(
                    table="expressions", alias="expressions",
                    from_table="files", from_col="id", to_col="file_id",
                    extra_where=(("expressions", "kind", "import"),),
                ),
            ),
        ),
    },
    predicates={},
)


# ── Virtual entity: import_expression ───────────────────────────────────────

_IMPORT_EXPRESSION_ENTITY = EntityDef(
    name="import_expression",
    is_root=False,
    base_table="expressions",
    base_where={"kind": "import"},
    fields={
        "filename": FieldDef("filename", STRING, "files",       "path"),
        "module":   FieldDef("module",   STRING, "expressions", "extra",
                             json_path=("module",)),
        "source":   FieldDef("source",   STRING, "expressions", "source_text"),
        "start":    FieldDef("start",    INT,    "expressions", "start_line"),
        "end":      FieldDef("end",      INT,    "expressions", "end_line"),
    },
    required_joins=[_JOIN_FILES_FROM_EXPRESSIONS],
    traversals={},
    predicates={},
)


# ── Virtual entity: file_function / file_class ──────────────────────────────
# These are thin wrappers — the function/class entity fields but reached from
# file. The compiler uses the same field map; only the join direction differs.

_FILE_FUNCTION_ENTITY = EntityDef(
    name="file_function",
    is_root=False,
    base_table="symbols",
    base_where={"kind": ["function", "method"]},
    fields=_FUNCTION_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={},
    predicates={},
)

_FILE_CLASS_ENTITY = EntityDef(
    name="file_class",
    is_root=False,
    base_table="symbols",
    base_where={"kind": "class"},
    fields=_CLASS_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={},
    predicates={},
)


# ── Root entity: expression ──────────────────────────────────────────────────

_EXPRESSION_ENTITY = EntityDef(
    name="expression",
    is_root=True,
    base_table="expressions",
    base_where={},
    fields={
        "kind":   FieldDef("kind",   STRING, "expressions", "kind"),
        "source": FieldDef("source", STRING, "expressions", "source_text"),
        "start":  FieldDef("start",  INT,    "expressions", "start_line"),
        "end":    FieldDef("end",    INT,    "expressions", "end_line"),
        "depth":  FieldDef("depth",  INT,    "expressions", "depth"),
        "filename": FieldDef("filename", STRING, "files",   "path"),
    },
    required_joins=[_JOIN_FILES_FROM_EXPRESSIONS],
    traversals={},
    predicates={},
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class EntityRegistry:
    """
    Central lookup for all GQL entities, fields, traversals, and predicates.

    Access the module-level singleton: ``from graft_engine.entity_registry import REGISTRY``
    """

    def __init__(self, entities: list[EntityDef]) -> None:
        self._entities: dict[str, EntityDef] = {e.name: e for e in entities}

    # ── Entity lookup ────────────────────────────────────────────────────

    def get(self, name: str) -> EntityDef:
        """Return an EntityDef by name. Raises RegistryError if unknown."""
        try:
            return self._entities[name]
        except KeyError:
            roots = self.root_names()
            raise RegistryError(
                f"Unknown entity '{name}'. Root entities are: {roots}"
            )

    def root_names(self) -> list[str]:
        """Return names of all root (directly queryable) entities."""
        return [e.name for e in self._entities.values() if e.is_root]

    def all_names(self) -> list[str]:
        """Return names of all entities (root + virtual)."""
        return list(self._entities.keys())

    # ── Path resolution ──────────────────────────────────────────────────

    def resolve_path(self, entity_path: EntityPath) -> ResolvedPath:
        """
        Walk entity_path and produce a ResolvedPath ready for the compiler.

        Raises RegistryError on:
        - Unknown root entity
        - Unknown traversal or predicate name
        - Wrong predicate arity
        """
        current = self.get(entity_path.root)
        extra_joins:  list[JoinDef]               = []
        extra_where:  list[tuple[str, str, str]]   = []
        pred_apps:    list[PredicateApplication]   = []
        injected:     dict[str, FieldDef]          = {}

        for step in entity_path.traversals:
            if isinstance(step, FieldTraversal):
                tdef = current.traversals.get(step.name)
                if tdef is None:
                    raise RegistryError(
                        f"Entity '{current.name}' has no traversal '{step.name}'. "
                        f"Available: {list(current.traversals)}"
                    )
                extra_joins.extend(tdef.extra_joins)
                extra_where.extend(tdef.extra_where)
                current = self.get(tdef.terminal_entity)

            elif isinstance(step, PredicateTraversal):
                pdef = current.predicates.get(step.name)
                if pdef is None:
                    raise RegistryError(
                        f"Entity '{current.name}' has no predicate '{step.name}'. "
                        f"Available: {list(current.predicates)}"
                    )
                if pdef.arity is not None and len(step.args) != pdef.arity:
                    raise RegistryError(
                        f"Predicate '{step.name}' expects {pdef.arity} arg(s), "
                        f"got {len(step.args)}"
                    )
                pred_apps.append(PredicateApplication(predicate=pdef, args=list(step.args)))
                for f in pdef.injected_fields:
                    injected[f.gql_name] = f
                if pdef.terminal_entity is not None:
                    current = self.get(pdef.terminal_entity)

        return ResolvedPath(
            terminal=current,
            extra_joins=extra_joins,
            extra_where=extra_where,
            predicate_applications=pred_apps,
            injected_fields=injected,
        )

    # ── Field resolution ─────────────────────────────────────────────────

    def resolve_field(
        self,
        resolved: ResolvedPath,
        gql_name: str,
    ) -> FieldDef:
        """
        Resolve a GQL field name against the terminal entity and injected fields.

        Injected fields (from predicates like callDepth) take precedence.

        Raises RegistryError if the field is not found.
        """
        fdef = resolved.resolve_field(gql_name)
        if fdef is None:
            available = sorted(
                set(resolved.terminal.fields) | set(resolved.injected_fields)
            )
            raise RegistryError(
                f"Field '{gql_name}' not found on entity '{resolved.terminal.name}'. "
                f"Available: {available}"
            )
        return fdef

    def field_names(self, entity_name: str) -> list[str]:
        """List all GQL field names for a named entity."""
        return list(self.get(entity_name).fields)


# ---------------------------------------------------------------------------
# Module-level singleton — import this, don't construct your own
# ---------------------------------------------------------------------------

REGISTRY = EntityRegistry([
    _FUNCTION_ENTITY,
    _CLASS_ENTITY,
    _FILE_ENTITY,
    _EXPRESSION_ENTITY,
    # virtual entities
    _CALL_EXPRESSION_ENTITY,
    _METHOD_ENTITY,
    _IMPORT_EXPRESSION_ENTITY,
    _FILE_FUNCTION_ENTITY,
    _FILE_CLASS_ENTITY,
    _FUNCTION_WITH_CALLERS_ENTITY,
    _CALLEE_FUNCTION_ENTITY,
])
