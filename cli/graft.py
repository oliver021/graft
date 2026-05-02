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

from pathlib import Path

import typer
import sqlalchemy as sa
from rich.console import Console

from code_indexer.schema import create_all
from code_indexer.indexer import scan_dir, resolve_references, SkippedFile, ScanError
from code_indexer.models import ScanResult
from graft_parser._parser import parse
from graft_engine.compiler import compile
from graft_engine.executor import run
from graft_engine.entity_registry import REGISTRY
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
        languages = [language] if language else None
        file_count = skipped_count = error_count = 0
        for result in scan_dir(str(path), languages=languages):
            if isinstance(result, ScanResult):
                result.insert_into(engine, replace=True)
                file_count += 1
            elif isinstance(result, SkippedFile):
                skipped_count += 1
            elif isinstance(result, ScanError):
                console.print(f"[yellow]Warning: {result.path}: {result.reason}[/yellow]")
                error_count += 1
        resolve_references(engine)
        console.print(
            f"[green]OK Indexed {file_count} files[/green]"
            + (f" [dim]({skipped_count} skipped, {error_count} errors)[/dim]" if skipped_count or error_count else "")
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


@app.command()
def entities() -> None:
    """List all queryable root entities (use as FROM targets in GQL)."""
    from rich.table import Table

    table = Table(title="Root entities", show_header=True, header_style="bold cyan")
    table.add_column("entity", style="bold")
    table.add_column("traversals")
    table.add_column("predicates")

    for name in REGISTRY.root_names():
        entity = REGISTRY.get(name)
        traversals = ", ".join(sorted(entity.traversals)) or "-"
        predicates = ", ".join(f"{p}()" for p in sorted(entity.predicates)) or "-"
        table.add_row(name, traversals, predicates)

    console.print(table)
    console.print(
        "[dim]Run [bold]graft fields <entity>[/bold] to see available SELECT fields.[/dim]"
    )


@app.command()
def fields(
    entity: str = typer.Argument(..., help="Entity name, e.g. 'function' or 'function.calls'"),
) -> None:
    """List all fields available to SELECT for an entity or traversal path."""
    from rich.table import Table
    from graft_parser._parser import parse as gql_parse
    from graft_parser.ast_nodes import EntityPath, FieldTraversal

    # Build a minimal dummy query to resolve the path through the registry
    dummy = f"from {entity} as x select x.name"
    try:
        ast = gql_parse(dummy)
        resolved = REGISTRY.resolve_path(ast.entity_path)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    terminal = resolved.terminal
    all_fields = {**terminal.fields, **resolved.injected_fields}

    table = Table(
        title=f"Fields on [bold]{entity}[/bold]",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("field")
    table.add_column("type")
    table.add_column("description")

    for gql_name, fdef in sorted(all_fields.items()):
        ftype = getattr(fdef, "sql_type", None)
        type_label = ftype.__class__.__name__ if ftype is not None else "text"
        desc = getattr(fdef, "description", "") or ""
        table.add_row(gql_name, type_label, desc)

    console.print(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
