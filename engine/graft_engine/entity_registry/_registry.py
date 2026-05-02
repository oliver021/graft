"""EntityRegistry — central lookup for GQL entities, traversals, and fields."""

from __future__ import annotations

from graft_parser.ast_nodes import EntityPath, FieldTraversal, PredicateTraversal

from ._types import (
    EntityDef,
    FieldDef,
    JoinDef,
    PredicateApplication,
    RegistryError,
    ResolvedPath,
)


class EntityRegistry:
    """
    Central lookup for all GQL entities, fields, traversals, and predicates.

    Access the module-level singleton:
        ``from graft_engine.entity_registry import REGISTRY``
    """

    def __init__(self, entities: list[EntityDef]) -> None:
        self._entities: dict[str, EntityDef] = {e.name: e for e in entities}

    # ── Entity lookup ────────────────────────────────────────────────────

    def get(self, name: str) -> EntityDef:
        """Return an EntityDef by name. Raises RegistryError if unknown."""
        try:
            return self._entities[name]
        except KeyError:
            roots = self.root_names()
            raise RegistryError(
                f"Unknown entity '{name}'. Root entities are: {roots}"
            )

    def root_names(self) -> list[str]:
        """Return names of all root (directly queryable) entities."""
        return [e.name for e in self._entities.values() if e.is_root]

    def all_names(self) -> list[str]:
        """Return names of all entities (root + virtual)."""
        return list(self._entities.keys())

    # ── Path resolution ──────────────────────────────────────────────────

    def resolve_path(self, entity_path: EntityPath) -> ResolvedPath:
        """
        Walk entity_path and produce a ResolvedPath ready for the compiler.

        Raises RegistryError on:
        - Unknown root entity
        - Unknown traversal or predicate name
        - Wrong predicate arity
        """
        current = self.get(entity_path.root)
        extra_joins: list[JoinDef]              = []
        extra_where: list[tuple[str, str, str]] = []
        pred_apps:   list[PredicateApplication] = []
        injected:    dict[str, FieldDef]        = {}

        for step in entity_path.traversals:
            if isinstance(step, FieldTraversal):
                tdef = current.traversals.get(step.name)
                if tdef is None:
                    raise RegistryError(
                        f"Entity '{current.name}' has no traversal '{step.name}'. "
                        f"Available: {list(current.traversals)}"
                    )
                extra_joins.extend(tdef.extra_joins)
                extra_where.extend(tdef.extra_where)
                current = self.get(tdef.terminal_entity)

            elif isinstance(step, PredicateTraversal):
                pdef = current.predicates.get(step.name)
                if pdef is None:
                    raise RegistryError(
                        f"Entity '{current.name}' has no predicate '{step.name}'. "
                        f"Available: {list(current.predicates)}"
                    )
                if pdef.arity is not None and len(step.args) != pdef.arity:
                    raise RegistryError(
                        f"Predicate '{step.name}' expects {pdef.arity} arg(s), "
                        f"got {len(step.args)}"
                    )
                pred_apps.append(PredicateApplication(predicate=pdef, args=list(step.args)))
                for f in pdef.injected_fields:
                    injected[f.gql_name] = f
                if pdef.terminal_entity is not None:
                    current = self.get(pdef.terminal_entity)

        return ResolvedPath(
            terminal=current,
            extra_joins=extra_joins,
            extra_where=extra_where,
            predicate_applications=pred_apps,
            injected_fields=injected,
        )

    # ── Field resolution ─────────────────────────────────────────────────

    def resolve_field(self, resolved: ResolvedPath, gql_name: str) -> FieldDef:
        """
        Resolve a GQL field name against the terminal entity and injected fields.

        Injected fields (from predicates like callDepth) take precedence.

        Raises RegistryError if the field is not found.
        """
        fdef = resolved.resolve_field(gql_name)
        if fdef is None:
            available = sorted(
                set(resolved.terminal.fields) | set(resolved.injected_fields)
            )
            raise RegistryError(
                f"Field '{gql_name}' not found on entity '{resolved.terminal.name}'. "
                f"Available: {available}"
            )
        return fdef

    def field_names(self, entity_name: str) -> list[str]:
        """List all GQL field names for a named entity."""
        return list(self.get(entity_name).fields)
