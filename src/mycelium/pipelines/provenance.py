"""Phase 4 causal provenance chain traversal."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mycelium.domain.types import Fact, RelationType

if TYPE_CHECKING:
    from uuid import UUID

    from mycelium.storage.protocols import FactRepository, RelationRepository


@dataclass(frozen=True)
class ProvenanceEntry:
    """A fact in a provenance walk with its traversal depth."""

    fact: Fact
    depth: int


class ProvenancePipeline:
    """Build causal provenance chains for facts."""

    def __init__(
        self,
        fact_repo: FactRepository,
        relation_repo: RelationRepository,
    ) -> None:
        self._fact_repo = fact_repo
        self._relation_repo = relation_repo

    async def trace_chain(self, fact_id: UUID, max_depth: int = 16) -> list[ProvenanceEntry]:
        """Trace causal ancestors for a fact up to `max_depth` hops."""
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        visited: set[UUID] = set()
        queue: deque[tuple[UUID, int]] = deque([(fact_id, 0)])
        entries: list[ProvenanceEntry] = []

        while queue:
            current_id, depth = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            current = await self._fact_repo.get_by_id(current_id)
            if current is None:
                continue

            entries.append(ProvenanceEntry(fact=current, depth=depth))

            if depth >= max_depth:
                continue

            parent_ids = set(current.derived_from)
            if current.supersedes is not None:
                parent_ids.add(current.supersedes)

            relations = await self._relation_repo.find_for_fact(current_id)
            for relation in relations:
                if relation.source_fact_id != current_id:
                    continue
                if relation.relation_type in {
                    RelationType.DERIVED_FROM,
                    RelationType.SUPERSEDES,
                    RelationType.DEPENDS_ON,
                }:
                    parent_ids.add(relation.target_fact_id)

            for parent_id in sorted(parent_ids, key=str):
                if parent_id not in visited:
                    queue.append((parent_id, depth + 1))

        return entries
