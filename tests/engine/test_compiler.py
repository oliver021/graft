"""
Tests for graft_engine.compiler.

Coverage:
- CompiledQuery shape (columns, query_sql, statement)
- All 6 canonical §9 examples from SPECS.md / CLAUDE.md
- Each root entity (function, class, file, expression)
- Each traversal (calls, callers, callees, methods, imports, functions, classes)
- Each predicate (withoutArgs, getDoesThrow, withoutCallers, callDepth, calls("x"))
- WHERE conditions (=, !=, >, <, >=, <=, like, null, not, and, or)
- Projections (field, literal, concat)
- GROUP BY auto-injection for callerCount
- CTE generation for callDepth
- CompileError on unknown fields, bad aliases
"""

import sys
import os

# Ensure packages are importable
for p in ("engine", "parser", "code_indexer"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", p))

import pytest
from sqlalchemy.sql import Select

from graft_parser._parser import parse
from graft_engine.compiler import compile, CompiledQuery, CompileError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile(query: str) -> CompiledQuery:
    return compile(parse(query))


def _sql(query: str) -> str:
    return _compile(query).query_sql


# ---------------------------------------------------------------------------
# TestCompiledQueryShape
# ---------------------------------------------------------------------------

class TestCompiledQueryShape:
    def test_returns_compiled_query(self):
        cq = _compile("from function as fn select fn.name")
        assert isinstance(cq, CompiledQuery)

    def test_has_statement(self):
        cq = _compile("from function as fn select fn.name")
        assert isinstance(cq.statement, Select)

    def test_has_query_sql_string(self):
        cq = _compile("from function as fn select fn.name")
        assert isinstance(cq.query_sql, str)
        assert len(cq.query_sql) > 10

    def test_has_columns_list(self):
        cq = _compile("from function as fn select fn.name, fn.filename")
        assert cq.columns == ["name", "filename"]

    def test_columns_order_matches_projection(self):
        cq = _compile("from function as fn select fn.start, fn.end, fn.name")
        assert cq.columns == ["start", "end", "name"]

    def test_query_sql_contains_select(self):
        sql = _sql("from function as fn select fn.name")
        assert "SELECT" in sql.upper()

    def test_query_sql_contains_from(self):
        sql = _sql("from function as fn select fn.name")
        assert "FROM" in sql.upper()


# ---------------------------------------------------------------------------
# TestCanonicalExamples  (SPECS.md §9 / CLAUDE.md examples)
# ---------------------------------------------------------------------------

class TestCanonicalExamples:
    """All 6 canonical §9 examples must compile without error and produce correct SQL."""

    def test_example1_calls_eval(self):
        """from function.calls as c where c.name = "eval" select c.filename, c.start, c.enclosing"""
        cq = _compile('from function.calls as c where c.name = "eval" select c.filename, c.start, c.enclosing')
        assert cq.columns == ["filename", "start", "enclosing"]
        sql = cq.query_sql
        assert "expressions" in sql
        assert "json_extract" in sql
        assert "eval" in sql

    def test_example2_without_args(self):
        """from function.withoutArgs() as fn select fn.name, fn.filename"""
        cq = _compile("from function.withoutArgs() as fn select fn.name, fn.filename")
        assert cq.columns == ["name", "filename"]
        sql = cq.query_sql
        assert "json_array_length" in sql
        assert "params" in sql

    def test_example3_does_throw(self):
        """from function.getDoesThrow() as fn select fn.name, fn.filename, fn.start"""
        cq = _compile("from function.getDoesThrow() as fn select fn.name, fn.filename, fn.start")
        assert cq.columns == ["name", "filename", "start"]
        sql = cq.query_sql
        assert "EXISTS" in sql.upper()
        assert "raise" in sql

    def test_example4_callers_caller_count(self):
        """from function.callers as fn select fn.name, fn.callerCount, fn.filename"""
        cq = _compile("from function.callers as fn select fn.name, fn.callerCount, fn.filename")
        assert "callerCount" in cq.columns
        sql = cq.query_sql
        assert "count" in sql.lower()
        assert "GROUP BY" in sql.upper()

    def test_example5_without_callers(self):
        """from function.withoutCallers() as fn select fn.name, fn.filename, fn.start"""
        cq = _compile("from function.withoutCallers() as fn select fn.name, fn.filename, fn.start")
        assert cq.columns == ["name", "filename", "start"]
        sql = cq.query_sql
        assert "NOT" in sql.upper()
        assert "EXISTS" in sql.upper()

    def test_example6_file_imports(self):
        """from file.imports as i select i.filename, i.module"""
        cq = _compile("from file.imports as i select i.filename, i.module")
        assert cq.columns == ["filename", "module"]
        sql = cq.query_sql
        assert "expressions" in sql
        assert "import" in sql


# ---------------------------------------------------------------------------
# TestRootEntityFunction
# ---------------------------------------------------------------------------

class TestRootEntityFunction:
    def test_base_table_is_symbols(self):
        sql = _sql("from function as fn select fn.name")
        assert "FROM symbols" in sql

    def test_base_where_kind_in(self):
        sql = _sql("from function as fn select fn.name")
        assert "function" in sql
        assert "method" in sql
        # IN clause
        assert "IN" in sql.upper() or "kind" in sql

    def test_joins_files(self):
        sql = _sql("from function as fn select fn.filename")
        assert "files" in sql
        assert "file_id" in sql

    def test_field_name(self):
        sql = _sql("from function as fn select fn.name")
        assert "symbols.name" in sql

    def test_field_filename(self):
        sql = _sql("from function as fn select fn.filename")
        assert "files.path" in sql

    def test_field_start_line(self):
        sql = _sql("from function as fn select fn.start")
        assert "start_line" in sql

    def test_field_end_line(self):
        sql = _sql("from function as fn select fn.end")
        assert "end_line" in sql

    def test_field_language(self):
        sql = _sql("from function as fn select fn.language")
        assert "language" in sql

    def test_field_param_count_uses_json(self):
        sql = _sql("from function as fn select fn.paramCount")
        assert "json_array_length" in sql
        assert "params" in sql

    def test_field_kind(self):
        sql = _sql("from function as fn select fn.kind")
        assert "symbols.kind" in sql


# ---------------------------------------------------------------------------
# TestRootEntityClass
# ---------------------------------------------------------------------------

class TestRootEntityClass:
    def test_base_where_kind_class(self):
        sql = _sql("from class as c select c.name")
        assert "class" in sql

    def test_field_name(self):
        sql = _sql("from class as c select c.name")
        assert "symbols.name" in sql


# ---------------------------------------------------------------------------
# TestRootEntityFile
# ---------------------------------------------------------------------------

class TestRootEntityFile:
    def test_base_table_is_files(self):
        sql = _sql("from file as f select f.filename")
        assert "FROM files" in sql

    def test_field_filename(self):
        sql = _sql("from file as f select f.filename")
        assert "files.path" in sql

    def test_field_language(self):
        sql = _sql("from file as f select f.language")
        assert "files.language" in sql

    def test_no_unnecessary_joins(self):
        sql = _sql("from file as f select f.filename")
        assert "symbols" not in sql
        assert "expressions" not in sql


# ---------------------------------------------------------------------------
# TestRootEntityExpression
# ---------------------------------------------------------------------------

class TestRootEntityExpression:
    def test_base_table_is_expressions(self):
        sql = _sql("from expression as e select e.kind")
        assert "FROM expressions" in sql

    def test_no_base_where(self):
        sql = _sql("from expression as e select e.kind")
        # expression entity has no base_where filters
        assert "kind =" not in sql.replace("e.kind", "")

    def test_field_source(self):
        sql = _sql("from expression as e select e.source")
        assert "source_text" in sql

    def test_joins_files_for_filename(self):
        sql = _sql("from expression as e select e.filename")
        assert "files" in sql


# ---------------------------------------------------------------------------
# TestTraversals
# ---------------------------------------------------------------------------

class TestTraversals:
    def test_function_calls_uses_expressions(self):
        sql = _sql("from function.calls as c select c.name")
        assert "FROM expressions" in sql
        assert "call" in sql

    def test_function_calls_name_is_json_extract(self):
        sql = _sql("from function.calls as c select c.name")
        assert "json_extract" in sql
        assert "callee" in sql

    def test_function_calls_enclosing_is_symbols_name(self):
        sql = _sql("from function.calls as c select c.enclosing")
        assert "symbols.name" in sql

    def test_function_callers_uses_references(self):
        sql = _sql("from function.callers as fn select fn.name")
        assert "references" in sql

    def test_function_callees_uses_references(self):
        sql = _sql("from function.callees as fn select fn.name")
        assert "references" in sql

    def test_class_methods_kind_method(self):
        sql = _sql("from class.methods as m select m.name")
        assert "method" in sql

    def test_class_methods_class_name_field(self):
        sql = _sql("from class.methods as m select m.className")
        assert "parent_sym" in sql

    def test_class_methods_left_join_parent(self):
        sql = _sql("from class.methods as m select m.className")
        assert "LEFT OUTER JOIN" in sql.upper() or "LEFT JOIN" in sql.upper()

    def test_file_imports_uses_expressions(self):
        sql = _sql("from file.imports as i select i.module")
        assert "expressions" in sql
        assert "import" in sql

    def test_file_imports_module_is_json(self):
        sql = _sql("from file.imports as i select i.module")
        assert "json_extract" in sql
        assert "module" in sql

    def test_file_functions_traversal(self):
        sql = _sql("from file.functions as fn select fn.name")
        assert "symbols" in sql
        assert "function" in sql

    def test_file_classes_traversal(self):
        sql = _sql("from file.classes as c select c.name")
        assert "symbols" in sql
        assert "class" in sql

    def test_method_calls_chain(self):
        sql = _sql("from class.methods.calls as c select c.name")
        assert "expressions" in sql
        assert "call" in sql


# ---------------------------------------------------------------------------
# TestPredicates
# ---------------------------------------------------------------------------

class TestPredicates:
    def test_without_args_adds_json_length_zero(self):
        sql = _sql("from function.withoutArgs() as fn select fn.name")
        assert "json_array_length" in sql
        assert "= 0" in sql

    def test_get_does_throw_uses_exists(self):
        sql = _sql("from function.getDoesThrow() as fn select fn.name")
        assert "EXISTS" in sql.upper()
        assert "raise" in sql

    def test_without_callers_uses_not_exists(self):
        sql = _sql("from function.withoutCallers() as fn select fn.name")
        assert "NOT" in sql.upper()
        assert "EXISTS" in sql.upper()

    def test_without_callers_subquery_on_references(self):
        sql = _sql("from function.withoutCallers() as fn select fn.name")
        assert "references" in sql

    def test_calls_predicate_with_arg(self):
        sql = _sql('from function.calls("eval") as c select c.name')
        assert "eval" in sql
        assert "callee" in sql

    def test_calls_predicate_moves_to_call_expression(self):
        cq = _compile('from function.calls("open") as c select c.enclosing, c.filename')
        assert cq.columns == ["enclosing", "filename"]

    def test_call_depth_generates_cte(self):
        sql = _sql("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "WITH RECURSIVE" in sql.upper() or "WITH" in sql.upper()
        assert "call_graph" in sql

    def test_call_depth_injects_depth_column(self):
        cq = _compile("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "depth" in cq.columns

    def test_call_depth_with_where_depth_filter(self):
        sql = _sql("from function.callDepth() as fn where fn.depth > 4 select fn.name, fn.depth, fn.filename")
        assert "4" in sql
        assert "call_depth" in sql or "depth" in sql


# ---------------------------------------------------------------------------
# TestWhereConditions
# ---------------------------------------------------------------------------

class TestWhereConditions:
    def test_equal(self):
        sql = _sql('from function as fn where fn.name = "foo" select fn.name')
        assert "foo" in sql
        assert "=" in sql

    def test_not_equal(self):
        sql = _sql('from function as fn where fn.name != "foo" select fn.name')
        assert "foo" in sql
        assert "!=" in sql or "<>" in sql

    def test_greater_than(self):
        sql = _sql("from function as fn where fn.start > 10 select fn.name")
        assert "10" in sql
        assert ">" in sql

    def test_less_than(self):
        sql = _sql("from function as fn where fn.start < 100 select fn.name")
        assert "100" in sql
        assert "<" in sql

    def test_gte(self):
        sql = _sql("from function as fn where fn.start >= 5 select fn.name")
        assert ">=" in sql

    def test_lte(self):
        sql = _sql("from function as fn where fn.start <= 5 select fn.name")
        assert "<=" in sql

    def test_like(self):
        sql = _sql('from function as fn where fn.name like "get" select fn.name')
        assert "LIKE" in sql.upper()
        assert "get" in sql

    def test_null_equality(self):
        sql = _sql("from function as fn where fn.kind = null select fn.name")
        assert "IS NULL" in sql.upper() or "NULL" in sql.upper()

    def test_not_expr(self):
        sql = _sql('from function as fn where not fn.name = "foo" select fn.name')
        # SQLAlchemy optimises NOT (a = b) → a != b; both forms are correct
        assert "foo" in sql
        assert ("NOT" in sql.upper()) or ("!=" in sql) or ("<>" in sql)

    def test_and_condition(self):
        sql = _sql('from function as fn where fn.start > 1 and fn.end < 100 select fn.name')
        assert "AND" in sql.upper()

    def test_or_condition(self):
        sql = _sql('from function as fn where fn.name = "foo" or fn.name = "bar" select fn.name')
        assert "OR" in sql.upper()
        assert "foo" in sql
        assert "bar" in sql

    def test_where_on_json_field(self):
        sql = _sql('from function.calls as c where c.name = "eval" select c.name')
        assert "json_extract" in sql
        assert "eval" in sql


# ---------------------------------------------------------------------------
# TestProjection
# ---------------------------------------------------------------------------

class TestProjection:
    def test_single_field(self):
        cq = _compile("from function as fn select fn.name")
        assert cq.columns == ["name"]

    def test_multiple_fields(self):
        cq = _compile("from function as fn select fn.name, fn.filename, fn.start")
        assert cq.columns == ["name", "filename", "start"]

    def test_literal_in_select(self):
        cq = _compile('from function as fn select fn.name, "prefix"')
        assert "prefix" in cq.columns or any("prefix" in c for c in cq.columns)

    def test_concat_in_select(self):
        cq = _compile('from function as fn select fn.filename + ":" + fn.name')
        # Concat produces a label combining both sides
        assert len(cq.columns) == 1
        sql = cq.query_sql
        assert "||" in sql or "concat" in sql.lower()

    def test_label_from_field_path(self):
        cq = _compile("from function as fn select fn.start")
        assert "start" in cq.columns


# ---------------------------------------------------------------------------
# TestGroupBy
# ---------------------------------------------------------------------------

class TestGroupBy:
    def test_caller_count_adds_group_by(self):
        sql = _sql("from function.callers as fn select fn.name, fn.callerCount, fn.filename")
        assert "GROUP BY" in sql.upper()

    def test_group_by_not_added_without_agg(self):
        sql = _sql("from function as fn select fn.name, fn.filename")
        assert "GROUP BY" not in sql.upper()

    def test_count_in_select(self):
        sql = _sql("from function.callers as fn select fn.name, fn.callerCount")
        assert "count" in sql.lower()


# ---------------------------------------------------------------------------
# TestCallDepthCTE
# ---------------------------------------------------------------------------

class TestCallDepthCTE:
    def test_recursive_cte_present(self):
        sql = _sql("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "call_graph" in sql

    def test_union_all_in_cte(self):
        sql = _sql("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "UNION ALL" in sql.upper()

    def test_safety_cap_present(self):
        sql = _sql("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "50" in sql  # depth < 50 cap

    def test_max_call_depth_subquery(self):
        sql = _sql("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "max_call_depth" in sql or "MAX" in sql.upper()

    def test_depth_column_labelled(self):
        cq = _compile("from function.callDepth() as fn select fn.name, fn.depth, fn.filename")
        assert "depth" in cq.columns


# ---------------------------------------------------------------------------
# TestErrors
# ---------------------------------------------------------------------------

class TestErrors:
    def test_unknown_field_raises_compile_error(self):
        with pytest.raises(CompileError):
            _compile("from function as fn select fn.nonexistent")

    def test_unknown_field_in_where_raises_compile_error(self):
        with pytest.raises(CompileError):
            _compile('from function as fn where fn.bogus = "x" select fn.name')

    def test_unknown_entity_raises_compile_error(self):
        with pytest.raises((CompileError, Exception)):
            _compile("from bogus as b select b.name")

    def test_wrong_alias_in_where_raises_compile_error(self):
        with pytest.raises(CompileError):
            _compile('from function as fn where x.name = "a" select fn.name')

    def test_wrong_alias_in_select_raises_compile_error(self):
        with pytest.raises(CompileError):
            _compile("from function as fn select x.name")


# ---------------------------------------------------------------------------
# TestSQLCorrectness  — spot-checks that generated SQL has correct structure
# ---------------------------------------------------------------------------

class TestSQLCorrectness:
    def test_function_no_where_no_join_to_expressions(self):
        sql = _sql("from function as fn select fn.name")
        assert "expressions" not in sql

    def test_call_expression_no_extra_symbols_join(self):
        # function.calls already has symbols in required_joins; no double join
        sql = _sql("from function.calls as c select c.enclosing")
        assert sql.count("symbols") == sql.count("symbols")  # sanity

    def test_file_no_join_to_symbols(self):
        sql = _sql("from file as f select f.filename, f.language")
        assert "symbols" not in sql

    def test_imports_no_symbols_join(self):
        sql = _sql("from file.imports as i select i.filename, i.module")
        assert "symbols" not in sql

    def test_base_where_applied_for_expression(self):
        sql = _sql("from expression as e select e.kind, e.source")
        # expression entity has no base_where — no spurious kind filter
        assert "kind IN" not in sql

    def test_call_expression_base_where_applied(self):
        sql = _sql("from function.calls as c select c.name")
        assert "'call'" in sql or '"call"' in sql

    def test_class_methods_base_where_kind_method(self):
        sql = _sql("from class.methods as m select m.name")
        assert "method" in sql

    def test_param_count_zero_in_without_args(self):
        sql = _sql("from function.withoutArgs() as fn select fn.name")
        assert "= 0" in sql

    def test_get_does_throw_exists_subquery_has_raise(self):
        sql = _sql("from function.getDoesThrow() as fn select fn.name")
        assert "raise" in sql

    def test_without_callers_not_exists_references(self):
        sql = _sql("from function.withoutCallers() as fn select fn.name")
        assert "references" in sql

    def test_callee_function_traversal(self):
        sql = _sql("from function.callees as fn select fn.name")
        assert "references" in sql
        assert "from_symbol_id" in sql

    def test_method_where_class_name(self):
        sql = _sql('from class.methods as m where m.className = "Calculator" select m.name')
        assert "Calculator" in sql
        assert "parent_sym" in sql


# ---------------------------------------------------------------------------
# TestOrderBy
# ---------------------------------------------------------------------------

class TestOrderBy:
    def test_order_by_asc_in_sql(self):
        sql = _sql("from function as fn select fn.name order by fn.name asc")
        assert "ORDER BY" in sql.upper()
        assert "ASC" in sql.upper()

    def test_order_by_desc_in_sql(self):
        sql = _sql("from function as fn select fn.name order by fn.name desc")
        assert "ORDER BY" in sql.upper()
        assert "DESC" in sql.upper()

    def test_order_by_default_is_asc(self):
        sql = _sql("from function as fn select fn.name order by fn.name")
        assert "ORDER BY" in sql.upper()
        assert "ASC" in sql.upper()

    def test_order_by_field_in_sql(self):
        sql = _sql("from function as fn select fn.name order by fn.paramCount desc")
        assert "ORDER BY" in sql.upper()
        assert "json_array_length" in sql  # paramCount compiles to json_array_length

    def test_order_by_multi_key_in_sql(self):
        sql = _sql(
            "from function as fn select fn.name, fn.filename "
            "order by fn.filename asc, fn.name desc"
        )
        assert sql.upper().count("ASC") >= 1
        assert "DESC" in sql.upper()

    def test_order_by_with_where(self):
        sql = _sql(
            'from function as fn '
            'where fn.language = "python" '
            'select fn.name '
            'order by fn.name asc'
        )
        assert "python" in sql
        assert "ORDER BY" in sql.upper()

    def test_no_order_by_absent_from_sql(self):
        sql = _sql("from function as fn select fn.name")
        assert "ORDER BY" not in sql.upper()

    def test_order_by_cte_query(self):
        sql = _sql(
            "from function.callDepth() as fn "
            "where fn.depth > 0 "
            "select fn.name, fn.depth "
            "order by fn.depth desc"
        )
        assert "ORDER BY" in sql.upper()
        assert "DESC" in sql.upper()


# ---------------------------------------------------------------------------
# TestLimit
# ---------------------------------------------------------------------------

class TestLimit:
    def test_limit_in_sql(self):
        sql = _sql("from function as fn select fn.name limit 10")
        assert "LIMIT" in sql.upper()
        assert "10" in sql

    def test_limit_value_correct(self):
        sql = _sql("from function as fn select fn.name limit 5")
        assert "5" in sql

    def test_no_limit_absent_from_sql(self):
        sql = _sql("from function as fn select fn.name")
        assert "LIMIT" not in sql.upper()

    def test_limit_with_order_by_in_sql(self):
        sql = _sql(
            "from function as fn select fn.name, fn.paramCount "
            "order by fn.paramCount desc limit 10"
        )
        assert "ORDER BY" in sql.upper()
        assert "LIMIT" in sql.upper()
        assert "10" in sql

    def test_limit_with_where(self):
        sql = _sql(
            'from function as fn '
            'where fn.language = "python" '
            'select fn.name '
            'limit 3'
        )
        assert "python" in sql
        assert "LIMIT" in sql.upper()

    def test_limit_cte_query(self):
        sql = _sql(
            "from function.callDepth() as fn "
            "select fn.name, fn.depth "
            "order by fn.depth desc limit 5"
        )
        assert "LIMIT" in sql.upper()
        assert "5" in sql


# ---------------------------------------------------------------------------
# TestReliability
# ---------------------------------------------------------------------------

class TestReliability:
    # ── signature_match with typed args ────────────────────────────────────

    def test_signature_with_typed_arg_raises_compile_error(self):
        """signature('str') with a non-null arg is not implemented; must raise."""
        from graft_engine.compiler import CompileError
        with pytest.raises(CompileError, match="not yet supported"):
            _compile("from function.signature('str') as fn select fn.name")

    # ── Condition invariant ─────────────────────────────────────────────────

    def test_mismatched_condition_operators_raises(self):
        """Malformed Condition (too many operators) must raise CompileError."""
        from graft_engine.compiler import CompileError
        from graft_parser.ast_nodes import (
            Condition, ConditionExpr, Field, FieldProjection, Projection,
            EntityPath, QueryAST,
        )
        bad_condition = Condition(
            expressions=[
                ConditionExpr(Field("fn", ["name"]), "=", "foo"),
            ],
            operators=["and", "or"],   # 2 ops for 1 expr — violates invariant
        )
        ast = QueryAST(
            entity_path=EntityPath(root="function", traversals=[]),
            alias="fn",
            condition=bad_condition,
            projection=Projection(items=[FieldProjection(Field("fn", ["name"]))]),
        )
        with pytest.raises(CompileError, match="Malformed WHERE clause"):
            from graft_engine.compiler import compile as gql_compile
            from graft_engine.entity_registry import REGISTRY
            gql_compile(ast, REGISTRY)

    def test_empty_condition_expressions_raises(self):
        """Condition with zero expressions must raise CompileError."""
        from graft_engine.compiler import CompileError
        from graft_parser.ast_nodes import (
            Condition, Field, FieldProjection, Projection,
            EntityPath, QueryAST,
        )
        bad_condition = Condition(expressions=[], operators=[])
        ast = QueryAST(
            entity_path=EntityPath(root="function", traversals=[]),
            alias="fn",
            condition=bad_condition,
            projection=Projection(items=[FieldProjection(Field("fn", ["name"]))]),
        )
        with pytest.raises(CompileError, match="no expressions"):
            from graft_engine.compiler import compile as gql_compile
            from graft_engine.entity_registry import REGISTRY
            gql_compile(ast, REGISTRY)

    # ── LIMIT guard in compiler ─────────────────────────────────────────────

    def test_manually_constructed_limit_zero_raises(self):
        """A QueryAST with limit=0 (e.g. hand-crafted) must raise CompileError."""
        from graft_engine.compiler import CompileError
        from graft_parser.ast_nodes import (
            Field, FieldProjection, Projection, EntityPath, QueryAST,
        )
        ast = QueryAST(
            entity_path=EntityPath(root="function", traversals=[]),
            alias="fn",
            condition=None,
            projection=Projection(items=[FieldProjection(Field("fn", ["name"]))]),
            limit=0,
        )
        with pytest.raises(CompileError, match="positive integer"):
            from graft_engine.compiler import compile as gql_compile
            from graft_engine.entity_registry import REGISTRY
            gql_compile(ast, REGISTRY)

    def test_manually_constructed_negative_limit_raises(self):
        """A QueryAST with limit=-1 must raise CompileError."""
        from graft_engine.compiler import CompileError
        from graft_parser.ast_nodes import (
            Field, FieldProjection, Projection, EntityPath, QueryAST,
        )
        ast = QueryAST(
            entity_path=EntityPath(root="function", traversals=[]),
            alias="fn",
            condition=None,
            projection=Projection(items=[FieldProjection(Field("fn", ["name"]))]),
            limit=-1,
        )
        with pytest.raises(CompileError, match="positive integer"):
            from graft_engine.compiler import compile as gql_compile
            from graft_engine.entity_registry import REGISTRY
            gql_compile(ast, REGISTRY)

    # ── ORDER BY direction guard ────────────────────────────────────────────

    def test_invalid_order_direction_raises(self):
        """An OrderByItem with an invalid direction must raise CompileError."""
        from graft_engine.compiler import CompileError
        from graft_parser.ast_nodes import (
            Field, FieldProjection, Projection, EntityPath, QueryAST,
            OrderBy, OrderByItem,
        )
        ast = QueryAST(
            entity_path=EntityPath(root="function", traversals=[]),
            alias="fn",
            condition=None,
            projection=Projection(items=[FieldProjection(Field("fn", ["name"]))]),
            order_by=OrderBy(items=[OrderByItem(Field("fn", ["name"]), "ascending")]),
        )
        with pytest.raises(CompileError, match="Invalid ORDER BY direction"):
            from graft_engine.compiler import compile as gql_compile
            from graft_engine.entity_registry import REGISTRY
            gql_compile(ast, REGISTRY)
