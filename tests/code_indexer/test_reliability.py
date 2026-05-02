"""
Reliability tests for code_indexer — hostile codebase scenarios.

Each test class maps to one of the 10 planned fixes:

  #1  File too large             → SkippedFile
  #2  Binary file                → SkippedFile
  #3  Unreadable file (I/O)      → IndexerError
  #4  Tree-sitter parse errors   → ScanResult.parse_error_count > 0
  #5  Unbounded source_text      → truncated to MAX_SOURCE_TEXT
  #6  Empty callee references    → not stored
  #7  Broken FK expression_id    → reference row dropped, no empty string
  #8  Re-index PK collision      → replace=True succeeds cleanly
  #9  scan_dir error reporting   → ScanError yielded, iteration continues
  #10 Expression count cap       → ScanResult.expressions_truncated = True
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text

from code_indexer import IndexerError, ScanError, SkippedFile, scan, scan_dir, scan_file
from code_indexer.indexer import MAX_FILE_BYTES, _build_result
from code_indexer.languages.base import MAX_SOURCE_TEXT, MAX_EXPRESSIONS_PER_FILE, RawExtraction
from code_indexer.models import ScanResult
from code_indexer.schema import create_all, files_table, references_table


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_py(content: str | bytes, suffix: str = ".py") -> Path:
    """Write content to a temp file and return its Path."""
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    if isinstance(content, str):
        content = content.encode("utf-8")
    f.write(content)
    f.flush()
    f.close()
    return Path(f.name)


def _in_memory_engine():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    return engine


# ---------------------------------------------------------------------------
# Fix #1 — File too large
# ---------------------------------------------------------------------------

class TestFileTooLarge:
    def test_oversized_file_returns_skipped(self, tmp_path):
        big = tmp_path / "big.py"
        big.write_bytes(b"x = 1\n" * 200_000)  # ~1.2 MB
        result = scan_file(big)
        assert isinstance(result, SkippedFile)
        assert "too large" in result.reason.lower()
        assert result.path == big

    def test_oversized_file_skipped_reason_includes_size(self, tmp_path):
        big = tmp_path / "large.py"
        big.write_bytes(b"# padding\n" * 150_000)
        result = scan_file(big)
        assert isinstance(result, SkippedFile)
        assert "MB" in result.reason

    def test_file_at_limit_is_scanned(self, tmp_path):
        """A file exactly at the limit (not exceeding it) should be scanned."""
        at_limit = tmp_path / "ok.py"
        # Write exactly MAX_FILE_BYTES bytes
        at_limit.write_bytes(b"a" * MAX_FILE_BYTES)
        # Should not raise and should not return SkippedFile
        result = scan_file(at_limit)
        assert isinstance(result, ScanResult)

    def test_custom_max_bytes_respected(self, tmp_path):
        small_limit = tmp_path / "tiny.py"
        small_limit.write_text("x = 1\n")  # ~6 bytes
        result = scan_file(small_limit, max_bytes=3)
        assert isinstance(result, SkippedFile)

    def test_small_file_not_skipped(self, tmp_path):
        f = tmp_path / "small.py"
        f.write_text("def f(): pass\n")
        result = scan_file(f)
        assert isinstance(result, ScanResult)


# ---------------------------------------------------------------------------
# Fix #2 — Binary file detected
# ---------------------------------------------------------------------------

class TestBinaryFile:
    def test_binary_py_file_returns_skipped(self, tmp_path):
        binary = tmp_path / "compiled.py"
        binary.write_bytes(b"\x00\x01\x02\x03" + b"some data")
        result = scan_file(binary)
        assert isinstance(result, SkippedFile)
        assert "binary" in result.reason.lower()

    def test_null_bytes_beyond_1kb_not_detected(self, tmp_path):
        """Null byte check only scans the first 1 KB — past that is ignored."""
        f = tmp_path / "edge.py"
        # 1025 safe bytes, then a null
        f.write_bytes(b"x" * 1025 + b"\x00")
        result = scan_file(f)
        # No null in first 1024 bytes → treated as text → ScanResult
        assert isinstance(result, ScanResult)

    def test_clean_utf8_file_not_skipped(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("def hello(): return 'hello world'\n", encoding="utf-8")
        result = scan_file(f)
        assert isinstance(result, ScanResult)


# ---------------------------------------------------------------------------
# Fix #3 — Unreadable file raises IndexerError
# ---------------------------------------------------------------------------

class TestUnreadableFile:
    def test_missing_file_raises_indexer_error(self, tmp_path):
        ghost = tmp_path / "ghost.py"
        with pytest.raises(IndexerError) as exc_info:
            scan_file(ghost)
        assert ghost == exc_info.value.path
        assert exc_info.value.original is not None

    @pytest.mark.skipif(os.name == "nt", reason="chmod not reliable on Windows CI")
    def test_permission_denied_raises_indexer_error(self, tmp_path):
        locked = tmp_path / "locked.py"
        locked.write_text("x = 1\n")
        locked.chmod(0o000)
        try:
            with pytest.raises(IndexerError) as exc_info:
                scan_file(locked)
            assert "cannot" in exc_info.value.reason.lower()
        finally:
            locked.chmod(0o644)

    def test_indexer_error_message_includes_path(self, tmp_path):
        ghost = tmp_path / "no_such_file.py"
        with pytest.raises(IndexerError) as exc_info:
            scan_file(ghost)
        assert str(ghost) in str(exc_info.value)

    def test_indexer_error_has_original_exception(self, tmp_path):
        ghost = tmp_path / "no_such_file.py"
        with pytest.raises(IndexerError) as exc_info:
            scan_file(ghost)
        assert exc_info.value.original is not None


# ---------------------------------------------------------------------------
# Fix #4 — Parse error detection
# ---------------------------------------------------------------------------

class TestParseErrorDetection:
    def test_clean_code_has_zero_parse_errors(self):
        result = scan("def f(x):\n    return x + 1\n", "python")
        assert result.parse_error_count == 0

    def test_broken_syntax_increments_parse_error_count(self):
        # Invalid Python — tree-sitter inserts ERROR nodes
        result = scan("def (\nbroken syntax here\n", "python")
        assert result.parse_error_count > 0

    def test_parse_error_count_is_int(self):
        result = scan("x = 1", "python")
        assert isinstance(result.parse_error_count, int)

    def test_scan_result_has_parse_error_count_field(self):
        result = scan("", "python")
        assert hasattr(result, "parse_error_count")


# ---------------------------------------------------------------------------
# Fix #5 — source_text truncation
# ---------------------------------------------------------------------------

class TestSourceTextTruncation:
    def test_long_expression_is_truncated(self):
        # Build a call with a very long argument string
        long_arg = "a" * 2000
        code = f"def f():\n    some_fn('{long_arg}')\n"
        result = scan(code, "python")
        for expr in result.expressions:
            assert len(expr.source_text) <= MAX_SOURCE_TEXT + 5  # +5 for '…' and safety

    def test_truncated_source_text_ends_with_ellipsis(self):
        long_arg = "b" * 2000
        code = f"def f():\n    func('{long_arg}')\n"
        result = scan(code, "python")
        long_exprs = [e for e in result.expressions if e.source_text.endswith("…")]
        assert long_exprs, "expected at least one truncated expression"

    def test_short_expression_not_truncated(self):
        result = scan("def f():\n    print('hi')\n", "python")
        calls = [e for e in result.expressions if e.kind == "call"]
        assert calls
        assert not calls[0].source_text.endswith("…")

    def test_max_source_text_constant_is_positive(self):
        assert MAX_SOURCE_TEXT > 0


# ---------------------------------------------------------------------------
# Fix #6 — Empty callee references not stored
# ---------------------------------------------------------------------------

class TestEmptyCalleeReferences:
    def test_no_reference_with_empty_callee_name(self):
        result = scan("def f():\n    x = 1 + 2\n    return x\n", "python")
        for ref in result.references:
            assert ref.callee_name != "", (
                f"Found reference with empty callee_name: {ref}"
            )

    def test_normal_call_reference_has_callee_name(self):
        result = scan("def f():\n    print('hello')\n", "python")
        ref_names = [r.callee_name for r in result.references]
        assert "print" in ref_names

    def test_all_references_have_non_empty_callee(self):
        code = """
def process(data):
    result = transform(data)
    save(result)
    return result
"""
        result = scan(code, "python")
        for ref in result.references:
            assert ref.callee_name.strip() != ""


# ---------------------------------------------------------------------------
# Fix #7 — Broken FK expression_id guarded
# ---------------------------------------------------------------------------

class TestBrokenFKGuard:
    def test_reference_with_missing_expr_key_is_dropped(self):
        """
        If a RawReference points to an expression_local_key that has no
        corresponding expression, _build_result must drop it rather than
        inserting an empty string FK.
        """
        from code_indexer.languages.base import RawReference, RawSymbol

        # Build a minimal RawExtraction with a reference whose key doesn't exist
        extraction = RawExtraction()
        extraction.symbols.append(RawSymbol(
            name="fn", kind="function",
            start_byte=0, end_byte=20,
            start_line=1, end_line=1,
            local_key="0:20",
        ))
        from code_indexer.languages.base import RawReference
        extraction.references.append(RawReference(
            callee_name="missing_target",
            expression_local_key="NONEXISTENT_KEY",
            from_symbol_local_key="0:20",
        ))
        result = _build_result("x = 1", "python", "test.py", extraction)
        # The bad reference must be silently dropped
        assert len(result.references) == 0

    def test_valid_reference_expression_id_is_not_empty(self):
        result = scan("def f():\n    print('hi')\n", "python")
        for ref in result.references:
            assert ref.expression_id != "", (
                f"Reference '{ref.callee_name}' has empty expression_id"
            )

    def test_valid_reference_expression_id_is_uuid_like(self):
        result = scan("def f():\n    len([1, 2, 3])\n", "python")
        for ref in result.references:
            # UUID v5 format: 8-4-4-4-12
            assert len(ref.expression_id) == 36
            assert ref.expression_id.count("-") == 4


# ---------------------------------------------------------------------------
# Fix #8 — Re-index without PK collision
# ---------------------------------------------------------------------------

class TestReIndex:
    def test_reindex_same_file_with_replace_true(self):
        engine = _in_memory_engine()
        result = scan("def f(): pass\n", "python", "a.py")
        result.insert_into(engine)
        # Re-index same file — must not raise IntegrityError
        result2 = scan("def f(): pass\n", "python", "a.py")
        result2.insert_into(engine, replace=True)
        # Exactly one file row should exist
        with engine.connect() as conn:
            rows = conn.execute(select(files_table)).fetchall()
        assert len(rows) == 1

    def test_reindex_changed_content_with_replace_true(self):
        engine = _in_memory_engine()
        scan("def old(): pass\n", "python", "a.py").insert_into(engine)
        scan("def new(): pass\n", "python", "a.py").insert_into(engine, replace=True)
        with engine.connect() as conn:
            rows = conn.execute(select(files_table)).fetchall()
        assert len(rows) == 1

    def test_reindex_without_replace_raises_on_duplicate(self):
        from sqlalchemy.exc import IntegrityError
        engine = _in_memory_engine()
        result = scan("def f(): pass\n", "python", "a.py")
        result.insert_into(engine)
        with pytest.raises(IntegrityError):
            result.insert_into(engine, replace=False)

    def test_replace_clears_old_references(self):
        """After replace=True re-index, old reference rows are gone."""
        engine = _in_memory_engine()
        scan("def f():\n    print('a')\n", "python", "a.py").insert_into(engine)
        # Re-index with no calls
        scan("def f():\n    x = 1\n", "python", "a.py").insert_into(engine, replace=True)
        with engine.connect() as conn:
            rows = conn.execute(select(references_table)).fetchall()
        assert len(rows) == 0

    def test_default_replace_is_false(self):
        """insert_into(engine) without replace= should keep old behaviour."""
        from sqlalchemy.exc import IntegrityError
        engine = _in_memory_engine()
        r = scan("x = 1\n", "python", "f.py")
        r.insert_into(engine)
        with pytest.raises(IntegrityError):
            r.insert_into(engine)


# ---------------------------------------------------------------------------
# Fix #9 — scan_dir structured error reporting
# ---------------------------------------------------------------------------

class TestScanDirErrorReporting:
    def test_scan_dir_yields_scan_error_for_bad_file(self, tmp_path):
        good = tmp_path / "good.py"
        good.write_text("def f(): pass\n")
        # Create a .py file then delete it while scan_dir is about to process it
        # — simulate by patching scan_file to raise IndexerError for one specific path
        bad_path = tmp_path / "bad.py"
        bad_path.write_text("def g(): pass\n")

        results = []
        errors = []

        import code_indexer.indexer as _idx_mod
        real_scan_file = _idx_mod.scan_file

        def patched(path, **kw):
            if Path(path).name == "bad.py":
                raise IndexerError(path, "simulated parse failure")
            return real_scan_file(path, **kw)

        with patch.object(_idx_mod, "scan_file", side_effect=patched):
            for item in scan_dir(tmp_path):
                if isinstance(item, ScanResult):
                    results.append(item)
                elif isinstance(item, ScanError):
                    errors.append(item)

        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0].path.name == "bad.py"
        assert "simulated" in errors[0].reason

    def test_scan_dir_yields_skipped_for_large_file(self, tmp_path):
        normal = tmp_path / "normal.py"
        normal.write_text("x = 1\n")
        big = tmp_path / "big.py"
        big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))

        items = list(scan_dir(tmp_path))
        skipped = [i for i in items if isinstance(i, SkippedFile)]
        scanned = [i for i in items if isinstance(i, ScanResult)]
        assert any(s.path.name == "big.py" for s in skipped)
        assert any(s.file.path.endswith("normal.py") for s in scanned)

    def test_scan_dir_continues_after_error(self, tmp_path):
        """An error on one file must not stop the rest of the scan."""
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text(f"def fn{i}(): pass\n")

        import code_indexer.indexer as _idx_mod
        count = 0
        with patch.object(_idx_mod, "scan_file", side_effect=RuntimeError("boom")):
            for item in scan_dir(tmp_path):
                assert isinstance(item, ScanError)
                count += 1

        assert count == 5

    def test_scan_error_has_path_and_exception(self, tmp_path):
        f = tmp_path / "oops.py"
        f.write_text("x = 1\n")

        import code_indexer.indexer as _idx_mod
        exc = RuntimeError("test error")
        with patch.object(_idx_mod, "scan_file", side_effect=exc):
            items = list(scan_dir(tmp_path))

        assert len(items) == 1
        err = items[0]
        assert isinstance(err, ScanError)
        assert err.path == f
        assert err.exception is exc

    def test_scan_dir_unsupported_extension_not_yielded(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not code\n")
        (tmp_path / "data.csv").write_text("a,b,c\n")
        (tmp_path / "real.py").write_text("x = 1\n")

        items = list(scan_dir(tmp_path))
        # Only the .py file should appear; .txt and .csv silently ignored
        assert len(items) == 1
        assert isinstance(items[0], ScanResult)


# ---------------------------------------------------------------------------
# Fix #10 — Expression count cap
# ---------------------------------------------------------------------------

class TestExpressionCap:
    def test_expressions_truncated_flag_set_on_overflow(self):
        # Generate code with more calls than MAX_EXPRESSIONS_PER_FILE
        calls = "\n".join(f"    fn_{i}()" for i in range(MAX_EXPRESSIONS_PER_FILE + 200))
        code = f"def big():\n{calls}\n"
        result = scan(code, "python")
        assert result.expressions_truncated is True

    def test_expression_count_does_not_exceed_cap(self):
        calls = "\n".join(f"    fn_{i}()" for i in range(MAX_EXPRESSIONS_PER_FILE + 200))
        code = f"def big():\n{calls}\n"
        result = scan(code, "python")
        assert len(result.expressions) <= MAX_EXPRESSIONS_PER_FILE

    def test_normal_file_not_truncated(self):
        code = "def f():\n" + "\n".join(f"    x_{i} = {i}" for i in range(50))
        result = scan(code, "python")
        assert result.expressions_truncated is False

    def test_expressions_truncated_field_exists(self):
        result = scan("x = 1\n", "python")
        assert hasattr(result, "expressions_truncated")
