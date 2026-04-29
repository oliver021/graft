#!/usr/bin/env python
"""
Graft CLI — index code, query with GQL, serve HTTP.

Usage:
    graft index <path>                 -- index a directory
    graft query "<gql>"                -- run a GQL query
    graft query --file audit.gql       -- run from file
    graft serve                        -- start HTTP server
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import typer
import sqlalchemy as sa
from rich.console import Console

# Wire up imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parser"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code_indexer"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from code_indexer.schema import create_all
from code_indexer.indexer import scan_dir
from graft_parser._parser import parse
from graft_engine.compiler import compile
from graft_engine.executor import run
from graft_server.app import create_app


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(help="Graft — code insight engine")
console = Console()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def index(
    path: str = typer.Argument(..., help="Directory to index"),
    db: str = typer.Option("sqlite:///index.db", help="Database URL"),
    language: str = typer.Option("python", help="Language filter (python, javascript, etc.)"),
) -> None:
    """Index a directory of source code."""
    path = Path(path).resolve()
    if not path.is_dir():
        console.print(f"[red]Error: {path} is not a directory[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Indexing {path}...[/cyan]")
    engine = sa.create_engine(db)
    create_all(engine)

    try:
        files, symbols, expressions, references = scan_dir(str(path), engine, language=language)
        console.print(
            f"[green]✓ Indexed {files} files, {symbols} symbols, {expressions} expressions, {references} references[/green]"
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def query(
    q: str = typer.Argument(None, help="GQL query string"),
    file: str = typer.Option(None, help="Read query from file"),
    db: str = typer.Option("sqlite:///index.db", help="Database URL"),
    format: str = typer.Option("table", help="Output format (table, json, csv)"),
) -> None:
    """Run a GQL query."""
    if file:
        try:
            q = Path(file).read_text()
        except FileNotFoundError:
            console.print(f"[red]Error: File not found: {file}[/red]")
            raise typer.Exit(1)
    elif not q:
        console.print("[red]Error: Provide a query string or --file[/red]")
        raise typer.Exit(1)

    engine = sa.create_engine(db)

    try:
        ast = parse(q)
        compiled = compile(ast)
        result = run(compiled, engine)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if format == "table":
        console.print(result.to_table())
    elif format == "json":
        console.print(result.to_json())
    elif format == "csv":
        console.print(result.to_csv())
    else:
        console.print(f"[red]Unknown format: {format}[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]{result.elapsed_ms}ms | {result.row_count} rows[/dim]")


@app.command()
def serve(
    port: int = typer.Option(8000, help="HTTP port"),
    db: str = typer.Option("sqlite:///index.db", help="Database URL"),
) -> None:
    """Start the HTTP server."""
    import uvicorn

    console.print(f"[cyan]Starting Graft server on http://127.0.0.1:{port}[/cyan]")
    console.print(f"[cyan]Database: {db}[/cyan]")
    console.print("[dim]POST /query with {\"query\": \"...\", \"format\": \"json\"}[/dim]")
    console.print("[dim]GET /status[/dim]")
    console.print()

    graft_app = create_app(db)
    uvicorn.run(graft_app, host="127.0.0.1", port=port, log_level="info")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
