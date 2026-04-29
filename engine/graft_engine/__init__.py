"""
graft_engine — query compiler and executor.
"""

from .compiler import compile, CompiledQuery, CompileError  # noqa: F401
from .executor import run, QueryResult, ExecuteError  # noqa: F401
from .entity_registry import (
    REGISTRY,
    EntityRegistry,
    EntityDef,
    FieldDef,
    JoinDef,
    TraversalDef,
    PredicateDef,
    PredicateApplication,
    ResolvedPath,
    RegistryError,
    # type constants
    STRING, INT, FLOAT, BOOL, JSON,
    # filter kinds
    FILTER_WHERE, FILTER_EXISTS, FILTER_NOT_EXISTS, FILTER_CTE,
    # join kinds
    JOIN_INNER, JOIN_LEFT,
)

__all__ = [
    "compile",
    "CompiledQuery",
    "CompileError",
    "run",
    "QueryResult",
    "ExecuteError",
    "REGISTRY",
    "EntityRegistry",
    "EntityDef",
    "FieldDef",
    "JoinDef",
    "TraversalDef",
    "PredicateDef",
    "PredicateApplication",
    "ResolvedPath",
    "RegistryError",
    "STRING", "INT", "FLOAT", "BOOL", "JSON",
    "FILTER_WHERE", "FILTER_EXISTS", "FILTER_NOT_EXISTS", "FILTER_CTE",
    "JOIN_INNER", "JOIN_LEFT",
]
