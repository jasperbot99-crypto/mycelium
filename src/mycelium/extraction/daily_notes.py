"""Daily notes extraction pipeline for cross-agent memory ingestion."""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx

from mycelium.auth.tokens import TokenProvider, build_token_provider
from mycelium.domain.types import FactContent, SourceType
from mycelium.extraction.prompts import daily_notes_system_prompt, daily_notes_user_prompt

if TYPE_CHECKING:
    import asyncpg

    from mycelium.client.client import MyceliumClient
    from mycelium.http.protocols import AsyncJsonHttpClient

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4.1-mini"
_CORRECTION_TAGS = frozenset({"correction", "feedback", "decision", "review"})


class DailyNotesExtractionError(Exception):
    """Raised when daily notes extraction fails."""


@dataclass(frozen=True)
class DailyNotesWorkspaceConfig:
    workspace_key: str
    glob_pattern: str
    source_agent_id: str
    correction_authority: bool = False


@dataclass(frozen=True)
class ExtractionState:
    workspace_key: str
    last_file_path: str | None
    last_line_offset: int
    updated_at: datetime


@dataclass(frozen=True)
class ExtractedDailyFact:
    subject: str
    predicate: str
    object: str
    context: str | None = None
    tags: list[str] | None = None
    source_type: SourceType | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class WorkspaceExtractionStats:
    workspace_key: str
    files_seen: int = 0
    files_processed: int = 0
    facts_extracted: int = 0
    facts_ingested: int = 0
    facts_skipped: int = 0
    facts_failed: int = 0


@dataclass
class DailyNotesExtractionResult:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    workspaces: list[WorkspaceExtractionStats] = field(default_factory=list)
    expired_memory_migration_facts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_files_seen(self) -> int:
        return sum(item.files_seen for item in self.workspaces)

    @property
    def total_files_processed(self) -> int:
        return sum(item.files_processed for item in self.workspaces)

    @property
    def total_facts_extracted(self) -> int:
        return sum(item.facts_extracted for item in self.workspaces)

    @property
    def total_facts_ingested(self) -> int:
        return sum(item.facts_ingested for item in self.workspaces)

    @property
    def total_facts_skipped(self) -> int:
        return sum(item.facts_skipped for item in self.workspaces)

    @property
    def total_facts_failed(self) -> int:
        return sum(item.facts_failed for item in self.workspaces)


class DailyNotesFactExtractor(Protocol):
    async def extract(
        self,
        content: str,
        *,
        file_path: str,
        workspace_key: str,
        source_agent_id: str,
        correction_authority: bool,
    ) -> list[ExtractedDailyFact]:
        """Extract structured facts from daily note text."""
        ...


class ExtractionStateStore(Protocol):
    async def get(self, workspace_key: str) -> ExtractionState | None:
        ...

    async def upsert(self, state: ExtractionState) -> None:
        ...


class InMemoryExtractionStateStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self._state: dict[str, ExtractionState] = {}

    async def get(self, workspace_key: str) -> ExtractionState | None:
        return self._state.get(workspace_key)

    async def upsert(self, state: ExtractionState) -> None:
        self._state[state.workspace_key] = state


class PostgresExtractionStateStore:
    """Postgres-backed extraction watermark store."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_table(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mycelium.extraction_state (
                    workspace_key TEXT PRIMARY KEY,
                    last_file_path TEXT,
                    last_line_offset INT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    async def get(self, workspace_key: str) -> ExtractionState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT workspace_key, last_file_path, last_line_offset, updated_at
                FROM mycelium.extraction_state
                WHERE workspace_key = $1
                """,
                workspace_key,
            )
        if row is None:
            return None
        return ExtractionState(
            workspace_key=row["workspace_key"],
            last_file_path=row["last_file_path"],
            last_line_offset=row["last_line_offset"],
            updated_at=row["updated_at"],
        )

    async def upsert(self, state: ExtractionState) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mycelium.extraction_state (
                    workspace_key, last_file_path, last_line_offset, updated_at
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (workspace_key) DO UPDATE SET
                    last_file_path = EXCLUDED.last_file_path,
                    last_line_offset = EXCLUDED.last_line_offset,
                    updated_at = EXCLUDED.updated_at
                """,
                state.workspace_key,
                state.last_file_path,
                state.last_line_offset,
                state.updated_at,
            )


class OpenAIDailyNotesFactExtractor:
    """OpenAI-backed extraction for daily notes files."""

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
        workspace_key: str,
        source_agent_id: str,
        correction_authority: bool,
    ) -> list[ExtractedDailyFact]:
        payload: dict[str, object] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": daily_notes_system_prompt()},
                {
                    "role": "user",
                    "content": daily_notes_user_prompt(
                        file_path=file_path,
                        workspace_key=workspace_key,
                        source_agent_id=source_agent_id,
                        correction_authority=correction_authority,
                        content=content,
                    ),
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
            raise DailyNotesExtractionError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = cast("dict[str, Any]", response.json())
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DailyNotesExtractionError("OpenAI response missing choices")
        first = cast("dict[str, Any]", choices[0])
        message = cast("dict[str, Any]", first.get("message", {}))
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            raise DailyNotesExtractionError("OpenAI response missing message content")
        return _parse_extraction_json(
            raw_content,
            correction_authority=correction_authority,
        )

    async def close(self) -> None:
        await self._client.aclose()


class DailyNotesExtractor:
    """Incremental extractor for workspace daily notes."""

    def __init__(
        self,
        *,
        workspaces: list[DailyNotesWorkspaceConfig],
        state_store: ExtractionStateStore,
        fact_extractor: DailyNotesFactExtractor,
    ) -> None:
        self._workspaces = workspaces
        self._state_store = state_store
        self._fact_extractor = fact_extractor

    async def run(
        self,
        client: MyceliumClient,
        *,
        pool: asyncpg.Pool | None = None,
        expire_memory_migration_facts: bool = False,
    ) -> DailyNotesExtractionResult:
        result = DailyNotesExtractionResult()
        if expire_memory_migration_facts:
            if pool is None:
                raise ValueError("pool is required when expire_memory_migration_facts=True")
            result.expired_memory_migration_facts = await expire_memory_migration_facts_once(
                pool
            )

        for workspace in self._workspaces:
            stats = await self._process_workspace(client, workspace)
            result.workspaces.append(stats)
        result.finished_at = datetime.now(UTC)
        return result

    async def _process_workspace(
        self,
        client: MyceliumClient,
        workspace: DailyNotesWorkspaceConfig,
    ) -> WorkspaceExtractionStats:
        files = _resolve_workspace_files(workspace.glob_pattern)
        stats = WorkspaceExtractionStats(
            workspace_key=workspace.workspace_key,
            files_seen=len(files),
        )
        if not files:
            return stats

        state = await self._state_store.get(workspace.workspace_key)
        pending = _files_to_process(files, state)
        if not pending:
            return stats

        files_processed = 0
        facts_extracted = 0
        facts_ingested = 0
        facts_skipped = 0
        facts_failed = 0

        for file_path in pending:
            raw = file_path.read_text(encoding="utf-8")
            lines = raw.splitlines()
            line_offset = _line_offset_for_file(file_path, state)
            if line_offset >= len(lines):
                await self._state_store.upsert(
                    ExtractionState(
                        workspace_key=workspace.workspace_key,
                        last_file_path=str(file_path),
                        last_line_offset=len(lines),
                        updated_at=datetime.now(UTC),
                    )
                )
                continue

            files_processed += 1
            chunk = "\n".join(lines[line_offset:])
            extracted = await self._fact_extractor.extract(
                chunk,
                file_path=str(file_path),
                workspace_key=workspace.workspace_key,
                source_agent_id=workspace.source_agent_id,
                correction_authority=workspace.correction_authority,
            )
            facts_extracted += len(extracted)

            for item in extracted:
                source_type = item.source_type or SourceType.AGENT_EXTRACTION
                tags = item.tags or []
                metadata = dict(item.metadata or {})
                metadata["extracted_from"] = "daily_note"
                metadata["workspace_key"] = workspace.workspace_key
                metadata["file_path"] = str(file_path)

                try:
                    ingest_result = await client.ingest(
                        content=FactContent(
                            subject=item.subject,
                            predicate=item.predicate,
                            object=item.object,
                            context=item.context,
                        ),
                        source_type=source_type,
                        tags=tags,
                        metadata=metadata,
                    )
                except Exception:
                    facts_failed += 1
                    continue

                if ingest_result.accepted:
                    facts_ingested += 1
                else:
                    facts_skipped += 1

            await self._state_store.upsert(
                ExtractionState(
                    workspace_key=workspace.workspace_key,
                    last_file_path=str(file_path),
                    last_line_offset=len(lines),
                    updated_at=datetime.now(UTC),
                )
            )

        return WorkspaceExtractionStats(
            workspace_key=workspace.workspace_key,
            files_seen=stats.files_seen,
            files_processed=files_processed,
            facts_extracted=facts_extracted,
            facts_ingested=facts_ingested,
            facts_skipped=facts_skipped,
            facts_failed=facts_failed,
        )


async def expire_memory_migration_facts_once(pool: asyncpg.Pool) -> int:
    """Expire legacy memory_file migration facts."""
    async with pool.acquire() as conn:
        command = await conn.execute(
            """
            UPDATE mycelium.facts
            SET expired_at = now()
            WHERE expired_at IS NULL
              AND metadata->>'migrated_from' = 'memory_file'
            """
        )
    return _parse_rows_from_execute(command)


def default_daily_notes_workspaces() -> list[DailyNotesWorkspaceConfig]:
    """Default workspace globs used for nightly extraction."""
    return [
        DailyNotesWorkspaceConfig(
            workspace_key="main",
            glob_pattern="~/.openclaw/workspace/memory/202*.md",
            source_agent_id="main",
            correction_authority=True,
        ),
        DailyNotesWorkspaceConfig(
            workspace_key="jasper-code",
            glob_pattern="~/Projects/jasper-code-workspace/memory/202*.md",
            source_agent_id="jasper-code",
        ),
        DailyNotesWorkspaceConfig(
            workspace_key="jasper-trader",
            glob_pattern="~/Projects/jasper-trader-workspace/memory/202*.md",
            source_agent_id="jasper-trader",
        ),
        DailyNotesWorkspaceConfig(
            workspace_key="jasper-research",
            glob_pattern="~/Projects/jasper-research-workspace/memory/202*.md",
            source_agent_id="jasper-research",
        ),
        DailyNotesWorkspaceConfig(
            workspace_key="jasper-planner",
            glob_pattern="~/Projects/jasper-planner-workspace/memory/202*.md",
            source_agent_id="jasper-planner",
        ),
    ]


def _files_to_process(files: list[Path], state: ExtractionState | None) -> list[Path]:
    if state is None or state.last_file_path is None:
        return files
    state_path = Path(state.last_file_path)
    if state_path not in files:
        return files
    start = files.index(state_path)
    return files[start:]


def _line_offset_for_file(file_path: Path, state: ExtractionState | None) -> int:
    if state is None or state.last_file_path is None:
        return 0
    if Path(state.last_file_path) != file_path:
        return 0
    return max(0, state.last_line_offset)


def _resolve_workspace_files(glob_pattern: str) -> list[Path]:
    expanded = str(Path(glob_pattern).expanduser())
    return sorted([Path(item) for item in glob.glob(expanded)])


def _parse_extraction_json(
    content: str,
    *,
    correction_authority: bool,
) -> list[ExtractedDailyFact]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed_obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DailyNotesExtractionError(f"Invalid extractor JSON: {exc}") from exc
    if not isinstance(parsed_obj, dict):
        raise DailyNotesExtractionError("Extractor JSON root must be an object")
    parsed = cast("dict[str, object]", parsed_obj)
    raw_facts = parsed.get("facts")
    if not isinstance(raw_facts, list):
        raise DailyNotesExtractionError("Extractor JSON must include a facts list")

    facts: list[ExtractedDailyFact] = []
    for raw_item in cast("list[object]", raw_facts):
        if not isinstance(raw_item, dict):
            continue
        item = cast("dict[str, object]", raw_item)
        subject = _as_non_empty_str(item.get("subject"))
        predicate = _as_non_empty_str(item.get("predicate"))
        obj = _as_non_empty_str(item.get("object"))
        if subject is None or predicate is None or obj is None:
            continue
        tags = _as_tags(item.get("tags"))
        facts.append(
            ExtractedDailyFact(
                subject=subject,
                predicate=predicate,
                object=obj,
                context=_as_optional_str(item.get("context")),
                tags=tags,
                source_type=_parse_source_type(
                    item.get("source_type"),
                    correction_authority=correction_authority,
                    tags=tags,
                ),
            )
        )
    return facts


def _parse_source_type(
    value: object | None,
    *,
    correction_authority: bool,
    tags: list[str],
) -> SourceType:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            try:
                return SourceType(normalized)
            except ValueError:
                pass

    lowered_tags = {tag.lower() for tag in tags}
    if correction_authority and lowered_tags.intersection(_CORRECTION_TAGS):
        return SourceType.HUMAN_CORRECTION
    return SourceType.AGENT_EXTRACTION


def _parse_rows_from_execute(command: str) -> int:
    # asyncpg returns e.g. "UPDATE 309"
    parts = command.strip().split(" ")
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


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
        for item in cast("list[object]", value):
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
