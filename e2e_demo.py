#!/usr/bin/env python
"""
Complete end-to-end demo: create sample code → index → query → results.

Run: python e2e_demo.py
"""

import sys
import os
from pathlib import Path
import json

# Setup paths
root = Path(__file__).parent
sys.path.insert(0, str(root / "engine"))
sys.path.insert(0, str(root / "parser"))
sys.path.insert(0, str(root / "code_indexer"))

import sqlalchemy as sa
from code_indexer.schema import create_all, files_table, symbols_table, expressions_table, references_table
from code_indexer.indexer import scan
from graft_parser._parser import parse
from graft_engine.compiler import compile
from graft_engine.executor import run


# ---------------------------------------------------------------------------
# Sample code
# ---------------------------------------------------------------------------

SAMPLE_CODE = '''
class DataProcessor:
    """Process data from various sources."""

    def __init__(self, name):
        self.name = name

    def validate(self, data):
        """Check if data is valid."""
        if not data:
            raise ValueError("data cannot be empty")
        return True

    def transform(self, data):
        """Transform data format."""
        result = self.validate(data)
        return result

    def _internal_check(self):
        """Internal helper."""
        return True


def load_data(filename):
    """Load data from file."""
    with open(filename) as f:
        data = f.read()
    print(data)
    return data


def unused_function():
    """Never called."""
    pass
'''

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

print("=" * 70)
print("GRAFT END-TO-END DEMO")
print("=" * 70)
print()

# Create DB
db_url = "sqlite:///demo.db"
db_path = root / "demo.db"
if db_path.exists():
    db_path.unlink()

engine = sa.create_engine(db_url)
create_all(engine)

# Index the sample code
print("[*] Indexing sample code...")
result = scan(SAMPLE_CODE, language="python", path="sample.py")

print(f"    [OK] {len(result.symbols)} symbols")
print(f"    [OK] {len(result.expressions)} expressions")
print(f"    [OK] {len(result.references)} references")
print()

# Insert into DB
with engine.begin() as conn:
    if result.file:
        conn.execute(
            files_table.insert(),
            [{
                "id": result.file.id,
                "path": result.file.path,
                "language": result.file.language,
                "content_hash": result.file.content_hash,
                "scanned_at": result.file.scanned_at,
            }],
        )
    if result.symbols:
        conn.execute(
            symbols_table.insert(),
            [
                {
                    "id": s.id,
                    "file_id": s.file_id,
                    "name": s.name,
                    "kind": s.kind,
                    "start_byte": s.start_byte,
                    "end_byte": s.end_byte,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "signature": json.dumps(s.signature) if s.signature else None,
                    "parent_id": s.parent_id,
                }
                for s in result.symbols
            ],
        )
    if result.expressions:
        conn.execute(
            expressions_table.insert(),
            [
                {
                    "id": e.id,
                    "file_id": e.file_id,
                    "symbol_id": e.symbol_id,
                    "parent_id": e.parent_id,
                    "kind": e.kind,
                    "source_text": e.source_text,
                    "start_byte": e.start_byte,
                    "end_byte": e.end_byte,
                    "start_line": e.start_line,
                    "end_line": e.end_line,
                    "depth": e.depth,
                    "extra": json.dumps(e.extra) if e.extra else None,
                }
                for e in result.expressions
            ],
        )
    if result.references:
        conn.execute(
            references_table.insert(),
            [
                {
                    "id": r.id,
                    "file_id": r.file_id,
                    "expression_id": r.expression_id,
                    "from_symbol_id": r.from_symbol_id,
                    "callee_name": r.callee_name,
                    "to_symbol_id": r.to_symbol_id,
                }
                for r in result.references
            ],
        )

print()

# ---------------------------------------------------------------------------
# Run queries
# ---------------------------------------------------------------------------

print("=" * 70)
print("RUNNING QUERIES")
print("=" * 70)
print()

queries = [
    ("All functions and methods",
     "from function as fn select fn.name, fn.filename, fn.start"),

    ("Functions that throw/raise",
     "from function.getDoesThrow() as fn select fn.name, fn.filename"),

    ("Functions never called",
     "from function.withoutCallers() as fn select fn.name, fn.filename"),

    ("Methods in DataProcessor class",
     "from class.methods as m select m.name, m.className, m.start"),

    ("All function calls",
     "from function.calls as c select c.enclosing, c.name, c.filename"),
]

for title, query in queries:
    print(f"[QUERY] {title}")
    print(f"   {query}")
    print()

    try:
        result = run(compile(parse(query)), engine)
        print(result.to_table())
        print(f"    [{result.elapsed_ms}ms]")
        print()
    except Exception as e:
        print(f"    [ERROR] {e}")
        import traceback
        traceback.print_exc()
        print()

# ---------------------------------------------------------------------------
# Show SQL
# ---------------------------------------------------------------------------

print("=" * 70)
print("COMPILED SQL EXAMPLE")
print("=" * 70)
print()

query = "from function.calls as c select c.enclosing, c.name"
result = run(compile(parse(query)), engine)
print(f"GQL: {query}")
print()
print("SQL:")
print(result.query_sql)
print()

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

print("=" * 70)
print("[SUCCESS] DEMO COMPLETE")
print("=" * 70)
print()
print(f"Database: {db_path}")
print()
print("Next: try the CLI")
print(f"  python -m cli.graft query 'from function as fn select fn.name' --db {db_url}")
print()
