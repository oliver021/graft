"""
SQLAlchemy Core schema definitions for the code indexer.

All tables use dialect-agnostic types. JSON columns fall back to TEXT
on databases that don't support native JSON (e.g. SQLite < 3.38).

Usage:
    from code_indexer.schema import metadata, create_all

    engine = create_engine("sqlite:///index.db")
    create_all(engine)
"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy import JSON

metadata = MetaData()

# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
files_table = Table(
    "files",
    metadata,
    Column("id", String(36), primary_key=True),          # UUID v4
    Column("path", Text, nullable=False),                 # absolute or relative path
    Column("language", String(64), nullable=False),       # "python", "javascript", etc.
    Column("content_hash", String(64), nullable=False),   # SHA-256 hex digest
    Column("scanned_at", String(32), nullable=False),     # ISO-8601 timestamp
    Index("ix_files_path", "path"),
    Index("ix_files_hash", "content_hash"),
)

# ---------------------------------------------------------------------------
# symbols  (functions, methods, classes, lambdas)
# ---------------------------------------------------------------------------
symbols_table = Table(
    "symbols",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("file_id", String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(512), nullable=False),
    Column("kind", String(64), nullable=False),           # function | method | class | lambda
    Column("start_byte", Integer, nullable=False),
    Column("end_byte", Integer, nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    # Structured signature: {"params": [...], "return_type": "str | None"}
    Column("signature", JSON, nullable=True),
    # Parent symbol id for nested functions / methods inside a class
    Column("parent_id", String(36), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True),
    Index("ix_symbols_file_id", "file_id"),
    Index("ix_symbols_name", "name"),
    Index("ix_symbols_kind", "kind"),
)

# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------
expressions_table = Table(
    "expressions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("file_id", String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
    # The symbol (function/method) this expression belongs to
    Column("symbol_id", String(36), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True),
    # Self-referential: parent expression in the tree (NULL = direct child of symbol body)
    Column("parent_id", String(36), ForeignKey("expressions.id", ondelete="CASCADE"), nullable=True),
    # call | binary | assignment | return | declaration | import | attribute | literal | conditional
    Column("kind", String(64), nullable=False),
    # Raw source text of this node (sliced from original source)
    Column("source_text", Text, nullable=False),
    Column("start_byte", Integer, nullable=False),
    Column("end_byte", Integer, nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    # Depth from the symbol body root (root = 0)
    Column("depth", Integer, nullable=False, default=0),
    # Kind-specific extras:
    #   call        -> {"callee": "print", "arg_count": 2, "is_method": true}
    #   binary      -> {"operator": "+"}
    #   assignment  -> {"target": "x", "is_augmented": false}
    #   declaration -> {"name": "x", "type_annotation": "int"}
    #   import      -> {"module": "os.path", "names": ["join", "exists"]}
    Column("extra", JSON, nullable=True),
    Index("ix_expressions_symbol_id", "symbol_id"),
    Index("ix_expressions_file_id", "file_id"),
    Index("ix_expressions_kind", "kind"),
    Index("ix_expressions_parent_id", "parent_id"),
)

# ---------------------------------------------------------------------------
# references  (call graph edges)
# ---------------------------------------------------------------------------
references_table = Table(
    "references",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("file_id", String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
    # The expression (call_expression) that creates this reference
    Column("expression_id", String(36), ForeignKey("expressions.id", ondelete="CASCADE"), nullable=False),
    # The symbol that contains this call
    Column("from_symbol_id", String(36), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=True),
    # Unresolved callee name as it appears in source
    Column("callee_name", String(512), nullable=False),
    # Resolved target symbol — NULL until resolution pass runs
    Column("to_symbol_id", String(36), ForeignKey("symbols.id", ondelete="SET NULL"), nullable=True),
    Index("ix_references_from_symbol_id", "from_symbol_id"),
    Index("ix_references_callee_name", "callee_name"),
)


def create_all(engine) -> None:  # type: ignore[no-untyped-def]
    """Create all tables if they don't exist."""
    metadata.create_all(engine)


def drop_all(engine) -> None:  # type: ignore[no-untyped-def]
    """Drop all tables. Useful for tests."""
    metadata.drop_all(engine)
