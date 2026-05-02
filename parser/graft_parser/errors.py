"""
GraftParseError — the single exception type raised by graft_parser.parse().

Design goals
------------
1. Every parse failure has a *kind* (machine-readable category).
2. Every parse failure has a *hint* (human-readable actionable suggestion).
3. Location (line, column) and a source *snippet* with a caret are always
   included when the information is available.
4. __str__ renders a self-contained, readable message suitable for a terminal
   or an API error body — no raw Lark internals leak through.

Usage
-----
    raise GraftParseError.syntax("Unexpected token 'fn'", line=1, col=14,
                                  snippet=..., hint="Did you forget 'as'?")

    raise GraftParseError.unsupported(
        "Parenthesised multi-term conditions",
        hint="Rewrite using 'and'/'or' at the top level: "
             "where fn.a = 'x' and fn.b = 'y'",
    )
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Error kind
# ---------------------------------------------------------------------------

class ParseErrorKind(str, Enum):
    """
    Machine-readable category for GraftParseError.

    SYNTAX         — unexpected token or character; query cannot be parsed
    UNEXPECTED_EOF — query ended before a required clause was complete
    UNSUPPORTED    — the construct is understood but not implemented in v0.1
    INTERNAL       — should never happen; indicates a parser bug
    """
    SYNTAX         = "syntax"
    UNEXPECTED_EOF = "unexpected_eof"
    UNSUPPORTED    = "unsupported"
    INTERNAL       = "internal"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class GraftParseError(Exception):
    """
    Raised by parse() on any invalid GQL input.

    Attributes
    ----------
    message  : human-readable description of what went wrong
    kind     : ParseErrorKind — machine-readable category
    line     : 1-indexed source line (0 = unknown)
    column   : 1-indexed source column (0 = unknown)
    snippet  : the offending source line with a caret beneath the error
    hint     : optional actionable suggestion for the user
    """

    def __init__(
        self,
        message: str,
        *,
        kind:    ParseErrorKind = ParseErrorKind.SYNTAX,
        line:    int  = 0,
        column:  int  = 0,
        snippet: str  = "",
        hint:    str  = "",
    ) -> None:
        super().__init__(message)
        self.message  = message
        self.kind     = kind
        self.line     = line
        self.column   = column
        self.snippet  = snippet
        self.hint     = hint

    # ── Convenience constructors ─────────────────────────────────────────

    @classmethod
    def syntax(
        cls,
        message: str,
        *,
        line:    int = 0,
        column:  int = 0,
        snippet: str = "",
        hint:    str = "",
    ) -> "GraftParseError":
        """Unexpected token or character."""
        return cls(message, kind=ParseErrorKind.SYNTAX,
                   line=line, column=column, snippet=snippet, hint=hint)

    @classmethod
    def unexpected_eof(
        cls,
        message: str,
        *,
        line:    int = 0,
        column:  int = 0,
        snippet: str = "",
        hint:    str = "",
    ) -> "GraftParseError":
        """Query ended before a required clause was complete."""
        return cls(message, kind=ParseErrorKind.UNEXPECTED_EOF,
                   line=line, column=column, snippet=snippet, hint=hint)

    @classmethod
    def unsupported(
        cls,
        feature: str,
        *,
        hint:    str = "",
        line:    int = 0,
        column:  int = 0,
        snippet: str = "",
    ) -> "GraftParseError":
        """Construct is understood but not yet implemented."""
        return cls(
            f"{feature} is not supported in GQL v0.1",
            kind=ParseErrorKind.UNSUPPORTED,
            line=line, column=column, snippet=snippet, hint=hint,
        )

    @classmethod
    def internal(cls, message: str) -> "GraftParseError":
        """Should never happen — indicates a parser bug."""
        return cls(
            f"Internal parser error: {message}",
            kind=ParseErrorKind.INTERNAL,
            hint="This is a bug in graft_parser — please report it.",
        )

    # ── Formatting ───────────────────────────────────────────────────────

    def __str__(self) -> str:
        """
        Render a complete, human-readable error message.

        Format::

            GQL <kind> error at <line>:<col>: <message>
              <snippet line>
              <caret>
            Hint: <hint>
        """
        # Header line
        if self.line and self.column:
            header = (
                f"GQL {self.kind.value} error at "
                f"{self.line}:{self.column}: {self.message}"
            )
        else:
            header = f"GQL {self.kind.value} error: {self.message}"

        parts = [header]

        # Source snippet (indented 2 spaces for visual separation)
        if self.snippet:
            for snip_line in self.snippet.splitlines():
                parts.append(f"  {snip_line}")

        # Actionable hint
        if self.hint:
            parts.append(f"Hint: {self.hint}")

        return "\n".join(parts)

    def __repr__(self) -> str:
        return (
            f"GraftParseError(kind={self.kind.value!r}, "
            f"message={self.message!r}, "
            f"line={self.line}, column={self.column})"
        )
