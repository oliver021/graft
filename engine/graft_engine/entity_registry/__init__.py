"""
graft_engine.entity_registry — semantic layer between GQL and SQL.

Public API (unchanged from the previous single-file module):
    REGISTRY       : module-level EntityRegistry singleton
    EntityRegistry : class
    RegistryError  : exception
    All type constants and dataclasses re-exported for compiler use.
"""

from ._types import (
    STRING,
    INT,
    FLOAT,
    BOOL,
    JSON,
    FILTER_WHERE,
    FILTER_EXISTS,
    FILTER_NOT_EXISTS,
    FILTER_CTE,
    JOIN_INNER,
    JOIN_LEFT,
    FieldDef,
    JoinDef,
    TraversalDef,
    PredicateDef,
    EntityDef,
    PredicateApplication,
    ResolvedPath,
    RegistryError,
)
from ._registry import EntityRegistry
from ._entities import ALL_ENTITIES

# Module-level singleton — import this, don't construct your own.
REGISTRY = EntityRegistry(ALL_ENTITIES)

__all__ = [
    "REGISTRY",
    "EntityRegistry",
    "RegistryError",
    # type constants
    "STRING", "INT", "FLOAT", "BOOL", "JSON",
    # filter/join constants
    "FILTER_WHERE", "FILTER_EXISTS", "FILTER_NOT_EXISTS", "FILTER_CTE",
    "JOIN_INNER", "JOIN_LEFT",
    # dataclasses
    "FieldDef",
    "JoinDef",
    "TraversalDef",
    "PredicateDef",
    "EntityDef",
    "PredicateApplication",
    "ResolvedPath",
]
