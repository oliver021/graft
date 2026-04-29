"""
Abstract base class for language adapters.

All language adapters inherit from LanguageAdapter and implement extract().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class LanguageAdapter(ABC):
    """Abstract base for language-specific extraction."""

    LANGUAGE_NAME: str
    FILE_EXTENSIONS: list[str]

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
