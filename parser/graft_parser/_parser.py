"""
Internal: builds the Lark parser instance and implements parse().

Grammar is read as UTF-8 and compiled once at import time.
"""

from __future__ import annotations

from pathlib import Path

from lark import Lark, UnexpectedInput, UnexpectedEOF

from .errors import GraftParseError
from .transformer import GQLTransformer
from .ast_nodes import QueryAST

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_GRAMMAR_TEXT = _GRAMMAR_PATH.read_text(encoding="utf-8")

_lark = Lark(
    _GRAMMAR_TEXT,
    parser="earley",
    ambiguity="resolve",
    propagate_positions=True,
    start="query",
)

_transformer = GQLTransformer()


def _make_snippet(source: str, line: int, column: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        src_line = lines[line - 1]
        caret = " " * max(0, column - 1) + "^"
        return f"{src_line}\n{caret}"
    return ""


def parse(query: str) -> QueryAST:
    """
    Parse a GQL query string and return a QueryAST.

    Pure function: no I/O, no DB, no side effects.

    Raises:
        GraftParseError: on any syntax or structural problem
    """
    try:
        tree = _lark.parse(query)
        result = _transformer.transform(tree)
        if not isinstance(result, QueryAST):
            raise GraftParseError("Parser produced unexpected output type")
        return result
    except GraftParseError:
        raise
    except UnexpectedEOF as e:
        line = getattr(e, "line", 1)
        col = getattr(e, "column", 1)
        raise GraftParseError(
            message=f"Unexpected end of input; expected: {e.expected}",
            line=line,
            column=col,
            snippet=_make_snippet(query, line, col),
        ) from e
    except UnexpectedInput as e:
        line = getattr(e, "line", 1)
        col = getattr(e, "column", 1)
        raise GraftParseError(
            message=str(e).split("\n")[0],
            line=line,
            column=col,
            snippet=_make_snippet(query, line, col),
        ) from e
    except Exception as e:
        raise GraftParseError(message=str(e)) from e
