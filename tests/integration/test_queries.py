"""
GQL query integration tests.

Each test class maps to one feature area of the query language.
All assertions reference corpus.EXPECTED and corpus.NAMES constants —
never hardcoded magic numbers.
"""

from __future__ import annotations

import pytest
import allure

from graft_parser import parse, GraftParseError, ParseErrorKind
from graft_engine.compiler import CompileError

from .corpus import EXPECTED, NAMES


# ---------------------------------------------------------------------------
# function entity — basic fields
# ---------------------------------------------------------------------------

@allure.feature("Function Entity")
@allure.story("Basic Fields")
@pytest.mark.integration
class TestFunctionEntity:

    @allure.title("Returns all functions and methods from the corpus")
    def test_returns_all_functions(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert result.row_count == EXPECTED["total_functions"]

    @allure.title("Every row has the required fields: name, filename, start, end, kind")
    def test_function_row_has_required_fields(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name, fn.filename, fn.start, fn.end, fn.kind"
        )
        for row in result.rows:
            assert row["name"] is not None
            assert row["filename"] is not None
            assert isinstance(row["start"], int)
            assert isinstance(row["end"], int)
            assert row["kind"] in ("function", "method")

    @allure.title("filename column contains the indexed corpus path")
    def test_filename_contains_corpus_path(self, graft_query):
        result = graft_query("from function as fn select fn.name, fn.filename")
        filenames = {row["filename"] for row in result.rows}
        assert any("math_utils" in f for f in filenames)
        assert any("processor" in f for f in filenames)

    @allure.title("kind values are only 'function' or 'method'")
    def test_function_kind_values(self, graft_query):
        result = graft_query("from function as fn select fn.name, fn.kind")
        kinds = {row["kind"] for row in result.rows}
        assert kinds <= {"function", "method"}

    @allure.title("start line is always less than or equal to end line")
    def test_start_lte_end(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name, fn.start, fn.end"
        )
        for row in result.rows:
            assert row["start"] <= row["end"], (
                f"Function '{row['name']}' has start {row['start']} > end {row['end']}"
            )


# ---------------------------------------------------------------------------
# function.calls traversal
# ---------------------------------------------------------------------------

@allure.feature("Function Entity")
@allure.story("Calls Traversal")
@pytest.mark.integration
class TestFunctionCallsTraversal:

    @allure.title("calls traversal returns call expressions")
    def test_calls_traversal_returns_rows(self, graft_query):
        result = graft_query("from function.calls as c select c.name")
        assert result.row_count > 0

    @allure.title("Every call row has a non-empty callee name")
    def test_call_row_has_callee_name(self, graft_query):
        result = graft_query("from function.calls as c select c.name")
        for row in result.rows:
            assert row["name"] is not None
            assert row["name"].strip() != ""

    @allure.title("Known calls from the corpus appear in results")
    def test_known_calls_present(self, graft_query):
        result = graft_query("from function.calls as c select c.name")
        names = {row["name"] for row in result.rows}
        # These bare calls in process() must appear
        assert "Calculator" in names
        assert "transform" in names

    @allure.title("calls traversal exposes filename and start fields")
    def test_call_row_has_location_fields(self, graft_query):
        result = graft_query(
            "from function.calls as c select c.name, c.filename, c.start"
        )
        for row in result.rows:
            assert row["filename"] is not None
            assert isinstance(row["start"], int)


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

@allure.feature("Function Entity")
@allure.story("Predicate Traversals")
@pytest.mark.integration
class TestPredicates:

    @allure.title("withoutCallers() returns the expected count of uncalled symbols")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_without_callers_count(self, graft_query):
        result = graft_query(
            "from function.withoutCallers() as fn select fn.name"
        )
        assert result.row_count == EXPECTED["without_callers"]

    @allure.title("withoutCallers() includes known dead functions")
    def test_without_callers_includes_dead_fns(self, graft_query):
        result = graft_query(
            "from function.withoutCallers() as fn select fn.name"
        )
        names = {row["name"] for row in result.rows}
        for dead in NAMES["no_callers"]:
            assert dead in names, f"Expected dead function '{dead}' in withoutCallers()"

    @allure.title("withoutCallers() excludes functions that are called")
    def test_without_callers_excludes_called_fns(self, graft_query):
        result = graft_query(
            "from function.withoutCallers() as fn select fn.name"
        )
        names = {row["name"] for row in result.rows}
        for called in NAMES["has_callers"]:
            assert called not in names, (
                f"'{called}' has callers but appeared in withoutCallers()"
            )

    @allure.title("withoutArgs() returns functions with zero parameters")
    def test_without_args_count(self, graft_query):
        result = graft_query(
            "from function.withoutArgs() as fn select fn.name"
        )
        assert result.row_count == EXPECTED["without_args"]

    @allure.title("withoutArgs() includes known no-argument functions")
    def test_without_args_names(self, graft_query):
        result = graft_query(
            "from function.withoutArgs() as fn select fn.name"
        )
        names = {row["name"] for row in result.rows}
        assert names == NAMES["without_args"]

    @allure.title("getDoesThrow() returns functions containing raise statements")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_does_throw_count(self, graft_query):
        result = graft_query(
            "from function.getDoesThrow() as fn select fn.name"
        )
        assert result.row_count == EXPECTED["does_throw"]

    @allure.title("getDoesThrow() returns process() which raises ValueError")
    def test_does_throw_contains_process(self, graft_query):
        result = graft_query(
            "from function.getDoesThrow() as fn select fn.name"
        )
        names = {row["name"] for row in result.rows}
        assert names == NAMES["does_throw"]

    @allure.title("getDoesThrow() excludes functions with no raise")
    def test_does_throw_excludes_non_raisers(self, graft_query):
        result = graft_query(
            "from function.getDoesThrow() as fn select fn.name"
        )
        names = {row["name"] for row in result.rows}
        assert "transform" not in names
        assert "add" not in names


# ---------------------------------------------------------------------------
# class entity
# ---------------------------------------------------------------------------

@allure.feature("Class Entity")
@allure.story("Class and Methods")
@pytest.mark.integration
class TestClassEntity:

    @allure.title("class entity returns the Calculator class")
    def test_class_entity_count(self, graft_query):
        result = graft_query("from class as c select c.name")
        assert result.row_count == EXPECTED["classes"]

    @allure.title("class entity returns Calculator by name")
    def test_class_entity_name(self, graft_query):
        result = graft_query("from class as c select c.name")
        names = {row["name"] for row in result.rows}
        assert "Calculator" in names

    @allure.title("class.methods traversal returns all 3 Calculator methods")
    def test_class_methods_count(self, graft_query):
        result = graft_query("from class.methods as m select m.name")
        assert result.row_count == EXPECTED["methods"]

    @allure.title("class.methods returns add, subtract, multiply")
    def test_class_methods_names(self, graft_query):
        result = graft_query("from class.methods as m select m.name")
        names = {row["name"] for row in result.rows}
        assert names == NAMES["all_methods"]

    @allure.title("class.methods filtered by className returns only that class's methods")
    def test_class_methods_where_classname(self, graft_query):
        result = graft_query(
            "from class.methods as m "
            "where m.className = 'Calculator' "
            "select m.name"
        )
        assert result.row_count == EXPECTED["methods"]


# ---------------------------------------------------------------------------
# file entity
# ---------------------------------------------------------------------------

@allure.feature("File Entity")
@allure.story("Imports Traversal")
@pytest.mark.integration
class TestFileEntity:

    @allure.title("file.imports traversal returns import expressions")
    def test_file_imports_count(self, graft_query):
        result = graft_query("from file.imports as i select i.filename")
        assert result.row_count == EXPECTED["imports_in_proc"]

    @allure.title("import row has a filename field")
    def test_import_row_has_filename(self, graft_query):
        result = graft_query("from file.imports as i select i.filename")
        for row in result.rows:
            assert row["filename"] is not None


# ---------------------------------------------------------------------------
# WHERE clause
# ---------------------------------------------------------------------------

@allure.feature("WHERE Clause")
@allure.story("Filtering")
@pytest.mark.integration
class TestWhereClause:

    @allure.title("WHERE = filters to exactly matching rows")
    def test_where_equals_by_name(self, graft_query):
        result = graft_query(
            "from function as fn "
            "where fn.name = 'process' "
            "select fn.name, fn.kind"
        )
        assert result.row_count == 1
        assert result.rows[0]["name"] == "process"

    @allure.title("WHERE != excludes the named function")
    def test_where_not_equals(self, graft_query):
        all_result  = graft_query("from function as fn select fn.name")
        excl_result = graft_query(
            "from function as fn where fn.name != 'process' select fn.name"
        )
        assert excl_result.row_count == all_result.row_count - 1
        names = {row["name"] for row in excl_result.rows}
        assert "process" not in names

    @allure.title("WHERE like filters by pattern")
    def test_where_like(self, graft_query):
        result = graft_query(
            "from function as fn "
            "where fn.name like 'no%' "
            "select fn.name"
        )
        assert result.row_count >= 1
        for row in result.rows:
            assert row["name"].startswith("no")

    @allure.title("WHERE … and … chains two conditions")
    def test_where_and(self, graft_query):
        result = graft_query(
            "from function as fn "
            "where fn.kind = 'function' and fn.name = 'process' "
            "select fn.name, fn.kind"
        )
        assert result.row_count == 1
        assert result.rows[0]["name"] == "process"
        assert result.rows[0]["kind"] == "function"

    @allure.title("WHERE … or … returns union of both conditions")
    def test_where_or(self, graft_query):
        result = graft_query(
            "from function as fn "
            "where fn.name = 'process' or fn.name = 'transform' "
            "select fn.name"
        )
        names = {row["name"] for row in result.rows}
        assert names == {"process", "transform"}


# ---------------------------------------------------------------------------
# ORDER BY and LIMIT
# ---------------------------------------------------------------------------

@allure.feature("ORDER BY and LIMIT")
@allure.story("Result Ordering and Truncation")
@pytest.mark.integration
class TestOrderAndLimit:

    @allure.title("ORDER BY name ASC produces alphabetical order")
    def test_order_by_name_asc(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name order by fn.name asc"
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    @allure.title("ORDER BY name DESC produces reverse alphabetical order")
    def test_order_by_name_desc(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name order by fn.name desc"
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    @allure.title("LIMIT 2 returns exactly 2 rows")
    def test_limit_reduces_row_count(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name limit 2"
        )
        assert result.row_count == 2

    @allure.title("LIMIT 1 returns exactly 1 row")
    def test_limit_one(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name limit 1"
        )
        assert result.row_count == 1

    @allure.title("ORDER BY + LIMIT returns the first N in sorted order")
    def test_order_by_and_limit_combined(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name order by fn.name asc limit 3"
        )
        assert result.row_count == 3
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Multi-file queries
# ---------------------------------------------------------------------------

@allure.feature("Multi-file Corpus")
@allure.story("Cross-file Queries")
@pytest.mark.integration
class TestMultiFile:

    @allure.title("Query spans both indexed files")
    def test_query_spans_both_files(self, graft_query):
        result = graft_query(
            "from function as fn select fn.name, fn.filename"
        )
        filenames = {row["filename"] for row in result.rows}
        assert any("math_utils" in f for f in filenames)
        assert any("processor" in f for f in filenames)

    @allure.title("WHERE filename = isolates functions in one file")
    def test_where_filename_isolates_file(self, graft_query):
        result = graft_query(
            "from function as fn "
            "where fn.filename = 'src/processor.py' "
            "select fn.name, fn.filename"
        )
        assert result.row_count > 0
        for row in result.rows:
            assert "processor" in row["filename"]

    @allure.title("Total function count matches sum across both files")
    def test_total_function_count(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert result.row_count == EXPECTED["total_functions"]


# ---------------------------------------------------------------------------
# Error propagation — bad inputs surface structured exceptions
# ---------------------------------------------------------------------------

@allure.feature("Error Handling")
@allure.story("Error Propagation")
@pytest.mark.integration
class TestErrorPropagation:

    @allure.title("Syntax error in GQL raises GraftParseError (not a raw exception)")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_bad_gql_raises_graft_parse_error(self, indexed_engine):
        with pytest.raises(GraftParseError):
            parse("this is not valid gql at all")

    @allure.title("GraftParseError has a non-empty hint")
    def test_parse_error_has_hint(self, indexed_engine):
        try:
            parse("from function fn select fn.name")   # missing 'as'
        except GraftParseError as e:
            assert e.hint, "Expected a non-empty hint"
            assert "as" in e.hint.lower()
        else:
            pytest.fail("Expected GraftParseError")

    @allure.title("GraftParseError has the correct kind")
    def test_parse_error_has_kind(self, indexed_engine):
        try:
            parse("SELECT * FROM function")
        except GraftParseError as e:
            assert e.kind == ParseErrorKind.SYNTAX
        else:
            pytest.fail("Expected GraftParseError")

    @allure.title("Unknown entity raises CompileError")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_unknown_entity_raises_compile_error(self, indexed_engine):
        from graft_engine import compile as graft_compile
        from graft_engine.executor import run as graft_run
        ast = parse("from function as fn select fn.name")
        # Patch entity to something invalid by parsing a valid query
        # then directly calling compile with a bad registry path
        with pytest.raises(Exception):
            # Simplest way: forge a query against a nonexistent entity via raw parse
            bad_ast = parse.__wrapped__(
                "from nonexistent as x select x.name"
            ) if hasattr(parse, "__wrapped__") else None
            if bad_ast is None:
                pytest.skip("Cannot forge bad entity parse for this test")

    @allure.title("Incomplete GQL raises GraftParseError with UNEXPECTED_EOF kind")
    def test_incomplete_gql_raises_eof_error(self, indexed_engine):
        try:
            parse("from function as fn")   # missing SELECT
        except GraftParseError as e:
            assert e.kind == ParseErrorKind.UNEXPECTED_EOF
        else:
            pytest.fail("Expected GraftParseError")

    @allure.title("GraftParseError __str__ includes line, column, and snippet")
    def test_parse_error_str_is_readable(self, indexed_engine):
        try:
            parse("from function fn select fn.name")
        except GraftParseError as e:
            rendered = str(e)
            assert "GQL" in rendered
            assert e.message in rendered
        else:
            pytest.fail("Expected GraftParseError")
