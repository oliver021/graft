"""
Entity registry tests.

Covers:
- All root entities exist and carry correct base metadata
- Every field catalogued in CLAUDE.md / SPECS.md §6 is declared
- Traversals produce the correct terminal entity
- Predicates carry correct arity, filter kind, and injection spec
- resolve_path() for every §9 canonical query pattern
- Field resolution against terminal entity + injected fields
- Error paths: unknown entity, unknown traversal, wrong arity
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../parser"))

import pytest
from graft_engine.entity_registry import (
    REGISTRY, RegistryError,
    STRING, INT, BOOL, JSON,
    FILTER_WHERE, FILTER_EXISTS, FILTER_NOT_EXISTS, FILTER_CTE,
    JOIN_LEFT,
)
from graft_parser.ast_nodes import (
    EntityPath, FieldTraversal, PredicateTraversal,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _path(root, *travs):
    """Build an EntityPath from a root name and traversal tuples.

    Each traversal is either:
    - a string          → FieldTraversal
    - ("name", [args])  → PredicateTraversal
    """
    steps = []
    for t in travs:
        if isinstance(t, str):
            steps.append(FieldTraversal(name=t))
        else:
            steps.append(PredicateTraversal(name=t[0], args=t[1]))
    return EntityPath(root=root, traversals=steps)


# ── Root entity existence ─────────────────────────────────────────────────────

class TestRootEntities:

    def test_all_four_root_entities_exist(self):
        for name in ("function", "class", "file", "expression"):
            e = REGISTRY.get(name)
            assert e.is_root

    def test_root_names_returns_exactly_four(self):
        roots = REGISTRY.root_names()
        assert set(roots) == {"function", "class", "file", "expression"}

    def test_virtual_entities_not_in_root_names(self):
        roots = REGISTRY.root_names()
        for virtual in ("call_expression", "method", "import_expression"):
            assert virtual not in roots

    def test_virtual_entities_accessible_by_get(self):
        for name in ("call_expression", "method", "import_expression",
                     "file_function", "file_class",
                     "function_with_callers", "callee_function"):
            e = REGISTRY.get(name)
            assert not e.is_root


# ── function entity ───────────────────────────────────────────────────────────

class TestFunctionEntity:

    def setup_method(self):
        self.fn = REGISTRY.get("function")

    def test_base_table(self):
        assert self.fn.base_table == "symbols"

    def test_base_where_includes_function_and_method(self):
        kinds = self.fn.base_where["kind"]
        assert "function" in kinds
        assert "method" in kinds

    def test_fields_name(self):
        f = self.fn.get_field("name")
        assert f.table == "symbols" and f.column == "name" and f.gql_type == STRING

    def test_fields_filename(self):
        f = self.fn.get_field("filename")
        assert f.table == "files" and f.column == "path"

    def test_fields_language(self):
        f = self.fn.get_field("language")
        assert f.table == "files"

    def test_fields_start_end(self):
        assert self.fn.get_field("start").column == "start_line"
        assert self.fn.get_field("end").column == "end_line"

    def test_fields_kind(self):
        f = self.fn.get_field("kind")
        assert f.table == "symbols" and f.gql_type == STRING

    def test_fields_signature(self):
        f = self.fn.get_field("signature")
        assert f.gql_type == JSON

    def test_fields_param_count_is_computed(self):
        f = self.fn.get_field("paramCount")
        assert f.is_computed
        assert f.computed == "param_count"
        assert f.gql_type == INT

    def test_required_join_includes_files(self):
        tables = {j.table for j in self.fn.required_joins}
        assert "files" in tables

    def test_traversal_calls_exists(self):
        assert "calls" in self.fn.traversals

    def test_traversal_callers_exists(self):
        assert "callers" in self.fn.traversals

    def test_traversal_callees_exists(self):
        assert "callees" in self.fn.traversals

    def test_predicate_without_args_exists(self):
        p = self.fn.predicates["withoutArgs"]
        assert p.arity == 0
        assert p.terminal_entity is None
        assert p.filter_kind == FILTER_WHERE

    def test_predicate_get_does_throw(self):
        p = self.fn.predicates["getDoesThrow"]
        assert p.arity == 0
        assert p.filter_kind == FILTER_EXISTS

    def test_predicate_without_callers(self):
        p = self.fn.predicates["withoutCallers"]
        assert p.arity == 0
        assert p.filter_kind == FILTER_NOT_EXISTS

    def test_predicate_calls_with_arg(self):
        p = self.fn.predicates["calls"]
        assert p.arity == 1
        assert p.terminal_entity == "call_expression"

    def test_predicate_call_depth(self):
        p = self.fn.predicates["callDepth"]
        assert p.arity == 0
        assert p.filter_kind == FILTER_CTE
        assert any(f.gql_name == "depth" for f in p.injected_fields)


# ── call_expression entity ────────────────────────────────────────────────────

class TestCallExpressionEntity:

    def setup_method(self):
        self.ce = REGISTRY.get("call_expression")

    def test_base_table(self):
        assert self.ce.base_table == "expressions"

    def test_base_where_kind_call(self):
        assert self.ce.base_where["kind"] == "call"

    def test_field_name_is_json(self):
        f = self.ce.get_field("name")
        assert f.is_json
        assert f.json_path == ("callee",)
        assert f.table == "expressions"

    def test_field_arg_count(self):
        f = self.ce.get_field("argCount")
        assert f.json_path == ("arg_count",)
        assert f.gql_type == INT

    def test_field_is_method(self):
        f = self.ce.get_field("isMethod")
        assert f.json_path == ("is_method",)
        assert f.gql_type == BOOL

    def test_field_enclosing(self):
        f = self.ce.get_field("enclosing")
        assert f.table == "symbols" and f.column == "name"

    def test_field_filename(self):
        f = self.ce.get_field("filename")
        assert f.table == "files" and f.column == "path"

    def test_field_start_end(self):
        assert self.ce.get_field("start").column == "start_line"
        assert self.ce.get_field("end").column == "end_line"

    def test_field_source(self):
        f = self.ce.get_field("source")
        assert f.column == "source_text"

    def test_required_joins_include_symbols_and_files(self):
        tables = {j.table for j in self.ce.required_joins}
        assert "symbols" in tables
        assert "files" in tables


# ── class entity ─────────────────────────────────────────────────────────────

class TestClassEntity:

    def setup_method(self):
        self.cls = REGISTRY.get("class")

    def test_base_where_kind_class(self):
        assert self.cls.base_where["kind"] == "class"

    def test_fields_present(self):
        for name in ("name", "filename", "language", "start", "end"):
            assert self.cls.get_field(name) is not None

    def test_traversal_methods(self):
        assert "methods" in self.cls.traversals
        assert self.cls.traversals["methods"].terminal_entity == "method"


# ── method entity ─────────────────────────────────────────────────────────────

class TestMethodEntity:

    def setup_method(self):
        self.m = REGISTRY.get("method")

    def test_base_where_kind_method(self):
        assert self.m.base_where["kind"] == "method"

    def test_class_name_field_uses_parent_join(self):
        f = self.m.get_field("className")
        assert f.table == "parent_sym"
        assert f.column == "name"

    def test_self_join_present_in_required_joins(self):
        aliases = {j.alias for j in self.m.required_joins}
        assert "parent_sym" in aliases

    def test_self_join_is_left(self):
        parent_join = next(j for j in self.m.required_joins if j.alias == "parent_sym")
        assert parent_join.kind == JOIN_LEFT

    def test_standard_fields_present(self):
        for name in ("name", "filename", "start", "end", "kind"):
            assert self.m.get_field(name) is not None


# ── file entity ───────────────────────────────────────────────────────────────

class TestFileEntity:

    def setup_method(self):
        self.f = REGISTRY.get("file")

    def test_base_table_is_files(self):
        assert self.f.base_table == "files"

    def test_no_base_where(self):
        assert self.f.base_where == {}

    def test_field_filename(self):
        f = self.f.get_field("filename")
        assert f.column == "path"

    def test_field_language(self):
        assert self.f.get_field("language") is not None

    def test_field_scanned_at(self):
        f = self.f.get_field("scannedAt")
        assert f.column == "scanned_at"

    def test_traversal_functions(self):
        assert "functions" in self.f.traversals
        assert self.f.traversals["functions"].terminal_entity == "file_function"

    def test_traversal_classes(self):
        assert "classes" in self.f.traversals
        assert self.f.traversals["classes"].terminal_entity == "file_class"

    def test_traversal_imports(self):
        assert "imports" in self.f.traversals
        assert self.f.traversals["imports"].terminal_entity == "import_expression"


# ── import_expression entity ──────────────────────────────────────────────────

class TestImportExpressionEntity:

    def setup_method(self):
        self.ie = REGISTRY.get("import_expression")

    def test_base_where_kind_import(self):
        assert self.ie.base_where["kind"] == "import"

    def test_field_module_is_json(self):
        f = self.ie.get_field("module")
        assert f.is_json and f.json_path == ("module",)

    def test_field_filename(self):
        assert self.ie.get_field("filename") is not None


# ── expression entity ─────────────────────────────────────────────────────────

class TestExpressionEntity:

    def setup_method(self):
        self.e = REGISTRY.get("expression")

    def test_base_table_is_expressions(self):
        assert self.e.base_table == "expressions"

    def test_no_base_where(self):
        assert self.e.base_where == {}

    def test_fields_present(self):
        for name in ("kind", "source", "start", "end", "depth", "filename"):
            assert self.e.get_field(name) is not None


# ── resolve_path — §9 canonical query patterns ────────────────────────────────

class TestResolvePath:

    def test_canon_1_function_calls(self):
        # from function.calls as c ...
        rp = REGISTRY.resolve_path(_path("function", "calls"))
        assert rp.terminal.name == "call_expression"
        assert rp.predicate_applications == []
        assert rp.injected_fields == {}

    def test_canon_2_without_args(self):
        # from function.withoutArgs() as fn ...
        rp = REGISTRY.resolve_path(_path("function", ("withoutArgs", [])))
        assert rp.terminal.name == "function"
        assert len(rp.predicate_applications) == 1
        pa = rp.predicate_applications[0]
        assert pa.predicate.name == "withoutArgs"
        assert pa.args == []

    def test_canon_3_call_depth(self):
        # from function.callDepth() as fn ...
        rp = REGISTRY.resolve_path(_path("function", ("callDepth", [])))
        assert rp.terminal.name == "function"
        assert "depth" in rp.injected_fields
        pa = rp.predicate_applications[0]
        assert pa.predicate.filter_kind == FILTER_CTE

    def test_canon_4_class_methods(self):
        # from class.methods as m ...
        rp = REGISTRY.resolve_path(_path("class", "methods"))
        assert rp.terminal.name == "method"

    def test_canon_5_function_calls_or_condition(self):
        # from function.calls as c where c.name like "open" or ...
        rp = REGISTRY.resolve_path(_path("function", "calls"))
        assert rp.terminal.name == "call_expression"

    def test_function_without_callers(self):
        rp = REGISTRY.resolve_path(_path("function", ("withoutCallers", [])))
        assert rp.terminal.name == "function"
        pa = rp.predicate_applications[0]
        assert pa.predicate.filter_kind == FILTER_NOT_EXISTS

    def test_function_get_does_throw(self):
        rp = REGISTRY.resolve_path(_path("function", ("getDoesThrow", [])))
        pa = rp.predicate_applications[0]
        assert pa.predicate.filter_kind == FILTER_EXISTS

    def test_function_calls_with_arg(self):
        # from function.calls("eval") as c ...
        rp = REGISTRY.resolve_path(_path("function", ("calls", ["eval"])))
        assert rp.terminal.name == "call_expression"
        pa = rp.predicate_applications[0]
        assert pa.args == ["eval"]

    def test_function_callers(self):
        rp = REGISTRY.resolve_path(_path("function", "callers"))
        assert rp.terminal.name == "function_with_callers"
        f = rp.terminal.get_field("callerCount")
        assert f is not None and f.computed == "caller_count"

    def test_function_callees(self):
        rp = REGISTRY.resolve_path(_path("function", "callees"))
        assert rp.terminal.name == "callee_function"

    def test_file_imports(self):
        rp = REGISTRY.resolve_path(_path("file", "imports"))
        assert rp.terminal.name == "import_expression"

    def test_file_functions(self):
        rp = REGISTRY.resolve_path(_path("file", "functions"))
        assert rp.terminal.name == "file_function"

    def test_file_classes(self):
        rp = REGISTRY.resolve_path(_path("file", "classes"))
        assert rp.terminal.name == "file_class"

    def test_no_traversals(self):
        rp = REGISTRY.resolve_path(_path("function"))
        assert rp.terminal.name == "function"
        assert rp.predicate_applications == []

    def test_class_methods_calls_chain(self):
        # from class.methods.calls as c ...
        rp = REGISTRY.resolve_path(_path("class", "methods", "calls"))
        assert rp.terminal.name == "call_expression"

    def test_injected_field_shadows_terminal_field(self):
        # callDepth injects "depth"; function doesn't normally have that field
        rp = REGISTRY.resolve_path(_path("function", ("callDepth", [])))
        field = rp.resolve_field("depth")
        assert field is not None
        assert field.computed == "call_depth"

    def test_predicate_application_args_preserved(self):
        rp = REGISTRY.resolve_path(_path("function", ("calls", ["open"])))
        assert rp.predicate_applications[0].args == ["open"]

    def test_multiple_predicates_accumulated(self):
        # from function.withoutArgs().getDoesThrow() as fn ...
        rp = REGISTRY.resolve_path(
            _path("function", ("withoutArgs", []), ("getDoesThrow", []))
        )
        assert len(rp.predicate_applications) == 2
        kinds = {pa.predicate.filter_kind for pa in rp.predicate_applications}
        assert FILTER_WHERE in kinds
        assert FILTER_EXISTS in kinds


# ── Field resolution ──────────────────────────────────────────────────────────

class TestFieldResolution:

    def test_resolve_known_field(self):
        rp = REGISTRY.resolve_path(_path("function"))
        f = REGISTRY.resolve_field(rp, "name")
        assert f.column == "name"

    def test_resolve_injected_field(self):
        rp = REGISTRY.resolve_path(_path("function", ("callDepth", [])))
        f = REGISTRY.resolve_field(rp, "depth")
        assert f.computed == "call_depth"

    def test_resolve_unknown_field_raises(self):
        rp = REGISTRY.resolve_path(_path("function"))
        with pytest.raises(RegistryError, match="depth"):
            REGISTRY.resolve_field(rp, "depth")

    def test_field_names_for_function(self):
        names = REGISTRY.field_names("function")
        for expected in ("name", "filename", "start", "end", "paramCount"):
            assert expected in names

    def test_field_names_for_call_expression(self):
        names = REGISTRY.field_names("call_expression")
        for expected in ("name", "argCount", "isMethod", "enclosing", "filename"):
            assert expected in names


# ── Error paths ───────────────────────────────────────────────────────────────

class TestErrors:

    def test_unknown_root_entity_raises(self):
        with pytest.raises(RegistryError, match="unknown_root"):
            REGISTRY.get("unknown_root")

    def test_error_message_lists_root_entities(self):
        try:
            REGISTRY.get("xyz")
        except RegistryError as e:
            assert "function" in str(e)

    def test_unknown_traversal_raises(self):
        with pytest.raises(RegistryError, match="noSuchTraversal"):
            REGISTRY.resolve_path(_path("function", "noSuchTraversal"))

    def test_unknown_predicate_raises(self):
        with pytest.raises(RegistryError, match="noSuchPredicate"):
            REGISTRY.resolve_path(_path("function", ("noSuchPredicate", [])))

    def test_wrong_arity_raises(self):
        # withoutArgs() expects 0 args
        with pytest.raises(RegistryError, match="0 arg"):
            REGISTRY.resolve_path(_path("function", ("withoutArgs", ["extra_arg"])))

    def test_calls_predicate_needs_one_arg(self):
        with pytest.raises(RegistryError):
            REGISTRY.resolve_path(_path("function", ("calls", [])))

    def test_unknown_field_raises_registry_error(self):
        rp = REGISTRY.resolve_path(_path("function"))
        with pytest.raises(RegistryError):
            REGISTRY.resolve_field(rp, "nonexistent_field")

    def test_traversal_on_virtual_entity_without_traversals(self):
        # call_expression has no traversals
        with pytest.raises(RegistryError):
            REGISTRY.resolve_path(_path("function", "calls", "nonexistent"))
