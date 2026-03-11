"""Memory file migration extractor — SPEC Section 7.8.

This migration source is intentionally LLM-assisted because MEMORY.md-style files
are semi-structured and inconsistent across agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx

from mycelium.auth.tokens import TokenProvider, build_token_provider
from mycelium.domain.types import FactContent, SourceType
from mycelium.migration.base import (
    MIGRATION_CONFIDENCE,
    MigrationRecord,
    MigrationResult,
    MigrationSource,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mycelium.client.client import MyceliumClient
    from mycelium.http.protocols import AsyncJsonHttpClient
    from mycelium.ops.logger import OpsLogger

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4.1-mini"


class MemoryExtractionError(Exception):
    """Raised when memory file extraction fails."""


class MemoryFactExtractor(Protocol):
    """LLM-assisted extractor interface for memory files."""

    async def extract(
        self,
        content: str,
        *,
        file_path: str,
        default_agent_id: str = "migration-agent",
    ) -> list[MigrationRecord]:
        """Extract structured migration records from memory file content."""
        ...


@dataclass(frozen=True)
class ExtractorFact:
    subject: str
    predicate: str
    object: str
    context: str | None = None
    tags: list[str] | None = None
    source_agent_id: str | None = None


class OpenAIMemoryFactExtractor:
    """OpenAI-backed extractor for MEMORY.md-like files."""

    def __init__(
        self,
        api_key: str | None = None,
        token_provider: TokenProvider | None = None,
        *,
        model: str = _DEFAULT_MODEL,
        timeout: float = 180.0,
    ) -> None:
        self._token_provider = build_token_provider(
            api_key,
            env_var="OPENAI_API_KEY",
            token_provider=token_provider,
            require_token=True,
        )
        self._model = model
        self._client: AsyncJsonHttpClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"Content-Type": "application/json"},
        )

    @property
    def http_client(self) -> AsyncJsonHttpClient:
        return self._client

    @http_client.setter
    def http_client(self, client: AsyncJsonHttpClient) -> None:
        self._client = client

    async def extract(
        self,
        content: str,
        *,
        file_path: str,
        default_agent_id: str = "migration-agent",
    ) -> list[MigrationRecord]:
        payload: dict[str, object] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": _user_prompt(file_path=file_path, content=content),
                },
            ],
        }

        token = await self._token_provider.get_token()
        response = await self._client.post(
            _OPENAI_CHAT_COMPLETIONS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            raise MemoryExtractionError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = cast("dict[str, Any]", response.json())
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise MemoryExtractionError("OpenAI response missing choices")

        first = cast("dict[str, Any]", choices[0])
        message = cast("dict[str, Any]", first.get("message", {}))
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            raise MemoryExtractionError("OpenAI response missing message content")

        parsed = _parse_extraction_json(raw_content)
        return _to_migration_records(
            parsed,
            file_path=file_path,
            default_agent_id=default_agent_id,
        )

    async def close(self) -> None:
        await self._client.aclose()


def _system_prompt() -> str:
    return (
        "Extract durable factual statements from the memory file. "
        "Return ONLY valid JSON with shape: "
        '{"facts":[{"subject":"...","predicate":"...","object":"...",'
        '"context":"optional","tags":["optional"],"source_agent_id":"optional"}]}. '
        "Use concise canonical-style predicates when possible."
    )


def _user_prompt(*, file_path: str, content: str) -> str:
    return (
        f"File: {file_path}\n"
        "Extract factual records suitable for a shared memory graph.\n"
        "Skip vague aspirations, TODOs, and stylistic prose.\n"
        "Memory content:\n"
        f"{content}"
    )


def _parse_extraction_json(content: str) -> list[ExtractorFact]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed_obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MemoryExtractionError(f"Invalid extractor JSON: {exc}") from exc

    if not isinstance(parsed_obj, dict):
        raise MemoryExtractionError("Extractor JSON root must be an object")
    parsed = cast("dict[str, object]", parsed_obj)

    raw_facts = parsed.get("facts")
    if not isinstance(raw_facts, list):
        raise MemoryExtractionError("Extractor JSON must include a facts list")

    facts: list[ExtractorFact] = []
    raw_fact_items = cast("list[object]", raw_facts)
    for raw_item in raw_fact_items:
        if not isinstance(raw_item, dict):
            continue
        item = cast("dict[str, object]", raw_item)
        subject = _as_non_empty_str(item.get("subject"))
        predicate = _as_non_empty_str(item.get("predicate"))
        obj = _as_non_empty_str(item.get("object"))
        if subject is None or predicate is None or obj is None:
            continue
        facts.append(
            ExtractorFact(
                subject=subject,
                predicate=predicate,
                object=obj,
                context=_as_optional_str(item.get("context")),
                tags=_as_tags(item.get("tags")),
                source_agent_id=_as_optional_str(item.get("source_agent_id")),
            )
        )
    return facts


def _to_migration_records(
    facts: list[ExtractorFact],
    *,
    file_path: str,
    default_agent_id: str,
) -> list[MigrationRecord]:
    path_name = Path(file_path).name
    records: list[MigrationRecord] = []
    for idx, fact in enumerate(facts):
        records.append(
            MigrationRecord(
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                context=fact.context,
                tags=fact.tags or [],
                source_agent_id=fact.source_agent_id or default_agent_id,
                original_id=f"{path_name}:{idx}",
                original_created_at=None,
            )
        )
    return records


def _as_non_empty_str(value: object | None) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered if rendered else None


def _as_optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    rendered = str(value).strip()
    return rendered if rendered else None


def _as_tags(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        tags: list[str] = []
        raw_values = cast("list[object]", value)
        for item in raw_values:
            rendered = _as_non_empty_str(item)
            if rendered is not None:
                tags.append(rendered)
        return tags
    single = _as_non_empty_str(value)
    if single is None:
        return []
    if "," in single:
        return [tag.strip() for tag in single.split(",") if tag.strip()]
    return [single]


async def extract_from_memory_file(
    file_path: str | Path,
    extractor: MemoryFactExtractor,
    *,
    default_agent_id: str = "migration-agent",
) -> list[MigrationRecord]:
    """Extract migration records from a single memory file."""
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    return await extractor.extract(
        content,
        file_path=str(path),
        default_agent_id=default_agent_id,
    )


async def extract_from_memory_files(
    file_paths: list[str | Path],
    extractor: MemoryFactExtractor,
    *,
    default_agent_id: str = "migration-agent",
) -> list[MigrationRecord]:
    """Extract migration records from multiple memory files."""
    records: list[MigrationRecord] = []
    for path in file_paths:
        extracted = await extract_from_memory_file(
            path,
            extractor,
            default_agent_id=default_agent_id,
        )
        records.extend(extracted)
    return records


async def import_memory_file_records(
    records: list[MigrationRecord],
    client: MyceliumClient,
    ops_logger: OpsLogger | None = None,
) -> MigrationResult:
    """Ingest extracted memory-file records into Mycelium."""
    result = MigrationResult(
        source=MigrationSource.MEMORY_FILE,
        total_records=len(records),
    )

    for record in records:
        try:
            content = FactContent(
                subject=record.subject,
                predicate=record.predicate,
                object=record.object,
                context=record.context,
            )
            ingest_result = await client.ingest(
                content=content,
                source_type=SourceType.AGENT_EXTRACTION,
                tags=record.tags,
                initial_confidence=MIGRATION_CONFIDENCE,
                metadata={
                    "migrated_from": MigrationSource.MEMORY_FILE.value,
                    "migration_date": datetime.now(UTC).isoformat(),
                    "original_id": record.original_id,
                },
            )
            if ingest_result.accepted:
                result.ingested += 1
            else:
                result.skipped += 1
                reason = str(ingest_result.rejection) if ingest_result.rejection else "unknown"
                result.errors.append(
                    f"Record {record.original_id} skipped: {reason}"
                )
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"Record {record.original_id} failed: {exc}")

    result.finished_at = datetime.now(UTC)

    if ops_logger is not None:
        await ops_logger.log(
            "migration",
            "completed",
            detail={
                "source": MigrationSource.MEMORY_FILE.value,
                "total": result.total_records,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "success_rate": result.success_rate,
            },
        )

    return result


async def extract_and_import_memory_files(
    file_paths: list[str | Path],
    extractor: MemoryFactExtractor,
    client: MyceliumClient,
    *,
    ops_logger: OpsLogger | None = None,
    default_agent_id: str = "migration-agent",
) -> MigrationResult:
    """Convenience helper: extract facts from files and import into Mycelium."""
    records = await extract_from_memory_files(
        file_paths,
        extractor,
        default_agent_id=default_agent_id,
    )
    return await import_memory_file_records(records, client, ops_logger=ops_logger)


async def extract_memory_file_with_callable(
    file_path: str | Path,
    extractor: Callable[[str], Awaitable[list[dict[str, object]]]],
    *,
    default_agent_id: str = "migration-agent",
) -> list[MigrationRecord]:
    """Adapter for simple callable-based extractors used in tests/tools."""
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    rows = await extractor(content)
    facts: list[ExtractorFact] = []
    for row in rows:
        subject = _as_non_empty_str(row.get("subject"))
        predicate = _as_non_empty_str(row.get("predicate"))
        obj = _as_non_empty_str(row.get("object"))
        if subject is None or predicate is None or obj is None:
            continue
        facts.append(
            ExtractorFact(
                subject=subject,
                predicate=predicate,
                object=obj,
                context=_as_optional_str(row.get("context")),
                tags=_as_tags(row.get("tags")),
                source_agent_id=_as_optional_str(row.get("source_agent_id")),
            )
        )
    return _to_migration_records(
        facts,
        file_path=str(path),
        default_agent_id=default_agent_id,
    )
