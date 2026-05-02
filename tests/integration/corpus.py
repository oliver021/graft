"""
Deterministic test corpus for integration tests.

Two Python source files with a precisely known structure so every
integration test can make exact assertions against known expected values.

CORPUS_MATH_PY  →  "src/math_utils.py"
CORPUS_PROC_PY  →  "src/processor.py"

IMPORTANT — naive reference resolver limitation:
  resolve_references() matches callee_name to symbol.name by exact string.
  Dotted calls like `calc.add(...)` store callee_name = "calc.add", which
  does NOT match symbol name "add".  Only bare function calls resolve.
  EXPECTED and test assertions are written to reflect this real behaviour.

Do NOT change these strings without updating EXPECTED below.
"""

# ---------------------------------------------------------------------------
# src/math_utils.py
# Symbols:
#   class  Calculator
#   method Calculator.add       (3 params: self, a, b)
#   method Calculator.subtract  (3 params: self, a, b)
#   method Calculator.multiply  (calls self.add — dotted, won't resolve)
#   fn     no_args_fn           (zero params)
#   fn     unused_fn            (never called)
# ---------------------------------------------------------------------------
CORPUS_MATH_PY = '''\
class Calculator:
    """Simple calculator class."""

    def add(self, a: int, b: int) -> int:
        return a + b

    def subtract(self, a: int, b: int) -> int:
        return a - b

    def multiply(self, a: int, b: int) -> int:
        return self.add(a, 0) + self.add(b, 0)


def no_args_fn() -> None:
    """Function with no arguments."""
    pass


def unused_fn() -> None:
    """This function is never called — dead code."""
    pass
'''
CORPUS_MATH_PATH = "src/math_utils.py"


# ---------------------------------------------------------------------------
# src/processor.py
# Symbols:
#   fn  process    calls Calculator() [resolves] + transform() [resolves]
#                  raises ValueError → getDoesThrow = True
#   fn  transform  called directly by process → HAS a resolved caller
#
# Resolved reference graph (bare calls only):
#   process  → Calculator   (resolves: "Calculator" == symbol name)
#   process  → transform    (resolves: "transform"  == symbol name)
#   multiply → self.add     (does NOT resolve: "self.add" ≠ "add")
# ---------------------------------------------------------------------------
CORPUS_PROC_PY = '''\
from math_utils import Calculator


def process(data):
    """Process data. Raises on empty input."""
    import os   # inside function body — captured by the adapter
    if not data:
        raise ValueError("data must not be empty")
    calc = Calculator()
    return transform(len(data))


def transform(x):
    """Called directly by process — will have a resolved caller."""
    return x * 2
'''
CORPUS_PROC_PATH = "src/processor.py"


# ---------------------------------------------------------------------------
# Expected counts — derived by hand and verified against the live system.
# All integration test assertions must reference these constants.
# ---------------------------------------------------------------------------
EXPECTED = {
    # ── Symbols ──────────────────────────────────────────────────────────
    "classes":         1,   # Calculator
    "methods":         3,   # add, subtract, multiply
    "top_fns":         4,   # no_args_fn, unused_fn, process, transform
    "total_functions": 7,   # 3 methods + 4 top-level functions
    "total_files":     2,   # math_utils.py, processor.py

    # ── Predicates ───────────────────────────────────────────────────────
    # getDoesThrow: symbols that contain a raise_statement
    "does_throw":      1,   # process (raises ValueError)

    # withoutArgs: symbols whose signature.params is empty
    "without_args":    2,   # no_args_fn, unused_fn

    # withoutCallers: symbols with no RESOLVED to_symbol_id reference
    # Has callers: transform (called as "transform(...)"), Calculator (called as "Calculator()")
    # No callers: add, subtract, multiply (only via self.add/calc.add), no_args_fn, process, unused_fn
    "without_callers": 6,

    # ── Traversals ───────────────────────────────────────────────────────
    "imports_in_proc": 1,   # import os  (from-import not counted as import_statement)
}

# Named sets used for membership assertions
NAMES = {
    "all_methods":      {"add", "subtract", "multiply"},
    "does_throw":       {"process"},
    "has_callers":      {"transform", "Calculator"},   # NOT in withoutCallers
    "no_callers":       {"no_args_fn", "unused_fn"},   # definitely IN withoutCallers
    "without_args":     {"no_args_fn", "unused_fn"},
    "calls_in_process": {"Calculator", "transform"},   # bare calls that resolve
}
