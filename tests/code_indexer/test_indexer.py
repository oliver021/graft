"""Unit tests for code_indexer module."""

import json
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine

from code_indexer.indexer import scan, scan_file
from code_indexer.models import ScanResult


class TestScan:
    """Tests for the scan() function."""

    def test_scan_simple_function(self):
        """Test scanning a simple function."""
        code = "def greet(name: str) -> str:\n    return f'Hello, {name}!'"
        result = scan(code, "python", "test.py")

        assert isinstance(result, ScanResult)
        assert result.file.path == "test.py"
        assert result.file.language == "python"
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "greet"
        assert result.symbols[0].kind == "function"

    def test_scan_extracts_symbols(self):
        """Test that scan extracts all symbols (functions, classes, methods)."""
        code = '''
def func1():
    pass

class MyClass:
    def method1(self):
        pass

    def method2(self):
        pass
'''
        result = scan(code, "python", "test.py")

        assert len(result.symbols) == 4
        assert result.symbols[0].name == "func1"
        assert result.symbols[0].kind == "function"
        assert result.symbols[1].name == "MyClass"
        assert result.symbols[1].kind == "class"
        assert result.symbols[2].name == "method1"
        assert result.symbols[2].kind == "method"
        assert result.symbols[3].name == "method2"
        assert result.symbols[3].kind == "method"

    def test_scan_extracts_expressions(self):
        """Test that scan extracts expressions (calls, assignments, returns)."""
        code = '''
def example():
    x = 5
    y = add(x, 3)
    return y
'''
        result = scan(code, "python", "test.py")

        assert len(result.expressions) > 0
        kinds = {e.kind for e in result.expressions}
        assert "assignment" in kinds
        assert "call" in kinds
        assert "return" in kinds

    def test_scan_extracts_references(self):
        """Test that scan extracts call references."""
        code = '''
def add(a, b):
    return a + b

def main():
    result = add(1, 2)
    print(result)
'''
        result = scan(code, "python", "test.py")

        assert len(result.references) > 0
        callee_names = {r.callee_name for r in result.references}
        assert "add" in callee_names
        assert "print" in callee_names

    def test_scan_with_signature(self):
        """Test that function signatures are extracted."""
        code = "def greet(name: str, age: int = 0) -> str:\n    return name"
        result = scan(code, "python", "test.py")

        func = result.symbols[0]
        assert func.signature is not None
        assert "params" in func.signature
        assert len(func.signature["params"]) == 2
        assert func.signature["params"][0]["name"] == "name"
        assert func.signature["params"][1]["name"] == "age"

    def test_scan_deterministic_ids(self):
        """Test that scan produces deterministic UUIDs."""
        code = "def foo():\n    pass"
        result1 = scan(code, "python", "test.py")
        result2 = scan(code, "python", "test.py")

        assert result1.file.id == result2.file.id
        assert result1.file.content_hash == result2.file.content_hash

    def test_scan_different_content_different_hash(self):
        """Test that different content produces different hashes."""
        result1 = scan("def foo(): pass", "python", "test.py")
        result2 = scan("def bar(): pass", "python", "test.py")

        assert result1.file.content_hash != result2.file.content_hash

    def test_scan_file(self, tmp_path):
        """Test scanning a file from disk."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    pass")

        result = scan_file(test_file)

        assert result.file.language == "python"
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "hello"


class TestScanResult:
    """Tests for ScanResult serialization."""

    def test_to_dict(self):
        """Test converting ScanResult to dict."""
        code = "def foo():\n    pass"
        result = scan(code, "python", "test.py")
        data = result.to_dict()

        assert "files" in data
        assert "symbols" in data
        assert "expressions" in data
        assert "references" in data
        assert len(data["files"]) == 1
        assert len(data["symbols"]) == 1

    def test_to_json(self):
        """Test converting ScanResult to JSON."""
        code = "def foo():\n    pass"
        result = scan(code, "python", "test.py")
        json_str = result.to_json()

        data = json.loads(json_str)
        assert "files" in data
        assert data["files"][0]["path"] == "test.py"

    def test_to_sql_sqlite(self):
        """Test generating SQLite INSERT statements."""
        code = "def foo():\n    pass"
        result = scan(code, "python", "test.py")
        sql = result.to_sql(dialect="sqlite")

        assert "INSERT INTO" in sql
        assert '"files"' in sql
        assert '"symbols"' in sql
        assert "test.py" in sql

    def test_to_sql_postgresql(self):
        """Test generating PostgreSQL INSERT statements."""
        code = "def foo():\n    pass"
        result = scan(code, "python", "test.py")
        sql = result.to_sql(dialect="postgresql")

        assert "INSERT INTO" in sql

    def test_insert_into_database(self, tmp_path):
        """Test inserting ScanResult into SQLite database."""
        from code_indexer.schema import create_all

        code = "def foo():\n    pass"
        result = scan(code, "python", "test.py")

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        create_all(engine)

        result.insert_into(engine)

        # Verify data was inserted
        with engine.connect() as conn:
            from code_indexer.schema import files_table, symbols_table
            files = list(conn.execute(files_table.select()))
            symbols = list(conn.execute(symbols_table.select()))

            assert len(files) == 1
            assert len(symbols) == 1
            assert symbols[0].name == "foo"

        engine.dispose()

    def test_functions_accessor(self):
        """Test the functions() convenience accessor."""
        code = '''
def foo():
    pass

class Bar:
    def method(self):
        pass
'''
        result = scan(code, "python", "test.py")
        functions = result.functions()

        assert len(functions) == 2
        names = {f.name for f in functions}
        assert "foo" in names
        assert "method" in names

    def test_calls_accessor(self):
        """Test the calls() convenience accessor."""
        code = '''
def main():
    print("hello")
    x = len([1, 2, 3])
'''
        result = scan(code, "python", "test.py")
        calls = result.calls()

        assert len(calls) > 0
        assert all(c.kind == "call" for c in calls)


class TestComplexCode:
    """Tests with more complex code structures."""

    def test_nested_functions(self):
        """Test extraction of nested functions."""
        code = '''
def outer():
    def inner():
        pass
    return inner()
'''
        result = scan(code, "python", "test.py")

        names = {s.name for s in result.symbols}
        assert "outer" in names
        assert "inner" in names

    def test_class_with_init(self):
        """Test extraction of __init__ and other methods."""
        code = '''
class Person:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}"
'''
        result = scan(code, "python", "test.py")

        methods = {s.name for s in result.symbols if s.kind == "method"}
        assert "__init__" in methods
        assert "greet" in methods

    def test_multiple_calls(self):
        """Test extraction of multiple function calls."""
        code = '''
def main():
    x = add(1, 2)
    y = multiply(x, 3)
    print(x)
    print(y)
'''
        result = scan(code, "python", "test.py")

        callees = {r.callee_name for r in result.references}
        assert "add" in callees
        assert "multiply" in callees
        assert "print" in callees

    def test_expression_depth(self):
        """Test that expression depth is tracked."""
        code = '''
def compute():
    result = (1 + 2) * (3 + 4)
    return result
'''
        result = scan(code, "python", "test.py")

        depths = {e.depth for e in result.expressions}
        assert len(depths) > 1  # Should have multiple depth levels


class TestRaiseStatement:
    """Tests for raise_statement capture (getDoesThrow support)."""

    def test_raise_is_captured(self):
        code = '''
def risky():
    raise ValueError("bad input")
'''
        result = scan(code, "python", "test.py")
        kinds = {e.kind for e in result.expressions}
        assert "raise" in kinds

    def test_raise_source_text(self):
        code = '''
def risky():
    raise ValueError("bad input")
'''
        result = scan(code, "python", "test.py")
        raises = [e for e in result.expressions if e.kind == "raise"]
        assert len(raises) == 1
        assert "raise" in raises[0].source_text

    def test_raise_without_argument(self):
        code = '''
def reraise():
    raise
'''
        result = scan(code, "python", "test.py")
        kinds = {e.kind for e in result.expressions}
        assert "raise" in kinds

    def test_raise_in_nested_branch(self):
        code = '''
def validate(x):
    if x < 0:
        raise ValueError("negative")
    return x
'''
        result = scan(code, "python", "test.py")
        kinds = {e.kind for e in result.expressions}
        assert "raise" in kinds


class TestImportModuleExtraction:
    """Tests for bare import module name extraction."""

    def test_bare_import_module_name(self):
        code = '''
def foo():
    import os
'''
        result = scan(code, "python", "test.py")
        imports = [e for e in result.expressions if e.kind == "import"]
        assert len(imports) == 1
        assert imports[0].extra is not None
        assert imports[0].extra["module"] == "os"

    def test_bare_import_dotted_module(self):
        code = '''
def foo():
    import os.path
'''
        result = scan(code, "python", "test.py")
        imports = [e for e in result.expressions if e.kind == "import"]
        assert imports[0].extra["module"] == "os.path"

    def test_bare_import_aliased(self):
        code = '''
def foo():
    import numpy as np
'''
        result = scan(code, "python", "test.py")
        imports = [e for e in result.expressions if e.kind == "import"]
        assert imports[0].extra["module"] == "numpy"

    def test_from_import_still_works(self):
        code = '''
def foo():
    from os.path import join
'''
        result = scan(code, "python", "test.py")
        imports = [e for e in result.expressions if e.kind == "import"]
        assert imports[0].extra["module"] == "os.path"


class TestResolveReferences:
    """Tests for the reference resolution pass."""

    def test_resolve_references_updates_to_symbol_id(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, references_table
        from code_indexer.indexer import resolve_references

        code = '''
def add(a, b):
    return a + b

def main():
    result = add(1, 2)
'''
        result = scan(code, "python", "test.py")
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        result.insert_into(engine)

        # Before resolution all to_symbol_id should be NULL
        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
        assert all(r.to_symbol_id is None for r in refs)

        resolved = resolve_references(engine)
        assert resolved > 0

        # After resolution, the call to `add` should be linked
        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
        add_ref = next(r for r in refs if r.callee_name == "add")
        assert add_ref.to_symbol_id is not None
        engine.dispose()

    def test_resolve_references_returns_count(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all
        from code_indexer.indexer import resolve_references

        code = '''
def helper():
    pass

def caller():
    helper()
    helper()
'''
        result = scan(code, "python", "test.py")
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        result.insert_into(engine)

        resolved = resolve_references(engine)
        assert resolved == 2  # two calls to helper
        engine.dispose()

    def test_resolve_references_idempotent(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all
        from code_indexer.indexer import resolve_references

        code = '''
def target():
    pass

def caller():
    target()
'''
        result = scan(code, "python", "test.py")
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        result.insert_into(engine)

        resolve_references(engine)
        second = resolve_references(engine)
        assert second == 0  # nothing left to resolve
        engine.dispose()

    def test_unresolvable_callee_stays_null(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, references_table
        from code_indexer.indexer import resolve_references

        code = '''
def caller():
    some_external_lib_function()
'''
        result = scan(code, "python", "test.py")
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        result.insert_into(engine)

        resolve_references(engine)

        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
        assert refs[0].to_symbol_id is None
        engine.dispose()


class TestDecoratedAndAsyncSymbols:
    """Decorated and async functions must be extracted like plain ones."""

    def test_decorated_function_extracted(self):
        code = '''
@staticmethod
def helper():
    pass
'''
        result = scan(code, "python", "test.py")
        assert any(s.name == "helper" for s in result.symbols)

    def test_decorated_class_extracted(self):
        code = '''
@dataclass
class Point:
    def distance(self):
        pass
'''
        result = scan(code, "python", "test.py")
        names = {s.name for s in result.symbols}
        assert "Point" in names
        assert "distance" in names

    def test_async_function_extracted(self):
        code = '''
async def fetch(url):
    return url
'''
        result = scan(code, "python", "test.py")
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        assert sym.name == "fetch"
        assert sym.kind == "function"

    def test_async_method_extracted(self):
        code = '''
class Client:
    async def get(self, path):
        pass
'''
        result = scan(code, "python", "test.py")
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(m.name == "get" for m in methods)

    def test_async_function_calls_captured(self):
        code = '''
async def main():
    result = await fetch("http://example.com")
    print(result)
'''
        result = scan(code, "python", "test.py")
        callees = {r.callee_name for r in result.references}
        assert "print" in callees


class TestRaiseEdgeCases:
    """Additional raise-statement coverage."""

    def test_raise_from_captured(self):
        code = '''
def convert(x):
    try:
        return int(x)
    except ValueError as e:
        raise TypeError("bad type") from e
'''
        result = scan(code, "python", "test.py")
        kinds = {e.kind for e in result.expressions}
        assert "raise" in kinds

    def test_raise_linked_to_symbol(self):
        code = '''
def risky():
    raise RuntimeError("oops")
'''
        result = scan(code, "python", "test.py")
        sym = next(s for s in result.symbols if s.name == "risky")
        raises = [e for e in result.expressions if e.kind == "raise"]
        assert raises[0].symbol_id == sym.id

    def test_multiple_raises_in_one_function(self):
        code = '''
def validate(x):
    if x < 0:
        raise ValueError("negative")
    if x > 100:
        raise ValueError("too large")
    return x
'''
        result = scan(code, "python", "test.py")
        raises = [e for e in result.expressions if e.kind == "raise"]
        assert len(raises) == 2


class TestSymbolHierarchy:
    """parent_id chains must be correct for GQL class.methods traversal."""

    def test_method_parent_id_points_to_class(self):
        code = '''
class Calculator:
    def add(self, a, b):
        return a + b
'''
        result = scan(code, "python", "test.py")
        cls = next(s for s in result.symbols if s.kind == "class")
        method = next(s for s in result.symbols if s.kind == "method")
        assert method.parent_id == cls.id

    def test_multiple_methods_all_point_to_class(self):
        code = '''
class Foo:
    def alpha(self): pass
    def beta(self): pass
    def gamma(self): pass
'''
        result = scan(code, "python", "test.py")
        cls = next(s for s in result.symbols if s.kind == "class")
        methods = [s for s in result.symbols if s.kind == "method"]
        assert len(methods) == 3
        assert all(m.parent_id == cls.id for m in methods)

    def test_nested_function_parent_id_points_to_outer(self):
        code = '''
def outer():
    def inner():
        pass
    return inner
'''
        result = scan(code, "python", "test.py")
        outer = next(s for s in result.symbols if s.name == "outer")
        inner = next(s for s in result.symbols if s.name == "inner")
        assert inner.parent_id == outer.id

    def test_triple_nesting_class_method_nested_func(self):
        code = '''
class Service:
    def process(self):
        def helper():
            pass
        return helper()
'''
        result = scan(code, "python", "test.py")
        cls = next(s for s in result.symbols if s.kind == "class")
        method = next(s for s in result.symbols if s.kind == "method")
        helper = next(s for s in result.symbols if s.name == "helper")
        assert method.parent_id == cls.id
        assert helper.parent_id == method.id

    def test_top_level_function_has_no_parent(self):
        code = '''
def standalone():
    pass
'''
        result = scan(code, "python", "test.py")
        assert result.symbols[0].parent_id is None

    def test_start_and_end_line_accuracy(self):
        code = "def foo():\n    pass\n"
        result = scan(code, "python", "test.py")
        sym = result.symbols[0]
        assert sym.start_line == 1
        assert sym.end_line == 2


class TestExpressionTree:
    """Expression symbol_id, parent_id and depth must form a valid tree."""

    def test_every_expression_has_symbol_id(self):
        code = '''
def compute():
    x = 1 + 2
    return x
'''
        result = scan(code, "python", "test.py")
        assert all(e.symbol_id is not None for e in result.expressions)

    def test_symbol_id_matches_owning_function(self):
        code = '''
def foo():
    x = 1

def bar():
    y = 2
'''
        result = scan(code, "python", "test.py")
        foo = next(s for s in result.symbols if s.name == "foo")
        bar = next(s for s in result.symbols if s.name == "bar")
        foo_exprs = [e for e in result.expressions if e.symbol_id == foo.id]
        bar_exprs = [e for e in result.expressions if e.symbol_id == bar.id]
        assert len(foo_exprs) >= 1
        assert len(bar_exprs) >= 1

    def test_nested_call_depth_increments(self):
        code = '''
def outer():
    result = foo(bar(baz()))
'''
        result = scan(code, "python", "test.py")
        calls = [e for e in result.expressions if e.kind == "call"]
        depths = {c.depth for c in calls}
        assert len(depths) >= 2  # foo at shallower depth, bar/baz deeper

    def test_max_depth_is_not_exceeded(self):
        # 10 levels of nesting — walk_body has max_depth=8
        code = "def f():\n    a(b(c(d(e(f(g(h(i(j())))))))))\n"
        result = scan(code, "python", "test.py")
        calls = [e for e in result.expressions if e.kind == "call"]
        assert all(e.depth <= 8 for e in calls)

    def test_method_call_is_method_flag(self):
        code = '''
def run(self):
    self.connect()
    self.send("data")
'''
        result = scan(code, "python", "test.py")
        calls = [e for e in result.expressions if e.kind == "call"]
        method_calls = [c for c in calls if c.extra and c.extra.get("is_method")]
        assert len(method_calls) >= 2

    def test_method_callee_name_includes_receiver(self):
        code = '''
def run(obj):
    obj.process()
'''
        result = scan(code, "python", "test.py")
        calls = [e for e in result.expressions if e.kind == "call"]
        assert any("obj.process" in (c.extra or {}).get("callee", "") for c in calls)


class TestReferenceIntegrity:
    """Every reference must carry correct from_symbol_id and expression_id."""

    def test_every_reference_has_from_symbol_id(self):
        code = '''
def caller():
    helper()

def helper():
    pass
'''
        result = scan(code, "python", "test.py")
        assert all(r.from_symbol_id is not None for r in result.references)

    def test_from_symbol_id_matches_caller(self):
        code = '''
def caller():
    target()

def target():
    pass
'''
        result = scan(code, "python", "test.py")
        caller_sym = next(s for s in result.symbols if s.name == "caller")
        for ref in result.references:
            if ref.callee_name == "target":
                assert ref.from_symbol_id == caller_sym.id

    def test_every_reference_has_expression_id(self):
        code = '''
def foo():
    bar()
    baz()
'''
        result = scan(code, "python", "test.py")
        assert all(r.expression_id for r in result.references)

    def test_reference_expression_id_links_to_call_expression(self):
        code = '''
def foo():
    bar()
'''
        result = scan(code, "python", "test.py")
        ref = result.references[0]
        call_expr = next(e for e in result.expressions if e.id == ref.expression_id)
        assert call_expr.kind == "call"


class TestCrossFileResolution:
    """resolve_references must link across files in the same DB."""

    def test_cross_file_callee_resolved(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, references_table
        from code_indexer.indexer import resolve_references

        lib_code = '''
def shared_helper():
    pass
'''
        app_code = '''
def main():
    shared_helper()
'''
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        scan(lib_code, "python", "lib.py").insert_into(engine)
        scan(app_code, "python", "app.py").insert_into(engine)

        resolved = resolve_references(engine)
        assert resolved >= 1

        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
        target_ref = next(r for r in refs if r.callee_name == "shared_helper")
        assert target_ref.to_symbol_id is not None
        engine.dispose()

    def test_same_name_ambiguity_resolves_to_first_match(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, references_table, symbols_table
        from code_indexer.indexer import resolve_references

        # Same function name in two files — contents differ to avoid ID collision
        code_a = "def common():\n    return 1\n"
        code_b = "def common():\n    return 2\n"
        code_c = "def caller():\n    common()\n"

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        scan(code_a, "python", "a.py").insert_into(engine)
        scan(code_b, "python", "b.py").insert_into(engine)
        scan(code_c, "python", "c.py").insert_into(engine)

        resolve_references(engine)

        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
            syms = list(conn.execute(symbols_table.select()))

        target_ref = next(r for r in refs if r.callee_name == "common")
        # Resolved to one of the two — not NULL, and a valid symbol ID
        valid_ids = {s.id for s in syms if s.name == "common"}
        assert target_ref.to_symbol_id in valid_ids
        engine.dispose()

    def test_method_call_self_dot_not_resolved(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, references_table
        from code_indexer.indexer import resolve_references

        code = '''
class Svc:
    def run(self):
        self.connect()

    def connect(self):
        pass
'''
        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        create_all(engine)
        scan(code, "python", "svc.py").insert_into(engine)
        resolve_references(engine)

        with engine.connect() as conn:
            refs = list(conn.execute(references_table.select()))
        # self.connect callee_name is "self.connect", not "connect" → stays NULL
        self_ref = next((r for r in refs if "connect" in r.callee_name), None)
        assert self_ref is not None
        assert self_ref.to_symbol_id is None
        engine.dispose()


class TestToSqlRoundtrip:
    """Generated SQL must be valid and reproduce the exact row data."""

    def test_sqlite_roundtrip_file_row(self, tmp_path):
        from sqlalchemy import create_engine, text
        from code_indexer.schema import create_all

        code = "def foo():\n    pass\n"
        result = scan(code, "python", "src.py")
        sql = result.to_sql("sqlite")

        engine = create_engine(f"sqlite:///{tmp_path}/rt.db")
        create_all(engine)
        with engine.begin() as conn:
            for stmt in sql.split(";\n"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))

        with engine.connect() as conn:
            rows = list(conn.execute(text("SELECT path, language FROM files")))
        assert rows[0].path == "src.py"
        assert rows[0].language == "python"
        engine.dispose()

    def test_sqlite_roundtrip_symbol_count(self, tmp_path):
        from sqlalchemy import create_engine, text
        from code_indexer.schema import create_all

        code = '''
def alpha(): pass
def beta(): pass
class Gamma:
    def delta(self): pass
'''
        result = scan(code, "python", "src.py")
        sql = result.to_sql("sqlite")

        engine = create_engine(f"sqlite:///{tmp_path}/rt.db")
        create_all(engine)
        with engine.begin() as conn:
            for stmt in sql.split(";\n"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM symbols")).scalar()
        assert count == 4
        engine.dispose()

    def test_json_with_special_chars_survives_roundtrip(self, tmp_path):
        from sqlalchemy import create_engine
        from code_indexer.schema import create_all, symbols_table
        import json as _json

        code = "def it(x: str = \"don't\") -> None:\n    pass\n"
        result = scan(code, "python", "src.py")

        engine = create_engine(f"sqlite:///{tmp_path}/rt.db")
        create_all(engine)
        result.insert_into(engine)

        with engine.connect() as conn:
            rows = list(conn.execute(symbols_table.select()))
        sig = rows[0].signature
        if isinstance(sig, str):
            sig = _json.loads(sig)
        assert sig["params"][0]["name"] == "x"
        engine.dispose()


class TestScanDir:
    """scan_dir must filter correctly and swallow per-file errors."""

    def test_scan_dir_yields_py_files(self, tmp_path):
        from code_indexer.indexer import scan_dir

        (tmp_path / "a.py").write_text("def foo(): pass")
        (tmp_path / "b.py").write_text("def bar(): pass")
        (tmp_path / "readme.txt").write_text("not code")

        results = list(scan_dir(tmp_path))
        assert len(results) == 2
        paths = {r.file.path for r in results}
        assert all(p.endswith(".py") for p in paths)

    def test_scan_dir_language_filter(self, tmp_path):
        from code_indexer.indexer import scan_dir

        (tmp_path / "a.py").write_text("def foo(): pass")
        (tmp_path / "b.py").write_text("def bar(): pass")

        results = list(scan_dir(tmp_path, languages=["python"]))
        assert len(results) == 2

    def test_scan_dir_empty_directory(self, tmp_path):
        from code_indexer.indexer import scan_dir

        results = list(scan_dir(tmp_path))
        assert results == []

    def test_scan_dir_bad_file_does_not_stop_iteration(self, tmp_path):
        from code_indexer.indexer import scan_dir
        from code_indexer import SkippedFile, ScanError
        from code_indexer.models import ScanResult

        (tmp_path / "good.py").write_text("def ok(): pass")
        # Binary file with .py extension — yielded as SkippedFile, not a crash
        (tmp_path / "bad.py").write_bytes(b"\xff\xfe" + b"\x00" * 50)

        items = list(scan_dir(tmp_path))
        # good.py must still be yielded as a ScanResult
        scan_results = [i for i in items if isinstance(i, ScanResult)]
        assert any("good.py" in r.file.path for r in scan_results)
        # bad.py must appear as SkippedFile (binary) — not crash the iterator
        non_results = [i for i in items if not isinstance(i, ScanResult)]
        assert any(isinstance(i, SkippedFile) for i in non_results)


class TestUUIDStability:
    """IDs must be deterministic across rescans."""

    def test_symbol_ids_are_stable(self):
        code = "def foo():\n    bar()\n"
        r1 = scan(code, "python", "f.py")
        r2 = scan(code, "python", "f.py")
        assert r1.symbols[0].id == r2.symbols[0].id

    def test_expression_ids_are_stable(self):
        code = "def foo():\n    bar()\n"
        r1 = scan(code, "python", "f.py")
        r2 = scan(code, "python", "f.py")
        ids1 = {e.id for e in r1.expressions}
        ids2 = {e.id for e in r2.expressions}
        assert ids1 == ids2

    def test_different_paths_same_content_different_file_ids(self):
        # Path is part of the file_id seed, so identical content at different
        # paths produces distinct IDs — required for multi-file indexing.
        code = "def foo(): pass\n"
        r1 = scan(code, "python", "a.py")
        r2 = scan(code, "python", "b.py")
        assert r1.file.id != r2.file.id

    def test_same_local_key_different_files_no_collision(self):
        # Both files have a function at the same byte offset.
        # Symbol IDs are seeded with file_id + local_key, so they diverge.
        code = "def foo(): pass\n"
        r1 = scan(code, "python", "x.py")
        r2 = scan(code, "python", "y.py")
        assert r1.symbols[0].id != r2.symbols[0].id


class TestCascadeDelete:
    """Deleting a file row must cascade to symbols, expressions, references."""

    def test_delete_file_removes_symbols(self, tmp_path):
        from sqlalchemy import create_engine, text
        from code_indexer.schema import create_all

        engine = create_engine(f"sqlite:///{tmp_path}/test.db",
                               connect_args={"check_same_thread": False})
        # Enable FK enforcement in SQLite
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))

        create_all(engine)
        result = scan("def foo(): pass\n", "python", "src.py")
        result.insert_into(engine)

        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))
            conn.execute(text(f"DELETE FROM files WHERE id = '{result.file.id}'"))

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM symbols")).scalar()
        assert count == 0
        engine.dispose()


class TestBoundaryBehavior:
    """Document intentional non-behaviors (module-level code, comprehensions)."""

    def test_module_level_code_not_indexed(self):
        # Assignments and calls at module scope (outside any function/class)
        # are not walked — this is by design, the walker only enters bodies.
        code = "x = 42\nprint(x)\n"
        result = scan(code, "python", "module.py")
        assert len(result.expressions) == 0
        assert len(result.references) == 0

    def test_comprehension_not_extracted_as_symbol(self):
        code = '''
def foo():
    squares = [x*x for x in range(10)]
    return squares
'''
        result = scan(code, "python", "test.py")
        # Comprehension generates no extra symbols — only the function itself
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "foo"

    def test_empty_file_produces_no_symbols(self):
        result = scan("", "python", "empty.py")
        assert result.symbols == []
        assert result.expressions == []
        assert result.references == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
