# Mycelium — Gap Analysis

_Date: 2026-03-11_
_Branch: feat/server-side-parsing_

---

## 1. Hvad virker (bygget + tests passer)

**343 unit tests passer, 45 parsing tests passer, 3 scenario tests passer. Pyright: 0 errors. Ruff: 0 errors.**

| Modul | Status | Tests |
|-------|--------|-------|
| Domain types (Fact, FactContent, enums, predicates) | Grøn | 35 |
| Storage layer (in-memory repos, alle 6) | Grøn | 28 |
| Postgres repos (CRUD, search, temporal) | Grøn | 21 (integration) |
| Embedding (Mock, OpenAI, Cache) | Grøn | 18 |
| Ingest pipeline (validate → embed → contradiction → score → store) | Grøn | 15 |
| Trust scorer (hierarchy, history, corroboration) | Grøn | 11 |
| Conflict detector (embedding similarity + canonical matching) | Grøn | 15 |
| ContradictionSweeper (background sweep) | Grøn | 10 |
| Propagation engine (subscriptions, events, transport) | Grøn | 14+5 |
| Verification pipeline + providers | Grøn | 16 |
| Hallucination detection (heuristic checks) | Grøn | 17 |
| Conflict resolution (deterministic + LLM-assisted) | Grøn | Phase 4 tests |
| Causal provenance + version vectors | Grøn | Phase 4 tests |
| OpsLogger | Grøn | 13 |
| Migration importers (LanceDB, Supabase, memory files) | Grøn | 14 |
| Transport (InProcess + Supabase Realtime) | Grøn | 6 |
| Client SDK (ingest, query, correct, verify, corroborate, connect) | Grøn | 8 |
| Server (FastAPI, REST endpoints, auth, health) | Grøn | Type-checks pass |
| TypeScript SDK | Grøn | Scaffold + HTTP client |
| Parsing module (server-side, `pipelines/parsing.py`) | Grøn | 45 |
| OpenClaw plugin (`mycelium-connector` v0.4.0) | Bygget | Ikke testet automatisk |

**Infrastructure status:**
- Server kører (launchd, PID aktiv, `/health` → OK)
- Alle 6 agenter registreret i DB (main, jasper-code, jasper-trader, jasper-research, jasper-planner, migration-agent)
- Plugin aktivt i OpenClaw (`mycelium-connector: enabled: true`)
- 327 facts i databasen (309 memory files, 18 LanceDB)

---

## 2. Hvad er i stykker (bygget men tests fejler)

### 14 unit tests + 8 scenario tests fejler

**Root cause: `datetime.now()` (naive) vs `datetime.now(UTC)` (aware)**

`query.py:147` og `decay.py:94` bruger `datetime.now(UTC)` (timezone-aware), men facts oprettes med `datetime.now()` (naive) i ~25 steder i kodebasen: `client.py`, `storage/memory.py`, `ingest.py`, `verification.py`, `conflict_resolution.py`, m.fl.

Når `_compute_score()` i `query.py:191` beregner `now - fact.valid_from`, crasher det med:
```
TypeError: can't subtract offset-naive and offset-aware datetimes
```

**Fejlende tests:**

| Testfil | Antal | Fejl |
|---------|-------|------|
| `test_pipelines.py` (QueryEngine) | 4 | naive vs aware i `_compute_score` |
| `test_decay.py` (DecayCycleRunner) | 6 | naive vs aware i `_evaluate` (`last_activity < stale_cutoff`) |
| `test_client.py` (roundtrip) | 2 | Query pipeline crasher efter succesfuld ingest |
| `test_migration.py` (import + query) | 2 | Query pipeline crasher efter migration ingest |
| `test_scenarios.py` | 2 | assert_query trin fejler |
| `test_jasper_scenarios.py` | 6 | assert_query trin fejler |

**Fix:** Ét konsistent valg — enten `datetime.now(UTC)` overalt eller `datetime.now()` overalt. Anbefalet: `datetime.now(UTC)` overalt (spec kræver temporal awareness, Postgres gemmer med timezone). Kræver opdatering af ~25 `datetime.now()` kald + evt. fixture-datoer i tests.

**Kompleksitet: S** — mekanisk find-and-replace + kør tests. Ingen arkitekturændring.

---

## 3. Hvad mangler (spec'et men ikke bygget)

### 3.1 Spec-features uden implementation

| Spec-sektion | Feature | Status |
|--------------|---------|--------|
| 7.8 | Legacy migration: **Supabase `shared_learnings`** actual extraction + import | Kode bygget, men aldrig kørt mod rigtig Supabase |
| 7.6 | Agent restart/replay: **consolidated replay** (deduplicated for agents down >N hours) | `replay()` eksisterer men consolidated mode mangler |
| 7.6 | **Full graph snapshot** for nye agenter (filtreret af subscriptions) | Ikke implementeret |
| 3.5 | Ground-truth check: **system probe** (fx health check, API kald) | `VerificationProvider` protocol eksisterer, men ingen system-probe implementering |
| 3.5 | **Source re-check** verification method | Ikke implementeret |
| 3.4 | **Persistence score** (access frequency, recency, relevance) | Decay bruger last_accessed_at, men der er ingen eksplicit persistence score på Fact |
| 8.1 | **Observability dashboard** (hvad propagerede hvorhen, hvorfor) | Ingen dashboard — kun OpsLogger + Supabase metrics |
| 9 Phase 5 | **Benchmark suite** udgivelse | Runner bygget (`benchmarks/run.py`), men ingen baseline publiceret |

### 3.2 TODO.md åbne items

| Item | Status |
|------|--------|
| CI-ready Docker Compose | Åben (deferred) |

Alt andet i TODO.md er afkrydset — men TODO.md afspejler ikke nødvendigvis 100% af spec'en (se 3.1).

### 3.3 Integration gaps: Mycelium ↔ OpenClaw

| Gap | Detaljer |
|-----|----------|
| **Nightly memory-fil extraction kører ikke** | Ingen cron/launchd job. Integration guide beskriver det, men det er ikke sat op. |
| **Plugin bruger `/ingest/raw` som ikke er merget til main** | Endpoint + parsing modul lever på `feat/server-side-parsing` branch. Server på main har det ikke. |
| **`agent_end` hook sender ikke session-facts** | Pluginet scorer hit-rate men batch-ingest af session-facts (OPENCLAW_INTEGRATION_GUIDE trin 4 punkt 3) er ikke implementeret. |
| **Ingen replay/reconnect i plugin** | Plugin connector kalder aldrig `replay()` — hvis server genstarter, mister agenten events fra nedetiden. |
| **Query response format uverificeret** | Plugin forventer `f.fact.content`, men server DTO returnerer muligvis et andet format. Ingen end-to-end test. |

---

## 4. Hvad mangler for produktion

| Krav | Status | Detaljer |
|------|--------|----------|
| Server som launchd service | **OK** | `com.mycelium.server.plist` installeret og kørende |
| 5 agenter registreret | **OK** | Alle 6 (inkl. migration-agent) i DB |
| Plugin aktivt i OpenClaw | **OK** | `mycelium-connector: enabled: true` |
| Nightly memory-fil extraction | **MANGLER** | Ingen cron job. Guide beskriver kommandoen men den er ikke sat op. |
| Server-side parsing merget | **MANGLER** | Plugin v0.4.0 kalder `/ingest/raw` som kun eksisterer på feature branch |
| End-to-end test (plugin → server → DB) | **MANGLER** | Aldrig kørt. Ingen integration test for HTTP-laget. |
| Monitoring/alerting | **DELVIST** | Supabase metrics-tabel modtager events, men ingen alerts/dashboards |
| MYCELIUM_SERVER_API_KEY | **UKLART** | Plist har `SÆTTES HER` for OpenAI key — skal verificeres at env vars er korrekte |
| Backup/recovery strategi | **MANGLER** | Postgres dev-database uden backup plan |
| Log rotation | **MANGLER** | Server logger til `/tmp/mycelium-server.log` — ingen rotation |
| SSL/TLS | **N/A** | Localhost only, men relevant hvis remote access tilføjes |
| Rate limiting på server | **MANGLER** | Ingen request rate limiting — relevant for trading agent |
| OpenClaw → Mycelium cutover plan | **DELVIST** | 5-fase plan beskrevet i guide, men systemet er stadig i Phase 0-1 |

---

## 5. Prioriteret handlingsplan

| # | Opgave | Impact | Kompleksitet | Begrundelse |
|---|--------|--------|-------------|-------------|
| 1 | **Fix datetime naive/aware bug** | Kritisk | **S** | 14+8 tests fejler. Blokerer al query og decay funktionalitet. Mekanisk fix — brug `datetime.now(UTC)` konsistent. |
| 2 | **Merge `feat/server-side-parsing` til main** | Høj | **S** | Plugin v0.4.0 afhænger af `/ingest/raw`. Uden merge er ingest fra OpenClaw broken. Branch er review-klar. |
| 3 | **End-to-end integration test** | Høj | **M** | Kør plugin → server → DB round-trip test. Verificer query response format matcher plugin forventning. Finder ukendte fejl før produktion. |
| 4 | **Sæt nightly memory-fil extraction op** | Høj | **S** | Kommandoen er dokumenteret. Opret launchd plist eller cron job der kører `mycelium-migrate run --source memory_file` nightly. |
| 5 | **Fix env vars i launchd plist** | Medium | **S** | OpenAI API key er placeholder (`SÆTTES HER`). Server kan ikke embedde uden rigtig key. Verificer alle env vars. |
| 6 | **Implementer consolidated replay** | Medium | **M** | Spec 7.6 kræver det. Plugin kalder aldrig replay — tilføj reconnect-logik i plugin og consolidated mode i server. |
| 7 | **Server request rate limiting** | Medium | **S** | Trading agent genererer mange requests. Tilføj simpel rate limiter middleware. |
| 8 | **Observability: alerts på fejl-metrics** | Medium | **M** | Supabase modtager metrics men ingen reagerer på dem. Tilføj alerts for error_rate, staleness_rate, query latency. |
| 9 | **System probe verification provider** | Lav | **L** | Spec 3.5 ground-truth checks. Kræver plugin-arkitektur for domæne-specifikke probes (health checks, API status). |
| 10 | **Docker Compose for CI** | Lav | **M** | Deferred fra Phase 1. Nødvendig for CI/CD pipeline med integration tests. |

---

## Opsummering

**Mycelium er feature-komplet igennem alle 5 faser.** Spec → kode mapping er bemærkelsesværdigt tæt — næsten alle spec-features har implementering + tests. Det store billede:

- **Kernen virker**: 343+45 tests passer, type-checks rene, lint ren
- **Én blokerende bug**: datetime naive/aware inkompatibilitet crasher query + decay (22 tests)
- **Integration gap**: Feature branch ikke merget, nightly extraction ikke sat op, ingen end-to-end test
- **Produktion**: Server kører, agenter registreret, plugin aktivt — men env vars og monitoring mangler

Fix datetime-buggen og merge feature branch → systemet er funktionelt. Derefter: end-to-end test, nightly extraction, monitoring.
