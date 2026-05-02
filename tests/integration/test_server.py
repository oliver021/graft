"""
HTTP server integration tests.

Uses Starlette's TestClient — no port binding, fully in-process.
A module-scoped SQLite DB is pre-loaded with the test corpus so server
tests exercise the full index → serve → query pipeline.
"""

from __future__ import annotations

import json
import pytest
import allure
import sqlalchemy as sa
from fastapi.testclient import TestClient

from code_indexer.indexer import scan, resolve_references
from code_indexer.schema import create_all
from graft_server.app import create_app

from .corpus import (
    CORPUS_MATH_PY, CORPUS_MATH_PATH,
    CORPUS_PROC_PY, CORPUS_PROC_PATH,
    EXPECTED,
)


# ---------------------------------------------------------------------------
# Module-scoped fixture: server backed by a real indexed in-memory DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """
    Build a temp SQLite database, index the test corpus into it, then
    spin up the FastAPI app pointed at that DB.  Returns a TestClient.

    Uses a file-based SQLite (not :memory:) because the TestClient and
    create_engine run in the same process but different call paths.
    """
    db_path = tmp_path_factory.mktemp("server_db") / "test.db"
    db_url  = f"sqlite:///{db_path}"

    engine = sa.create_engine(db_url)
    create_all(engine)
    scan(CORPUS_MATH_PY, "python", CORPUS_MATH_PATH).insert_into(engine)
    scan(CORPUS_PROC_PY, "python", CORPUS_PROC_PATH).insert_into(engine)
    resolve_references(engine)
    engine.dispose()

    app = create_app(db_url=db_url)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

@allure.feature("HTTP Server")
@allure.story("Status Endpoint")
@pytest.mark.server
@pytest.mark.integration
class TestStatusEndpoint:

    @allure.title("GET /status returns HTTP 200")
    def test_get_status_200(self, client):
        resp = client.get("/status")
        assert resp.status_code == 200

    @allure.title("GET /status body contains status: ok")
    def test_get_status_ok(self, client):
        resp = client.get("/status")
        body = resp.json()
        assert body["status"] == "ok"

    @allure.title("GET /status body contains db key")
    def test_get_status_has_db_key(self, client):
        resp = client.get("/status")
        body = resp.json()
        assert "db" in body


# ---------------------------------------------------------------------------
# POST /query — happy path
# ---------------------------------------------------------------------------

@allure.feature("HTTP Server")
@allure.story("Query Endpoint — Happy Path")
@pytest.mark.server
@pytest.mark.integration
class TestQueryEndpoint:

    @allure.title("POST /query returns HTTP 200 for a valid query")
    def test_post_query_200(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "json",
        })
        allure.attach(resp.text, name="Response Body", attachment_type=allure.attachment_type.JSON)
        assert resp.status_code == 200

    @allure.title("POST /query response has columns and rows")
    def test_post_query_has_columns_and_rows(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "json",
        })
        body = resp.json()
        assert "columns" in body
        assert "rows" in body
        assert body["row_count"] == EXPECTED["total_functions"]

    @allure.title("POST /query response includes generated SQL")
    def test_post_query_has_query_sql(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "json",
        })
        body = resp.json()
        assert "query_sql" in body
        assert "SELECT" in body["query_sql"].upper()

    @allure.title("POST /query response includes elapsed_ms")
    def test_post_query_has_elapsed_ms(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "json",
        })
        body = resp.json()
        assert "elapsed_ms" in body
        assert body["elapsed_ms"] >= 0

    @allure.title("POST /query with format=csv returns CSV text")
    def test_post_query_csv_format(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "csv",
        })
        assert resp.status_code == 200
        # CSV response is plain text, first line is the header
        text = resp.text.strip('"')   # FastAPI returns plain string as JSON string
        assert "name" in text

    @allure.title("POST /query with format=table returns ASCII table text")
    def test_post_query_table_format(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "table",
        })
        assert resp.status_code == 200
        assert resp.text  # non-empty

    @allure.title("POST /query results match direct graft_query results")
    def test_post_query_results_match_direct(self, client, graft_query):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name order by fn.name asc",
            "format": "json",
        })
        http_names = [r["name"] for r in resp.json()["rows"]]

        direct = graft_query(
            "from function as fn select fn.name order by fn.name asc"
        )
        direct_names = [r["name"] for r in direct.rows]

        assert http_names == direct_names


# ---------------------------------------------------------------------------
# POST /query — error paths
# ---------------------------------------------------------------------------

@allure.feature("HTTP Server")
@allure.story("Query Endpoint — Error Handling")
@pytest.mark.server
@pytest.mark.integration
class TestQueryEndpointErrors:

    @allure.title("POST /query with invalid GQL returns HTTP 400")
    @allure.severity(allure.severity_level.CRITICAL)
    def test_bad_gql_returns_400(self, client):
        resp = client.post("/query", json={
            "query": "this is not gql",
            "format": "json",
        })
        assert resp.status_code == 400

    @allure.title("400 response body contains a readable error message")
    def test_bad_gql_error_message(self, client):
        resp = client.post("/query", json={
            "query": "SELECT * FROM function",
            "format": "json",
        })
        body = resp.json()
        assert "detail" in body
        assert len(body["detail"]) > 0

    @allure.title("POST /query with unknown format returns HTTP 400")
    def test_unknown_format_returns_400(self, client):
        resp = client.post("/query", json={
            "query": "from function as fn select fn.name",
            "format": "xml",
        })
        assert resp.status_code == 400

    @allure.title("POST /query with empty query string returns HTTP 400")
    def test_empty_query_returns_400(self, client):
        resp = client.post("/query", json={
            "query": "",
            "format": "json",
        })
        assert resp.status_code == 400
