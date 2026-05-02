"""
Integration test fixtures — shared across all tests/integration/ modules.

indexed_engine  (session) — in-memory SQLite with both corpus files indexed
                             and references resolved.  Built once per session.
graft_query     (session) — callable that runs parse→compile→execute and
                             attaches SQL + result table to the Allure report.
"""

from __future__ import annotations

import allure
import pytest
import sqlalchemy as sa

from code_indexer.indexer import scan, resolve_references
from code_indexer.schema import create_all
from graft_parser import parse
from graft_engine import compile, run
from graft_engine.executor import QueryResult

from .corpus import (
    CORPUS_MATH_PY, CORPUS_MATH_PATH,
    CORPUS_PROC_PY, CORPUS_PROC_PATH,
)


# ---------------------------------------------------------------------------
# Session-scoped engine: index the full corpus once, reuse across all tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def indexed_engine() -> sa.Engine:
    """
    Build an in-memory SQLite database from the test corpus.

    Steps performed once per pytest session:
      1. create_engine("sqlite:///:memory:")
      2. create_all(engine)              — schema
      3. scan(CORPUS_MATH_PY, ...)       — index math_utils.py
         .insert_into(engine)
      4. scan(CORPUS_PROC_PY, ...)       — index processor.py
         .insert_into(engine)
      5. resolve_references(engine)      — link callee names → symbol IDs
    """
    engine = sa.create_engine("sqlite:///:memory:")
    create_all(engine)

    with allure.step("Index corpus: math_utils.py"):
        scan(CORPUS_MATH_PY, "python", CORPUS_MATH_PATH).insert_into(engine)

    with allure.step("Index corpus: processor.py"):
        scan(CORPUS_PROC_PY, "python", CORPUS_PROC_PATH).insert_into(engine)

    with allure.step("Resolve cross-file references"):
        resolved = resolve_references(engine)
        allure.attach(
            f"Resolved {resolved} reference(s)",
            name="Reference resolution",
            attachment_type=allure.attachment_type.TEXT,
        )

    return engine


# ---------------------------------------------------------------------------
# Session-scoped query helper
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def graft_query(indexed_engine: sa.Engine):
    """
    Return a callable  graft_query(gql: str) -> QueryResult  that:
      - parses the GQL string
      - compiles it against the default REGISTRY
      - executes it against indexed_engine
      - attaches the generated SQL and ASCII result table to the Allure report

    Usage in tests:
        result = graft_query("from function as fn select fn.name")
        assert result.row_count > 0
    """
    def _run(gql: str) -> QueryResult:
        with allure.step(f"Parse: {gql[:80]}"):
            ast = parse(gql)

        with allure.step("Compile to SQL"):
            compiled = compile(ast)

        with allure.step("Execute against indexed DB"):
            result = run(compiled, indexed_engine)

        allure.attach(
            result.query_sql,
            name="Generated SQL",
            attachment_type=allure.attachment_type.TEXT,
        )
        allure.attach(
            result.to_table(),
            name="Result Table",
            attachment_type=allure.attachment_type.TEXT,
        )
        return result

    return _run
