"""
Minimal FastAPI server for Graft.

Single endpoint: POST /query
"""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
import sqlalchemy as sa

from graft_parser._parser import parse, GraftParseError
from graft_engine.compiler import compile, CompileError
from graft_engine.executor import run, ExecuteError


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    format: str = "json"  # json, csv, table


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    query_sql: str
    row_count: int
    elapsed_ms: float


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app(db_url: str = "sqlite:///index.db") -> FastAPI:
    """Create and configure the FastAPI app."""
    app = FastAPI(title="Graft", version="0.1.0")
    engine = sa.create_engine(db_url)

    @app.post("/query")
    async def query_endpoint(req: QueryRequest) -> QueryResponse | str:
        """Execute a GQL query and return results."""
        try:
            ast = parse(req.query)
            compiled = compile(ast)
            result = run(compiled, engine)
        except GraftParseError as e:
            raise HTTPException(status_code=400, detail=f"Parse error: {e.message}")
        except CompileError as e:
            raise HTTPException(status_code=400, detail=f"Compile error: {str(e)}")
        except ExecuteError as e:
            raise HTTPException(status_code=500, detail=f"Execute error: {str(e)}")

        if req.format == "json":
            return QueryResponse(
                columns=result.columns,
                rows=result.rows,
                query_sql=result.query_sql,
                row_count=result.row_count,
                elapsed_ms=result.elapsed_ms,
            )
        elif req.format == "csv":
            return result.to_csv()
        elif req.format == "table":
            return result.to_table()
        else:
            raise HTTPException(status_code=400, detail=f"Unknown format '{req.format}'")

    @app.get("/status")
    async def status() -> dict:
        """Health check."""
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return {"status": "ok", "db": db_url}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    return app


if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)
