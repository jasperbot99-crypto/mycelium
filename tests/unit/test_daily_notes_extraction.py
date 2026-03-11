from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mycelium.domain.types import Fact, FactContent, SourceType
from mycelium.extraction.daily_notes import (
    DailyNotesExtractor,
    DailyNotesWorkspaceConfig,
    ExtractedDailyFact,
    InMemoryExtractionStateStore,
    _parse_extraction_json,
)
from mycelium.pipelines.ingest import IngestResult


class _FakeExtractor:
    def __init__(self, facts: list[ExtractedDailyFact]) -> None:
        self._facts = facts
        self.received: list[str] = []

    async def extract(
        self,
        content: str,
        *,
        file_path: str,
        workspace_key: str,
        source_agent_id: str,
        correction_authority: bool,
    ) -> list[ExtractedDailyFact]:
        del file_path, workspace_key, source_agent_id, correction_authority
        self.received.append(content)
        return self._facts


@dataclass
class _FakeClient:
    ingests: list[FactContent]

    async def ingest(
        self,
        content: FactContent,
        source_type: SourceType,
        tags: list[str] | None = None,
        derived_from: list[object] | None = None,
        metadata: dict[str, object] | None = None,
        initial_confidence: float | None = None,
    ) -> IngestResult:
        del source_type, tags, derived_from, metadata, initial_confidence
        self.ingests.append(content)
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="extractor",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.7,
            trust_score=0.7,
            valid_from=datetime.now(UTC),
        )
        return IngestResult(fact=fact)


@pytest.mark.asyncio
async def test_daily_notes_extractor_uses_incremental_line_offset(tmp_path) -> None:
    notes_dir = tmp_path / "memory"
    notes_dir.mkdir()
    note_file = notes_dir / "2026-03-11.md"
    note_file.write_text("line1\nline2\n", encoding="utf-8")

    workspace = DailyNotesWorkspaceConfig(
        workspace_key="planner",
        glob_pattern=str(notes_dir / "202*.md"),
        source_agent_id="jasper-planner",
    )
    fake_extractor = _FakeExtractor(
        [
            ExtractedDailyFact(
                subject="pipeline",
                predicate="has_status",
                object="running",
            )
        ]
    )
    state_store = InMemoryExtractionStateStore()
    client = _FakeClient(ingests=[])

    runner = DailyNotesExtractor(
        workspaces=[workspace],
        state_store=state_store,
        fact_extractor=fake_extractor,
    )
    first = await runner.run(client)
    assert first.total_files_processed == 1
    assert first.total_facts_ingested == 1
    assert fake_extractor.received == ["line1\nline2"]

    note_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    second = await runner.run(client)
    assert second.total_files_processed == 1
    assert second.total_facts_ingested == 1
    assert fake_extractor.received[-1] == "line3"


def test_parse_extraction_json_infers_human_correction_from_tags() -> None:
    payload = """
    {
      "facts": [
        {
          "subject": "jasper-research",
          "predicate": "has_status",
          "object": "needs_correction",
          "tags": ["feedback"]
        }
      ]
    }
    """
    parsed = _parse_extraction_json(payload, correction_authority=True)
    assert len(parsed) == 1
    assert parsed[0].source_type == SourceType.HUMAN_CORRECTION
