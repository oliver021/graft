"""
Abstract base class for language adapters.

All language adapters inherit from LanguageAdapter and implement extract().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Maximum length of source_text stored per expression.
# Longer values are truncated with a '…' suffix to keep the DB lean.
MAX_SOURCE_TEXT: int = 512

# Default cap on total expressions extracted per file.
# Prevents unbounded DB growth for generated / minified files.
MAX_EXPRESSIONS_PER_FILE: int = 10_000


@dataclass
class RawSymbol:
    """Extracted symbol (function, method, class, lambda)."""

    name: str
    kind: str  # "function" | "method" | "class" | "lambda"
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    local_key: str = ""  # unique within file (e.g. "12:45")
    signature: dict[str, Any] | None = None
    parent_local_key: str | None = None


@dataclass
class RawExpression:
    """Extracted expression inside a symbol body."""

    kind: str  # "call" | "binary" | "assignment" | "return" | "import" | ...
    source_text: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    local_key: str = ""
    symbol_local_key: str | None = None
    parent_local_key: str | None = None
    depth: int = 0
    extra: dict[str, Any] | None = None


@dataclass
class RawReference:
    """A call or reference from one symbol to another."""

    callee_name: str
    expression_local_key: str
    from_symbol_local_key: str | None = None


@dataclass
class RawExtraction:
    """Result of extracting code structure with a language adapter."""

    symbols: list[RawSymbol] = field(default_factory=list)
    expressions: list[RawExpression] = field(default_factory=list)
    references: list[RawReference] = field(default_factory=list)
    # Number of ERROR nodes found in the parse tree (tree-sitter syntax errors).
    parse_error_count: int = 0
    # True when expression extraction was stopped early due to MAX_EXPRESSIONS_PER_FILE.
    expressions_truncated: bool = False


def _truncate_source(text: str, limit: int = MAX_SOURCE_TEXT) -> str:
    """Truncate source_text to `limit` characters, appending '…' if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class LanguageAdapter(ABC):
    """Abstract base for language-specific extraction."""

    LANGUAGE_NAME: str
    FILE_EXTENSIONS: list[str]
    MAX_EXPRESSIONS: int = MAX_EXPRESSIONS_PER_FILE

    @abstractmethod
    def extract(self, source: str) -> RawExtraction:
        """
        Parse source code and extract structure.

        Args:
            source: raw source code string

        Returns:
            RawExtraction with symbols, expressions, and references
        """
        pass
