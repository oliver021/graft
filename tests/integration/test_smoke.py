"""
Smoke tests — single happy-path run of the complete pipeline.

If anything here fails nothing else matters.
"""

import pytest
import allure

from graft_engine.executor import QueryResult


@allure.feature("Pipeline")
@allure.story("Smoke")
@pytest.mark.smoke
@pytest.mark.integration
class TestPipelineSmoke:

    @allure.title("Full pipeline completes and returns at least one row")
    @allure.severity(allure.severity_level.BLOCKER)
    def test_full_pipeline_returns_results(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert result.row_count > 0
        assert len(result.rows) == result.row_count

    @allure.title("QueryResult has all required fields with correct types")
    @allure.severity(allure.severity_level.BLOCKER)
    def test_query_result_shape(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert isinstance(result, QueryResult)
        assert isinstance(result.columns, list)
        assert isinstance(result.rows, list)
        assert isinstance(result.query_sql, str)
        assert isinstance(result.row_count, int)
        assert isinstance(result.elapsed_ms, float)

    @allure.title("Generated SQL is non-empty and contains SELECT … FROM")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_generated_sql_present(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert "SELECT" in result.query_sql.upper()
        assert "FROM" in result.query_sql.upper()

    @allure.title("Result rows are dicts keyed by the projected column names")
    def test_rows_are_dicts_keyed_by_columns(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert "name" in result.columns
        for row in result.rows:
            assert isinstance(row, dict)
            assert "name" in row

    @allure.title("elapsed_ms is a positive number")
    def test_elapsed_ms_is_positive(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        assert result.elapsed_ms >= 0

    @allure.title("to_json() returns a valid non-empty JSON string")
    def test_to_json_serializes(self, graft_query):
        import json
        result = graft_query("from function as fn select fn.name")
        data = json.loads(result.to_json())
        assert isinstance(data, list)
        assert len(data) == result.row_count

    @allure.title("to_csv() returns a string with a header row")
    def test_to_csv_has_header(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        lines = result.to_csv().splitlines()
        assert lines[0] == "name"

    @allure.title("to_table() returns a non-empty ASCII table string")
    def test_to_table_non_empty(self, graft_query):
        result = graft_query("from function as fn select fn.name")
        table = result.to_table()
        assert isinstance(table, str)
        assert len(table) > 0
