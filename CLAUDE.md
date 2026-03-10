# Mycelium — Project Instructions

## What This Is

Multi-agent memory and coordination system. See [SPEC.md](./SPEC.md) for what/why, [ARCHITECTURE.md](./ARCHITECTURE.md) for how.

## Source of Truth

1. **SPEC.md** — defines what we build and why. Do not contradict it.
2. **ARCHITECTURE.md** — defines how we build it. All implementation follows this.
3. **TODO.md** — current work items and progress.
4. **docs/** — running documentation of decisions, changes, and learnings.

If code and docs disagree, the docs are wrong only if the change was intentional and documented. Otherwise, the code is wrong.

## Working Process

### Branching
- `main` is always clean. No direct commits to main.
- Feature branches: `feat/<short-name>` (e.g., `feat/fact-model`, `feat/ingest-pipeline`)
- Fix branches: `fix/<short-name>`
- Merge via PR. Squash merge preferred.

### Before Every Commit
- All tests must pass: `pytest`
- Type checks must pass: `pyright` (or `mypy` — TBD based on setup)
- No secrets in committed files (.env, API keys, credentials)

### Commits
- Short, imperative commit messages: "Add fact model", "Fix contradiction threshold"
- One logical change per commit

### Documentation
- Every significant decision gets a dated entry in `docs/decisions/`
- Format: `YYYY-MM-DD-<short-name>.md`
- Update TODO.md as tasks complete

## Code Conventions

### Python
- **Python 3.12+**
- **Async-first**: all I/O-bound operations are async
- **No ORM**: raw SQL via `asyncpg` (D-ARCH-2)
- **Protocol-based abstractions**: use `typing.Protocol` for interfaces, not ABC
- **Dataclasses for domain types**: `@dataclass` for data, not Pydantic (keep dependencies minimal)
- **Type hints everywhere**: all function signatures fully typed
- **f-strings** for string formatting
- Imports: stdlib → third-party → local, separated by blank lines

### Architecture Rules
- **Mycelium Boundary Rule** (spec 5.4): ALL agent interaction with the knowledge graph goes through Mycelium's Python API. Never Supabase directly. No exceptions.
- **Operational logging is separate** (spec 5.5): memory facts and ops logs live in different schemas. Mycelium does not use itself for observability.
- **Layer dependencies flow downward only**: Client SDK → Pipelines → Domain Logic → Storage. Never upward.
- **Facts are immutable** (D-ARCH-6): corrections create new facts that supersede old ones. Never mutate fact content in place.

### Testing
- Unit tests: `tests/unit/` — no database, no network, fast
- Integration tests: `tests/integration/` — requires Postgres with pgvector
- Scenario tests: `tests/scenarios/` — YAML-driven declarative tests (spec D6)
- Test database: local Postgres via Homebrew (Docker Compose for CI later)

## Tech Stack

- **Language**: Python 3.12+
- **Database**: PostgreSQL 16 + pgvector (Homebrew local, Supabase production)
- **Async DB**: asyncpg
- **Embeddings**: text-embedding-3-small via OpenAI API (pluggable)
- **LLM**: Claude API (optional, not needed for core operations)
- **Testing**: pytest + pytest-asyncio
- **Type checking**: pyright
- **Formatting**: ruff

## Local Development Setup

### Prerequisites
```bash
brew install postgresql@16 pgvector
brew services start postgresql@16
# Ensure psql is in PATH:
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
```

### Database Setup
```bash
createdb mycelium_dev
createdb mycelium_test
psql mycelium_dev -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql mycelium_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Python Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests
```bash
pytest                          # all tests
pytest tests/unit/              # unit only (fast, no DB)
pytest tests/integration/       # integration (requires Postgres)
```

## What Not To Do

- Do not query Supabase/Postgres directly from agent code — go through MyceliumClient
- Do not add dependencies without discussion — keep the dependency tree minimal
- Do not add LLM calls to core operations (ingest, query, propagate) — these must work without LLM
- Do not mutate fact content after creation — create a new superseding fact instead
- Do not skip tests before committing
- Do not add Pydantic, SQLAlchemy, or other heavy frameworks without explicit decision
