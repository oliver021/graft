"""Demonstration test: end-to-end indexer workflow."""

import json
import sqlite3
import tempfile
from pathlib import Path
from sqlalchemy import create_engine

from code_indexer.indexer import scan, scan_file
from code_indexer.schema import create_all


def test_demo_index_and_query():
    """
    Demonstration: Index Python code, store in SQLite, and query results.

    This shows the complete workflow:
    1. Scan Python source code
    2. Generate SQL
    3. Create database schema
    4. Insert data
    5. Query and verify
    """
    print("\n" + "=" * 70)
    print("DEMONSTRATION: Code Indexer End-to-End Workflow")
    print("=" * 70)

    # Step 1: Define some realistic Python code
    code = '''
"""A shopping cart system."""

class ShoppingCart:
    """Manages items in a shopping cart."""

    def __init__(self):
        self.items = []

    def add_item(self, name: str, price: float) -> None:
        """Add an item to the cart."""
        item = {"name": name, "price": price}
        self.items.append(item)

    def remove_item(self, name: str) -> bool:
        """Remove an item by name."""
        for item in self.items:
            if item["name"] == name:
                self.items.remove(item)
                return True
        return False

    def total(self) -> float:
        """Calculate total price."""
        total = sum(item["price"] for item in self.items)
        return apply_tax(total)


def apply_tax(amount: float, rate: float = 0.1) -> float:
    """Apply tax to an amount."""
    return amount * (1 + rate)


def main():
    """Main entry point."""
    cart = ShoppingCart()
    cart.add_item("Apple", 1.5)
    cart.add_item("Banana", 0.75)
    total = cart.total()
    print(f"Total: ${total:.2f}")
'''

    print("\n[1] Scanning Python code...")
    result = scan(code, "python", "shopping.py")
    print(f"    Symbols found: {len(result.symbols)}")
    print(f"    Expressions found: {len(result.expressions)}")
    print(f"    References found: {len(result.references)}")

    print("\n[2] Code structure extracted:")
    for sym in result.symbols:
        print(f"    - {sym.kind:10} {sym.name:20} (lines {sym.start_line}-{sym.end_line})")

    print("\n[3] Creating SQLite database...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "shopping.db"
        engine = create_engine(f"sqlite:///{db_path}")

        create_all(engine)
        result.insert_into(engine)
        print(f"    Database created: {db_path}")
        print(f"    Data inserted: 1 file, {len(result.symbols)} symbols, "
              f"{len(result.expressions)} expressions, {len(result.references)} references")

        print("\n[4] Querying database...")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query all functions
        cursor.execute("""
            SELECT name, kind FROM symbols WHERE kind IN ('function', 'method')
            ORDER BY start_line
        """)
        print("\n    Functions and Methods:")
        for row in cursor.fetchall():
            print(f"      - {row['kind']:8} {row['name']}")

        # Query all function calls
        cursor.execute("""
            SELECT DISTINCT callee_name FROM "references"
            ORDER BY callee_name
        """)
        print("\n    Function Calls:")
        for row in cursor.fetchall():
            print(f"      - {row['callee_name']}")

        # Count expressions by kind
        cursor.execute("""
            SELECT kind, COUNT(*) as count FROM expressions
            GROUP BY kind
            ORDER BY count DESC
        """)
        print("\n    Expression Types:")
        for row in cursor.fetchall():
            print(f"      - {row['kind']:15} {row['count']:3} occurrences")

        conn.close()
        engine.dispose()

    print("\n[5] Serialization formats:")
    json_str = result.to_json(indent=2)
    print(f"    to_json():  {len(json_str)} bytes")
    print(f"    to_dict():  {len(str(result.to_dict()))} bytes")
    sql = result.to_sql(dialect="sqlite")
    print(f"    to_sql():   {len(sql)} bytes, {len(sql.splitlines())} statements")

    print("\n" + "=" * 70)
    print("SUCCESS: Code indexer works end-to-end!")
    print("=" * 70)

    # Assertions to make it a valid test
    assert len(result.symbols) > 0
    assert len(result.expressions) > 0
    assert len(result.references) > 0
    assert result.file.path == "shopping.py"
    assert result.file.language == "python"


def test_demo_scan_file(tmp_path):
    """
    Demonstration: Scan a real file from disk.
    """
    print("\n" + "=" * 70)
    print("DEMONSTRATION: Scanning File from Disk")
    print("=" * 70)

    # Create a test file
    test_file = tmp_path / "calculator.py"
    test_file.write_text('''
class Calculator:
    """A basic calculator."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def multiply(self, a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b

    def power(self, base: int, exp: int) -> int:
        """Raise base to power."""
        result = 1
        for _ in range(exp):
            result = self.multiply(result, base)
        return result
''')

    print(f"\n[1] Scanning file: {test_file}")
    result = scan_file(test_file)

    print(f"    Language detected: {result.file.language}")
    print(f"    Content hash: {result.file.content_hash[:16]}...")
    print(f"    Symbols: {len(result.symbols)}")
    print(f"    Expressions: {len(result.expressions)}")
    print(f"    References: {len(result.references)}")

    print("\n[2] File structure:")
    for sym in result.symbols:
        print(f"    - {sym.kind:10} {sym.name:20} (lines {sym.start_line}-{sym.end_line})")

    print("\n[3] Internal method calls:")
    for ref in result.references:
        if ref.from_symbol_id:  # Only internal calls
            print(f"    - {ref.callee_name}")

    print("\n" + "=" * 70)
    print("SUCCESS: File scanning works!")
    print("=" * 70)

    assert result.file.language == "python"
    assert len(result.symbols) == 4  # class + 3 methods


if __name__ == "__main__":
    # Run demonstrations
    test_demo_index_and_query()
    print("\n\n")
    test_demo_scan_file(Path(tempfile.mkdtemp()))
