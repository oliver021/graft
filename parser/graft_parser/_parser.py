"""
Internal: builds the Lark parser instance and implements parse().

Grammar is read as UTF-8 and compiled once at import time.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from lark import Lark, UnexpectedEOF, UnexpectedInput
from lark.exceptions import VisitError

from .ast_nodes import QueryAST
from .errors import GraftParseError, ParseErrorKind
from .transformer import GQLTransformer

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

# ---------------------------------------------------------------------------
# Hint helpers
# ---------------------------------------------------------------------------

# All keywords and entity names a user might mistype
_GQL_KEYWORDS  = [
    "from", "as", "where", "select", "order", "by", "limit",
    "and", "or", "not", "like", "null", "true", "false", "asc", "desc",
]
_ROOT_ENTITIES = ["function", "class", "file", "expression"]
_ALL_KNOWN     = _GQL_KEYWORDS + _ROOT_ENTITIES


def _close_match(word: str, candidates: list[str] | None = None, *, cutoff: float = 0.75) -> str | None:
    """Return the closest known word, or None if nothing is close enough."""
    pool = candidates if candidates is not None else _ALL_KNOWN
    hits = difflib.get_close_matches(word.lower(), pool, n=1, cutoff=cutoff)
    return hits[0] if hits else None


def _norm_expected(expected: frozenset) -> set[str]:
    """Normalise Lark terminal names to lowercase bare strings for easy matching."""
    return {t.strip("\"'").lower() for t in expected}


def _hint_for_eof(expected: frozenset, query: str) -> str:
    """Return a hint string based on what Lark expected when EOF was hit."""
    norm = _norm_expected(expected)

    if "select" in norm:
        return (
            "The SELECT clause is required. "
            "Add it after the entity alias, e.g.:  select fn.name, fn.filename"
        )
    if "as" in norm:
        return (
            "The alias is required after the entity path. "
            "Use:  from <entity> as <alias>  e.g.  from function as fn"
        )
    if "root_entity" in norm or "__anon" in " ".join(norm) and "from" in query.lower():
        return (
            "Expected an entity name after 'from'. "
            "Valid entities: function, class, file, expression"
        )
    if "ident" in norm:
        return "Expected a name (alias, field, or traversal segment)"
    if "by" in norm:
        return "ORDER BY requires both keywords:  order by <field>"
    if "pos_int" in norm:
        return "LIMIT requires a positive integer ≥ 1, e.g.  limit 10"
    if "op" in norm:
        return "Expected a comparison operator: =  !=  >  <  >=  <="
    if "bool_op" in norm:
        return "Expected 'and' or 'or' to chain conditions"
    return ""


def _word_at(source: str, line: int, col: int) -> str:
    """Extract the full identifier/keyword starting at (line, col) in source."""
    import re
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        tail = lines[line - 1][max(0, col - 1):]
        m = re.match(r"[a-zA-Z_]\w*", tail)
        return m.group() if m else ""
    return ""


def _structural_hint(source: str, line: int, col: int, token_text: str) -> str:
    """Detect common structural mistakes by analysing the query text directly."""
    import re
    lines = source.splitlines()
    if not lines or not (1 <= line <= len(lines)):
        return ""
    before = lines[line - 1][: col - 1].rstrip()
    # Pattern: "from <entity>[.traversal]* <ident>" — user forgot 'as'
    if re.match(r"from\s+\w+(\s*\.\s*\w+(\s*\(.*?\))?)*\s*$", before, re.DOTALL | re.IGNORECASE):
        alias = token_text or _word_at(source, line, col)
        return f"Did you forget the 'as' keyword? Use:  from <entity> as {alias or '<alias>'} ..."
    stripped = source.lstrip()
    if stripped and not re.match(r"from\b", stripped, re.IGNORECASE):
        return "A GQL query must start with 'from', e.g.:  from function as fn select fn.name"
    return ""


def _hint_for_unexpected_token(
    source: str, line: int, col: int,
    token_text: str, token_type: str, expected: frozenset,
) -> str:
    """Return a hint string for an unexpected token."""
    # Structural analysis first — catches cases where Lark gives sparse expected info
    structural = _structural_hint(source, line, col, token_text)
    if structural:
        return structural

    norm = _norm_expected(expected)
    word = token_text.strip("\"'") or _word_at(source, line, col)

    # ROOT_ENTITY mismatch — unknown or misspelled entity name
    if "root_entity" in norm:
        close = _close_match(word, _ROOT_ENTITIES)
        if close and close != word.lower():
            return (
                f"Unknown entity '{word}'. Did you mean '{close}'? "
                f"Valid entities: function, class, file, expression"
            )
        return (
            f"Unknown entity '{word}'. "
            f"Valid root entities are: function, class, file, expression"
        )

    # Query does not start with 'from'
    if "from" in norm or (not norm and token_type == "__ANON_0"):
        return "A GQL query must start with 'from', e.g.:  from function as fn select fn.name"

    # ── Typo detection ───────────────────────────────────────────────────

    if word:
        close = _close_match(word)
        if close and close.lower() != word.lower():
            return f"Did you mean '{close}'?"

    return ""


def _make_snippet(source: str, line: int, column: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        src_line = lines[line - 1]
        caret    = " " * max(0, column - 1) + "^"
        return f"{src_line}\n{caret}"
    return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse(query: str) -> QueryAST:
    """
    Parse a GQL query string and return a QueryAST.

    Pure function: no I/O, no DB, no side effects.

    Raises:
        GraftParseError: on any syntax or structural problem
    """
    try:
        tree   = _lark.parse(query)
        result = _transformer.transform(tree)
        if not isinstance(result, QueryAST):
            raise GraftParseError.internal("transformer returned non-QueryAST")
        return result

    except GraftParseError:
        raise

    except UnexpectedEOF as e:
        line = getattr(e, "line", 0)
        col  = getattr(e, "column", 0)
        # Lark may report line=-1 for EOF on single-line inputs; fix to last line
        if line <= 0:
            line = max(1, query.count("\n") + 1)
            col  = len(query.splitlines()[-1]) + 1 if query.strip() else 1
        snippet = _make_snippet(query, line, col)
        hint    = _hint_for_eof(e.expected, query)
        raise GraftParseError.unexpected_eof(
            f"Unexpected end of input — the query is incomplete",
            line=line, column=col, snippet=snippet, hint=hint,
        ) from e

    except UnexpectedInput as e:
        line    = getattr(e, "line",   1)
        col     = getattr(e, "column", 1)
        snippet = _make_snippet(query, line, col)

        token_text = str(getattr(e, "token", "")).strip()
        token_type = str(getattr(getattr(e, "token", None), "type", ""))
        expected   = getattr(e, "expected", frozenset()) or getattr(e, "allowed", frozenset()) or frozenset()

        hint = _hint_for_unexpected_token(query, line, col, token_text, token_type, expected)

        # Build a compact, non-Lark message
        if token_text:
            message = f"Unexpected token '{token_text}'"
        else:
            message = str(e).split("\n")[0]

        raise GraftParseError.syntax(
            message, line=line, column=col, snippet=snippet, hint=hint,
        ) from e

    except VisitError as e:
        # Lark wraps transformer exceptions in VisitError.
        # If the original cause is already a GraftParseError, re-raise it
        # directly so the kind/hint set by the transformer are preserved.
        if isinstance(e.__context__, GraftParseError):
            raise e.__context__
        raise GraftParseError.internal(str(e.__context__ or e)) from e

    except Exception as e:
        raise GraftParseError.internal(str(e)) from e
