# Decision: Daily Notes Extraction Foundation

Date: 2026-03-11

## Context
Mycelium's live ingest path is underutilized and MEMORY.md migration data is mostly redundant in agent context. We need an extraction path that captures cross-agent learning from daily note files and supports incremental processing.

## Decision
Implement a dedicated daily-notes extraction foundation with:

- `DailyNotesExtractor` in `src/mycelium/extraction/daily_notes.py`
- Incremental watermarks (`last_file_path`, `last_line_offset`) in `mycelium.extraction_state`
- API trigger endpoint: `POST /v1/extraction/run`
- Default workspace globs configurable via `MyceliumConfig.daily_note_workspaces`
- Optional one-time cleanup in extraction run: expire facts migrated from `memory_file`

## Rationale
- Keeps extraction asynchronous and outside the query critical path.
- Allows safe incremental ingest from continuously growing note files.
- Reuses existing `MyceliumClient.ingest()` boundary and trust/correction semantics.
- Makes nightly operations deterministic and externally triggerable.

## Consequences
- Requires new schema object (`mycelium.extraction_state`) via migration `002_extraction_state.sql`.
- Server now depends on OpenAI token configuration when extraction is triggered.
- Further refinement is still needed for correction detection quality and real-time correction hooks in plugins.
