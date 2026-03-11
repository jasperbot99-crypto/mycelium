# Mycelium ↔ OpenClaw Integration Guide

_For den session der bygger `mycelium-connector` pluginet i OpenClaw._

---

## Mycelium Projekt

**Fuld sti:** `<project-root>` (e.g. `~/Projects/mycelium`)

Læs disse filer for detaljer:
- `SPEC.md` — hvad og hvorfor
- `ARCHITECTURE.md` — hvordan
- `src/mycelium/client/client.py` — Python SDK (MyceliumClient)
- `src/mycelium/server/app.py` — HTTP server (FastAPI)
- `src/mycelium/server/cli.py` — Server entry point
- `src/mycelium/config.py` — MyceliumConfig
- `src/mycelium/domain/types.py` — Fact, SourceType, FactContent m.fl.

See `MYCELIUM_MIGRATION.md` in the main agent workspace for the migration plan.

---

## Nuværende Status

- **Database:** Postgres 16 + pgvector kører. `mycelium_dev` har 327 facts (309 fra memory files, 18 fra LanceDB), 1 agent (`migration-agent`).
- **Server:** IKKE startet endnu. Skal startes.
- **Schema:** Applied. Tabeller: `agents`, `facts`, `conflicts`, `fact_relations`, `propagation_events`, `subscriptions`, `schema_migrations`.
- **Agenter registreret:** Kun `migration-agent`. De 5 rigtige agenter mangler.

---

## Trin 1: Start Mycelium Server

Mycelium server er en FastAPI-app der kører via uvicorn.

### Environment variables

```bash
export MYCELIUM_DATABASE_URL="postgresql://localhost:5432/mycelium_dev"
export OPENAI_API_KEY="<din rigtige OpenAI key>"
export MYCELIUM_SERVER_HOST="127.0.0.1"
export MYCELIUM_SERVER_PORT="8080"
# Valgfrit — API key til autentificering af requests:
export MYCELIUM_SERVER_API_KEY="<valgfri server-key>"
```

### Start serveren

```bash
cd ~/Projects/mycelium
source .venv/bin/activate
mycelium-server
```

Eller som launchd service (anbefalet for permanent drift):

```xml
<!-- ~/Library/LaunchAgents/com.mycelium.server.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mycelium.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/ABS/PATH/TO/mycelium/.venv/bin/mycelium-server</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MYCELIUM_DATABASE_URL</key>
        <string>postgresql://localhost:5432/mycelium_dev</string>
        <key>OPENAI_API_KEY</key>
        <string>SÆTTES HER</string>
        <key>MYCELIUM_SERVER_HOST</key>
        <string>127.0.0.1</string>
        <key>MYCELIUM_SERVER_PORT</key>
        <string>8080</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/mycelium-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/mycelium-server.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.mycelium.server.plist
```

### Verifikation

```bash
curl http://127.0.0.1:8080/health
# → {"status":"ok"}
curl http://127.0.0.1:8080/ready
# → {"status":"ready"}
```

---

## Trin 2: Registrer Agenterne

Agenter registreres automatisk ved første `connect`. Serveren eksponerer:

```
POST /v1/agents/connect
Body: { "agent_id": "jasper-code", "role": "code", "subscriptions": [...] }
```

### De 5 agenter og deres subscriptions

| Agent | Role | Subscriptions (topics) |
|-------|------|----------------------|
| `main` | coordinator | `*` (alt) |
| `jasper-code` | code | `api.*`, `infrastructure.*`, `dashboard.*`, `memory.*` |
| `jasper-trader` | trader | `trading.*`, `api.*`, `broker.*`, `risk.*` |
| `jasper-research` | research | `job.*`, `market.*`, `research.*` |
| `jasper-planner` | planner | `*` (alt — planner koordinerer) |

---

## Trin 3: Byg `mycelium-connector` Plugin

### Plugin-placering

```
~/.openclaw/extensions/mycelium-connector/
├── openclaw.plugin.json
└── index.ts
```

### Manifest (`openclaw.plugin.json`)

```json
{
  "id": "mycelium-connector",
  "name": "Mycelium Knowledge Graph Connector",
  "version": "0.1.0",
  "description": "Connects OpenClaw agents to Mycelium shared memory"
}
```

### Registrering i `~/.openclaw/openclaw.json`

Tilføj under `plugins`:
```json
{
  "allow": ["mycelium-connector", ...eksisterende...],
  "installs": {
    "mycelium-connector": {
      "source": "path",
      "installPath": "~/.openclaw/extensions/mycelium-connector"
    }
  },
  "entries": {
    "mycelium-connector": {
      "enabled": true
    }
  }
}
```

### Environment variables (sættes i OpenClaw's env)

```
MYCELIUM_API_URL=http://127.0.0.1:8080
MYCELIUM_API_KEY=<samme som MYCELIUM_SERVER_API_KEY>
```

---

## Trin 4: Plugin Implementation

### Arkitektur

Pluginet har 3 jobs:

1. **`before_prompt_build`** (priority 12) — Query Mycelium for relevante facts → inject i prompt
2. **`after_tool_call`** — Fang `memory_store` kald → ingest til Mycelium parallelt
3. **`agent_end`** — Ekstraher facts fra session → batch ingest

### HTTP API Reference

Alle endpoints bruger JSON. Hvis `MYCELIUM_SERVER_API_KEY` er sat, send `X-API-Key` header.

#### Connect agent
```
POST /v1/agents/connect
Body: {
  "agent_id": "jasper-code",
  "role": "code",
  "subscriptions": [
    { "topic": "api.*", "priority": "high", "min_confidence": 0.5 },
    { "topic": "infrastructure.*", "priority": "normal" }
  ]
}
Response: { "agent_id": "jasper-code", "role": "code", "connected": true }
```

#### Ingest fact
```
POST /v1/agents/{agent_id}/ingest
Body: {
  "content": {
    "subject": "API /v2/orders",
    "predicate": "moved_to",
    "object": "/v3/orders",
    "context": "production"
  },
  "source_type": "agent_extraction",
  "tags": ["api", "infrastructure"],
  "metadata": {}
}
Response: { "fact_id": "uuid", "status": "created" | "conflict_detected" | "rejected", ... }
```

#### Query facts
```
POST /v1/agents/{agent_id}/query
Body: {
  "question": "API orders endpoint status",
  "filters": {
    "min_confidence": 0.5,
    "tags": ["api"],
    "max_results": 8
  }
}
Response: [
  {
    "fact": { "id": "...", "content": {...}, "confidence": 0.85, ... },
    "relevance_score": 0.92,
    "source_agent_id": "jasper-code"
  },
  ...
]
```

#### Disconnect
```
POST /v1/agents/{agent_id}/disconnect
```

### Plugin Template (`index.ts`)

```typescript
const MYCELIUM_URL = process.env.MYCELIUM_API_URL ?? "http://127.0.0.1:8080";
const MYCELIUM_KEY = process.env.MYCELIUM_API_KEY ?? "";

// Agent role mapping
const AGENT_ROLES: Record<string, string> = {
  main: "coordinator",
  "jasper-code": "code",
  "jasper-trader": "trader",
  "jasper-research": "research",
  "jasper-planner": "planner",
};

// Subscription config per agent
const AGENT_SUBSCRIPTIONS: Record<string, Array<{ topic: string; priority: string }>> = {
  main: [{ topic: "*", priority: "normal" }],
  "jasper-code": [
    { topic: "api.*", priority: "high" },
    { topic: "infrastructure.*", priority: "high" },
    { topic: "dashboard.*", priority: "normal" },
    { topic: "memory.*", priority: "normal" },
  ],
  "jasper-trader": [
    { topic: "trading.*", priority: "critical" },
    { topic: "api.*", priority: "high" },
    { topic: "broker.*", priority: "critical" },
    { topic: "risk.*", priority: "critical" },
  ],
  "jasper-research": [
    { topic: "job.*", priority: "high" },
    { topic: "market.*", priority: "normal" },
    { topic: "research.*", priority: "normal" },
  ],
  "jasper-planner": [{ topic: "*", priority: "normal" }],
};

// Token budget for Mycelium injection
const MYCELIUM_TOKEN_BUDGET = {
  main: 1200,
  cron: 400,
  spawn: 600,
};

export default function register(api: any): void {
  if (!MYCELIUM_URL) {
    console.warn("[mycelium-connector] MYCELIUM_API_URL not set, disabling");
    return;
  }

  const connectedAgents = new Set<string>();

  // ──────────────────────────────────────────────
  // 1. INJECT: Query Mycelium → inject facts before prompt
  // ──────────────────────────────────────────────
  api.on(
    "before_prompt_build",
    async (event: any, ctx: any) => {
      const agentId = ctx.agentId ?? "main";

      // Connect agent if not already connected
      if (!connectedAgents.has(agentId)) {
        await connectAgent(agentId);
        connectedAgents.add(agentId);
      }

      // Build query from prompt context
      const queryText = extractQueryFromContext(event, ctx);
      if (!queryText) return;

      // Query Mycelium with timeout
      const facts = await queryMycelium(agentId, queryText, 8);
      if (!facts || facts.length === 0) return;

      // Format and budget-check
      const sessionType = ctx.cronName ? "cron" : ctx.taskName ? "spawn" : "main";
      const budget = MYCELIUM_TOKEN_BUDGET[sessionType] ?? 600;
      const formatted = formatFactsForPrompt(facts, budget);

      if (formatted) {
        return { appendSystemContext: formatted };
      }
    },
    { priority: 12 }
  );

  // ──────────────────────────────────────────────
  // 2. CAPTURE: Intercept memory_store → parallel ingest
  // ──────────────────────────────────────────────
  api.on(
    "after_tool_call",
    async (event: any, ctx: any) => {
      if (event.toolName !== "memory_store") return;
      if (!event.result) return;

      const agentId = ctx.agentId ?? "main";
      const parsed = parseMemoryStoreToFact(event.result, agentId);
      if (!parsed) return;

      // Fire-and-forget ingest
      ingestFact(agentId, parsed).catch((err) =>
        console.warn(`[mycelium-connector] ingest failed: ${err}`)
      );
    },
    { priority: 5 }
  );

  // ──────────────────────────────────────────────
  // 3. CLEANUP: Disconnect on session end
  // ──────────────────────────────────────────────
  api.on(
    "agent_end",
    async (_event: any, ctx: any) => {
      const agentId = ctx.agentId ?? "main";
      // Don't disconnect — agent stays registered for propagation
      // Just cleanup local state if needed
    },
    { priority: 30 }
  );
}

// ──────────────────────────────────────────────
// HTTP helpers
// ──────────────────────────────────────────────

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (MYCELIUM_KEY) h["X-API-Key"] = MYCELIUM_KEY;
  return h;
}

async function connectAgent(agentId: string): Promise<void> {
  const role = AGENT_ROLES[agentId] ?? "generic";
  const subs = AGENT_SUBSCRIPTIONS[agentId] ?? [{ topic: "*", priority: "normal" }];

  try {
    const res = await fetch(`${MYCELIUM_URL}/v1/agents/connect`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ agent_id: agentId, role, subscriptions: subs }),
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      console.warn(`[mycelium-connector] connect ${agentId} failed: ${res.status}`);
    }
  } catch (err) {
    console.warn(`[mycelium-connector] connect ${agentId} error: ${err}`);
  }
}

async function queryMycelium(
  agentId: string,
  question: string,
  limit: number
): Promise<any[] | null> {
  try {
    const res = await fetch(`${MYCELIUM_URL}/v1/agents/${agentId}/query`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        question,
        filters: { min_confidence: 0.4, max_results: limit },
      }),
      signal: AbortSignal.timeout(2000), // 2s timeout — skip if slow
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null; // Timeout or error — degrade gracefully
  }
}

async function ingestFact(agentId: string, fact: any): Promise<void> {
  const res = await fetch(`${MYCELIUM_URL}/v1/agents/${agentId}/ingest`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(fact),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text}`);
  }
}

// ──────────────────────────────────────────────
// Parsing helpers
// ──────────────────────────────────────────────

function extractQueryFromContext(event: any, ctx: any): string | null {
  // Extract key terms from the prompt for Mycelium query
  const parts: string[] = [];

  // Use task name if spawned
  if (ctx.taskName) parts.push(ctx.taskName);

  // Extract from prompt text
  const prompt = typeof event.prompt === "string" ? event.prompt : "";
  if (prompt.length > 0) {
    // Take first 200 chars as query seed
    parts.push(prompt.slice(0, 200));
  }

  return parts.length > 0 ? parts.join(" ") : null;
}

function parseMemoryStoreToFact(
  result: any,
  agentId: string
): Record<string, any> | null {
  // memory_store format: [agent-id] [type] content
  const text = typeof result === "string" ? result : JSON.stringify(result);
  if (!text || text.length < 10) return null;

  // Parse the [agent] [type] content pattern
  const match = text.match(/\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.*)/s);
  if (match) {
    const [, _source, type, content] = match;
    return {
      content: {
        subject: extractSubject(content),
        predicate: type.toLowerCase().replace(/\s+/g, "_"),
        object: content.trim(),
      },
      source_type: "agent_extraction",
      tags: [agentId, type.toLowerCase()],
    };
  }

  // Fallback: treat as unstructured
  return {
    content: {
      subject: "unknown",
      predicate: "has_memory",
      object: text.slice(0, 500),
    },
    source_type: "agent_inference",
    tags: [agentId],
  };
}

function extractSubject(content: string): string {
  // Simple heuristic: first capitalized phrase or quoted string
  const quoted = content.match(/"([^"]+)"/);
  if (quoted) return quoted[1];

  const firstPhrase = content.match(/^([A-Z][a-zA-Z0-9\s/\-.]+)/);
  if (firstPhrase) return firstPhrase[1].trim();

  return content.slice(0, 50).trim();
}

function formatFactsForPrompt(facts: any[], tokenBudget: number): string | null {
  if (!facts || facts.length === 0) return null;

  let output = "## Mycelium Knowledge Graph\n";
  output += "_Relevante facts fra det delte memory-system:_\n\n";

  let tokens = estimateTokens(output);

  for (const f of facts) {
    const fact = f.fact ?? f;
    const content = fact.content ?? {};
    const line =
      `- **${content.subject ?? "?"}** ${content.predicate ?? "?"} → ${content.object ?? "?"}` +
      (content.context ? ` _(${content.context})_` : "") +
      ` [confidence: ${(fact.confidence ?? 0).toFixed(2)}]` +
      "\n";

    const lineTokens = estimateTokens(line);
    if (tokens + lineTokens > tokenBudget) break;

    output += line;
    tokens += lineTokens;
  }

  return output;
}

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}
```

---

## Trin 5: Daglige Memory-filer → Mycelium (Cron/Batch)

De daglige noter (`memory/YYYY-MM-DD.md`) er for store til real-time ingest. Anbefalet approach:

### Option A: Cron-baseret extraction (anbefalet)

Kør en nightly extraction der trækker facts ud af dagens noter:

```bash
# Kør fx kl 23:00 via launchd/cron
cd ~/Projects/mycelium
source .venv/bin/activate

mycelium-migrate run \
  --database-url "$MYCELIUM_DATABASE_URL" \
  --source memory_file \
  --memory-file ~/Projects/jasper-planner-workspace/memory/$(date +%Y-%m-%d).md \
  --memory-file ~/Projects/jasper-code-workspace/memory/$(date +%Y-%m-%d).md \
  --memory-file ~/Projects/jasper-trader-workspace/memory/$(date +%Y-%m-%d).md \
  --openai-api-key "$OPENAI_API_KEY" \
  --stop-on-error
```

### Option B: Plugin-baseret (i `agent_end` hook)

Lad hvert plugin-kald afslutte med at sende sessionsnotater til Mycelium. Mere real-time men mere støjende.

---

## Vigtige Design-beslutninger

### Hvad Mycelium erstatter
- `preconscious-buffer.md` → Mycelium query ved session start
- `team-knowledge.md` → Cross-agent propagation
- LanceDB for delte facts → Mycelium fact store

### Hvad der beholdes
- `MEMORY.md` per agent — agent-identitet, injected uanset hvad
- `SOUL.md` / `IDENTITY.md` — personlighed, ikke facts
- Daglige noter — audit trail, beholdes som filer
- `procedural-memory` plugin — procedurer er action-patterns, ikke facts
- LanceDB for private facts (operator-personalia)

### Graceful degradation
- **2 sekunder timeout** på alle Mycelium queries
- Hvis server er nede → agent kører uden Mycelium-context (ingen fejl)
- Ingest er fire-and-forget (fejl logges, blokerer ikke agent)

### Trading-agent rate limiting
- `jasper-trader` genererer mange decisions — ingest kun ændringer, ikke hvert scan
- Max 1 ingest per fact-type per 5 min for trading-relaterede facts
- Market data facts ingest-es IKKE (for noisy)

---

## Source Type Mapping

| Kilde | SourceType | Initial Confidence |
|-------|------------|-------------------|
| Agent skriver en observation | `agent_extraction` | 0.5 |
| Agent drager en konklusion | `agent_inference` | 0.4 |
| Verificeret mod system (broker API, health check) | `system_verification` | 0.85 |
| Operator corrects something | `human_correction` | 1.0 |
| Migreret data | `agent_extraction` | 0.7 |

---

## Trust Hierarchy (enforced, ikke suggested)

```
human_correction (1.0) > system_verification (0.85) > agent_extraction (0.6) > agent_inference (0.4)
```

Agenter beslutter aldrig autonomt hvem der "vinder" en contradiction — det er operatørens privilegium for ambiguøse sager.

---

## Verifikation af at det virker

```bash
# 1. Server kører?
curl http://127.0.0.1:8080/health

# 2. Agenter registreret?
# (check via database)
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
psql mycelium_dev -c "SELECT id, role FROM mycelium.agents;"

# 3. Facts i systemet?
psql mycelium_dev -c "SELECT count(*) FROM mycelium.facts;"

# 4. Test ingest via API
curl -X POST http://127.0.0.1:8080/v1/agents/jasper-code/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "content": {
      "subject": "test",
      "predicate": "is",
      "object": "working"
    },
    "source_type": "agent_extraction",
    "tags": ["test"]
  }'

# 5. Test query via API
curl -X POST http://127.0.0.1:8080/v1/agents/jasper-code/query \
  -H "Content-Type: application/json" \
  -d '{"question": "test", "filters": {"max_results": 5}}'
```

---

## Faser (fra Jaspers plan)

1. **Phase 0 (nu):** Start server + byg plugin → verify health
2. **Phase 1 (shadow):** Plugin skriver til Mycelium, agenter læser stadig fra gammelt system
3. **Phase 2 (parallel):** Inject Mycelium-facts i prompt + behold gammel injection
4. **Phase 3 (primær):** Mycelium er kilde, gammelt system er fallback
5. **Phase 4 (cutover):** Slet preconscious-buffer, team-knowledge. Mycelium er eneste kilde.

Start med Phase 0+1. Ingen risiko. Ingen agent ændrer adfærd.
