from __future__ import annotations


class GraftParseError(Exception):
    """
    Raised by parse() on invalid GQL input.

    Attributes:
        message: human-readable description
        line:    1-indexed line number in the source
        column:  1-indexed column number
        snippet: the offending source line with a caret beneath the error
    """

    def __init__(
        self,
        message: str,
        line: int = 1,
        column: int = 1,
        snippet: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.snippet = snippet

    def __str__(self) -> str:
        parts = [f"GQL parse error at {self.line}:{self.column}: {self.message}"]
        if self.snippet:
            parts.append(self.snippet)
        return "\n".join(parts)
