"""
GQL entity definitions — the normative mapping of GQL concepts to SQL.

All table references match code_indexer/schema.py.
Aliases:
  "symbols"     → symbols_table
  "files"       → files_table
  "expressions" → expressions_table
  "references"  → references_table
  "parent_sym"  → symbols_table self-join (for method.className)
"""

from __future__ import annotations

from ._types import (
    STRING, INT, BOOL, JSON,
    FILTER_WHERE, FILTER_EXISTS, FILTER_NOT_EXISTS, FILTER_CTE,
    JOIN_INNER, JOIN_LEFT,
    EntityDef, FieldDef, JoinDef, PredicateDef, TraversalDef,
)


# ---------------------------------------------------------------------------
# Shared JoinDefs
# ---------------------------------------------------------------------------

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

_JOIN_PARENT_SYM = JoinDef(
    table="symbols", alias="parent_sym",
    from_table="symbols", from_col="parent_id", to_col="id",
    kind=JOIN_LEFT,
)


# ---------------------------------------------------------------------------
# Shared field maps
# ---------------------------------------------------------------------------

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

_CLASS_FIELDS: dict[str, FieldDef] = {
    "name":     FieldDef("name",     STRING, "symbols", "name"),
    "filename": FieldDef("filename", STRING, "files",   "path"),
    "language": FieldDef("language", STRING, "files",   "language"),
    "start":    FieldDef("start",    INT,    "symbols", "start_line"),
    "end":      FieldDef("end",      INT,    "symbols", "end_line"),
}

_DEPTH_FIELD = FieldDef("depth", INT, "symbols", "", computed="call_depth")


# ---------------------------------------------------------------------------
# Root entity: function
# ---------------------------------------------------------------------------

FUNCTION_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Virtual entity: call_expression
# ---------------------------------------------------------------------------

CALL_EXPRESSION_ENTITY = EntityDef(
    name="call_expression",
    is_root=False,
    base_table="expressions",
    base_where={"kind": "call"},
    fields=_CALL_EXPR_FIELDS,
    required_joins=[
        _JOIN_SYMBOLS_FROM_EXPRESSIONS,
        _JOIN_FILES_FROM_EXPRESSIONS,
    ],
    traversals={},
    predicates={},
)


# ---------------------------------------------------------------------------
# Virtual entity: function_with_callers
# ---------------------------------------------------------------------------

FUNCTION_WITH_CALLERS_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Virtual entity: callee_function
# ---------------------------------------------------------------------------

CALLEE_FUNCTION_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Root entity: class
# ---------------------------------------------------------------------------

CLASS_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Virtual entity: method
# ---------------------------------------------------------------------------
# Reached via class.methods. Requires a self-join on symbols for className.

METHOD_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Root entity: file
# ---------------------------------------------------------------------------

FILE_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Virtual entity: import_expression
# ---------------------------------------------------------------------------

IMPORT_EXPRESSION_ENTITY = EntityDef(
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


# ---------------------------------------------------------------------------
# Virtual entities: file_function / file_class
# ---------------------------------------------------------------------------
# Thin wrappers — same field maps as function/class but reached from file.
# The compiler uses the same field map; only the join direction differs.

FILE_FUNCTION_ENTITY = EntityDef(
    name="file_function",
    is_root=False,
    base_table="symbols",
    base_where={"kind": ["function", "method"]},
    fields=_FUNCTION_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={},
    predicates={},
)

FILE_CLASS_ENTITY = EntityDef(
    name="file_class",
    is_root=False,
    base_table="symbols",
    base_where={"kind": "class"},
    fields=_CLASS_FIELDS,
    required_joins=[_JOIN_FILES_FROM_SYMBOLS],
    traversals={},
    predicates={},
)


# ---------------------------------------------------------------------------
# Root entity: expression
# ---------------------------------------------------------------------------

EXPRESSION_ENTITY = EntityDef(
    name="expression",
    is_root=True,
    base_table="expressions",
    base_where={},
    fields={
        "kind":     FieldDef("kind",     STRING, "expressions", "kind"),
        "source":   FieldDef("source",   STRING, "expressions", "source_text"),
        "start":    FieldDef("start",    INT,    "expressions", "start_line"),
        "end":      FieldDef("end",      INT,    "expressions", "end_line"),
        "depth":    FieldDef("depth",    INT,    "expressions", "depth"),
        "filename": FieldDef("filename", STRING, "files",       "path"),
    },
    required_joins=[_JOIN_FILES_FROM_EXPRESSIONS],
    traversals={},
    predicates={},
)


# ---------------------------------------------------------------------------
# Exported list — consumed by EntityRegistry constructor
# ---------------------------------------------------------------------------

ALL_ENTITIES = [
    FUNCTION_ENTITY,
    CLASS_ENTITY,
    FILE_ENTITY,
    EXPRESSION_ENTITY,
    # virtual entities
    CALL_EXPRESSION_ENTITY,
    METHOD_ENTITY,
    IMPORT_EXPRESSION_ENTITY,
    FILE_FUNCTION_ENTITY,
    FILE_CLASS_ENTITY,
    FUNCTION_WITH_CALLERS_ENTITY,
    CALLEE_FUNCTION_ENTITY,
]
