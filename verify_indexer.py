#!/usr/bin/env python3
"""
Verification script: ensure indexer creates a local DB and dumps content.

Usage:
    python verify_indexer.py
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from sqlalchemy import create_engine
from code_indexer.indexer import scan
from code_indexer.schema import create_all, drop_all

# Test source code
TEST_PYTHON = '''
def greet(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    """Add two numbers."""
    result = a + b
    return result

class Calculator:
    """A simple calculator."""

    def multiply(self, x: int, y: int) -> int:
        """Multiply two numbers."""
        return x * y

    def divide(self, x: float, y: float) -> float:
        """Divide two numbers."""
        if y == 0:
            raise ValueError("Cannot divide by zero")
        return x / y

def main():
    """Main entry point."""
    name = "Alice"
    message = greet(name)
    print(message)

    result = add(2, 3)
    print(f"2 + 3 = {result}")

    calc = Calculator()
    prod = calc.multiply(4, 5)
    print(f"4 * 5 = {prod}")
'''

def main():
    print("=" * 70)
    print("GRAFT INDEXER VERIFICATION")
    print("=" * 70)

    # Step 1: Scan the test code
    print("\n[1] Scanning test Python code...")
    result = scan(TEST_PYTHON, "python", "test.py")
    print(f"    OK Scan result: {result}")
    print(f"    OK Symbols: {len(result.symbols)}")
    print(f"    OK Expressions: {len(result.expressions)}")
    print(f"    OK References: {len(result.references)}")

    # Step 2: Verify to_sql() output
    print("\n[2] Generating SQL INSERT statements...")
    sql_statements = result.to_sql(dialect="sqlite")
    num_statements = len(sql_statements.strip().split('\n'))
    print(f"    [OK] Generated {num_statements} SQL statements")
    print(f"    [OK] SQL preview (first 300 chars):\n       {sql_statements[:300]}...")

    # Step 3: Create local SQLite DB and insert
    print("\n[3] Creating local SQLite database...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "index.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # Create schema
        create_all(engine)
        print(f"    [OK] Schema created at {db_path}")

        # Insert data
        result.insert_into(engine)
        print(f"    [OK] Data inserted into database")

        # Step 4: Query and dump content
        print("\n[4] Dumping database content...\n")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # List all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"    Tables: {tables}")

        # Dump files
        print("\n    [FILES]")
        cursor.execute("SELECT * FROM files")
        for row in cursor.fetchall():
            print(f"      id: {row['id']}")
            print(f"      path: {row['path']}")
            print(f"      language: {row['language']}")
            print(f"      content_hash: {row['content_hash'][:16]}...")
            print(f"      scanned_at: {row['scanned_at']}")

        # Dump symbols
        print("\n    [SYMBOLS]")
        cursor.execute("""
            SELECT id, name, kind, start_line, end_line
            FROM symbols
            ORDER BY start_line
        """)
        for row in cursor.fetchall():
            print(f"      {row['kind']:10} {row['name']:20} (lines {row['start_line']}-{row['end_line']})")

        # Dump expressions
        print("\n    [EXPRESSIONS]")
        cursor.execute("""
            SELECT kind, source_text, start_line, depth
            FROM expressions
            ORDER BY start_line
            LIMIT 10
        """)
        for row in cursor.fetchall():
            src = row['source_text'][:30].replace('\n', ' ')
            print(f"      {row['kind']:15} '{src:30}' @ line {row['start_line']} (depth {row['depth']})")

        # Dump references
        print("\n    [REFERENCES]")
        cursor.execute("""
            SELECT callee_name, COUNT(*) as count
            FROM "references"
            GROUP BY callee_name
        """)
        for row in cursor.fetchall():
            print(f"      {row['callee_name']:20} (called {row['count']} times)")

        cursor.close()
        conn.close()
        engine.dispose()

    # Step 5: Verify to_dict() output
    print("\n[5] Verifying to_dict() output...")
    data = result.to_dict()
    print(f"    [OK] Keys: {list(data.keys())}")
    print(f"    [OK] Files: {len(data['files'])}")
    print(f"    [OK] Symbols: {len(data['symbols'])}")
    print(f"    [OK] Expressions: {len(data['expressions'])}")
    print(f"    [OK] References: {len(data['references'])}")

    # Step 6: Verify to_json() output
    print("\n[6] Verifying to_json() output...")
    json_str = result.to_json()
    parsed = json.loads(json_str)
    print(f"    [OK] Valid JSON: {len(json_str)} bytes")
    print(f"    [OK] Top-level keys: {list(parsed.keys())}")

    print("\n" + "=" * 70)
    print("[OK] ALL VERIFICATIONS PASSED")
    print("=" * 70)

if __name__ == "__main__":
    main()
