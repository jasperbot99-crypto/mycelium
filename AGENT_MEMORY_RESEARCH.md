# Agent Memory & Coordination — Research Document
_Startet: 2026-03-10, Tobias + Jasper_
_Status: Research phase_

---

## Målet

Byg verdens bedste memory system til AI-agenter — specifikt til multi-agent setups som OpenClaw — hvor agenter:
1. **Husker på tværs af sessioner** uden cold start
2. **Lærer af hinanden** — ikke kun af sig selv
3. **Koordinerer reelt** — ikke bare via shared files og Supabase-polling
4. **Forbedrer sig kontinuerligt** — systemet bliver measurably bedre over tid
5. **Kræver minimal menneskelig intervention** til at holde sig aligned

Startpunkt: Jasper-systemet. Slutmål: generelt framework der kan bruges af andre OpenClaw-setups og potentielt open-sourced.

---

## Hvad vi ved ikke virker (fra FRUSTRATION_AUDIT.md)

Disse er ikke teoretiske problemer — de er observerede failures i vores eget system:

- **Session amnesi**: Agenter starter forfra hver gang. Memory-filer hjælper, men der er altid lag og huller.
- **Siloed learning**: Jasper-code lærer noget → jasper-trader ved det ikke. Tobias korrigerer main → jasper-planner ved det ikke.
- **Stale state**: Research rapporterer ting som broken der er fixet. Ingen agent tjekker mod faktiske system-state.
- **Hallucineret fakta**: COMM2IG "Ansøgt 9/3" — agent rapporterede noget Tobias aldrig havde gjort som om det var sandt.
- **Soft guidance virker ikke**: Regler i filer overholdes ikke systematisk. Kun hard enforcement (plugins/hooks) virker.
- **Intra-session amnesi**: Tobias siger X → agent reverterer til gammel model 5 beskeder senere.
- **Ingen måling**: Vi ved ikke om memory-systemet faktisk hjælper. Ingen metrics.

---

## Hvad vi allerede har bygget

- **shared_cognition plugin**: Cross-agent knowledge via Supabase `shared_learnings` tabel. Live dag 1.
- **procedural-memory v2**: Pattern capture, crystallization, proaktiv injection ved kendte fejl.
- **self-evolution plugin**: Gap detection, auto-skill placeholders, goal-alignment check.
- **rumination engine**: Nattlig destillering af raw captures til structured facts.
- **LanceDB**: Semantisk memory via `memory_recall`/`memory_store`.
- **Preconscious buffer**: Pre-session context injection.

Disse er udgangspunktet, ikke scratch.

---

## Åbne forskningsspørgsmål

1. **Hvad eksisterer?** MemGPT/Letta, Zep, mem0, Cognee, A-MEM — hvad løser de, hvad løser de ikke?
2. **Hvad er state of the art for cross-agent propagation?** Ikke bare shared storage — aktiv knowledge push.
3. **Hvordan måles memory-kvalitet?** Hit-rate, stale-state incidents, alignment-score — hvad er best practice?
4. **Hvad er den reelle bottleneck?** Storage? Retrieval? Injection? Capture? Crystallization?
5. **Kan implicit feedback (frustration, korrektioner) struktureres pålideligt?** Uden hallucination.
6. **Hvad gør at agenter faktisk lærer** — ikke bare gemmer ting?

---

## Research tasks (jasper-research)

- [x] Survey: MemGPT/Letta architecture — hvad er nyskabende, hvad er svagt?
- [x] Survey: Zep, mem0, Cognee — same
- [x] Survey: akademisk litteratur om multi-agent memory/coordination (2024-2026)
- [x] Identificér: hvad er det ingen har løst endnu?
- [x] Sammenfat: konkrete arkitektur-mønstre der virker
- [ ] Arkitektur-design baseret på findings

---

## Repo

Navn: TBD
Location: `~/Projects/` (privat, kan open-sources senere)
Stack: TBD efter research

---

## Research Findings

_Surveyed 2026-03-10 by jasper-research. Fokus: arkitektur + uløste problemer._

---

### 1. MemGPT / Letta

**Arkitektur:**
- **OS-inspireret to-tier memory**: Fast context window (RAM-analog) med main context + external storage (disk-analog). Agenten "pager" information ind/ud via self-managed function calls. Core memory blocks (~5K chars) er altid synlige; conversation buffer er FIFO med rekursiv summarization ved overflow.
- **Tre memory-komponenter**: (1) Core memory — writable in-context blocks med persona/user facts, (2) Recall memory — komplet ucomprimeret samtalehistorik, søgbar via keyword, (3) Archival memory — persistent knowledge base med embedding-baseret vektor-retrieval.
- **Seks self-managed memory-funktioner**: Agenten kalder selv `core_memory_append/replace`, `archival_memory_insert/search`, `conversation_search`, `send_message`. LLM'en beslutter hvornår og hvad der gemmes — ingen developer-defined RAG pipeline.
- **Letta platform (2025-2026)**: Tilføjer sleep-time agents (asynkron memory consolidation mellem sessioner), Conversations API (shared state på tværs af sessioner for *samme* agent), Context Repositories (git-inspireret versioneret kontekst).
- **Schema-løs flat storage**: Archival memory er flat text passages med embedding-indeks. Ingen graf-struktur, relationer, temporale links, eller schema.

**Uløste problemer:**
- **Cross-agent propagation: INGEN.** Memory er isoleret per agent-instans. Agent A's viden når aldrig Agent B. Conversations API dækker kun samme agent på tværs af sessioner — ikke multi-agent knowledge sharing.
- **Implicit feedback: INGEN.** Alle memory-opdateringer kræver at agenten eksplicit kalder memory-funktioner. Systemet lærer ikke fra brugeradfærd (genfrasering = frustration, ignoreret forslag = irrelevans). Ingen RL-signal, ingen behavioral inference.
- **Intra-session amnesi: DELVIST.** Information evicted fra context via FIFO + lossy summarization kan funktionelt glemmes. Agenten skal *vide at den skal søge* — men hvis summarization tabte detaljen, eller agenten ikke genkender behovet, er den effektivt glemt. Ingen salience-weights, ingen prioriteret eviction.
- **Hallucination prevention: INGEN.** Archival retrieval via embeddings har semantic drift (matcher topic men fejler på identifiers), context dilution (underweighter vigtige fragments), og ingen provenance/confidence scoring. Hallucinated "memories" behandles som ground truth.
- **Flat memory-organisation** gør multi-hop reasoning umuligt uden re-retrieval af raw text. Ingen relational links mellem facts — systemer som A-MEM adresserer dette med Zettelkasten-inspirerede knowledge networks.

---

### 2. Zep

**Arkitektur:**
- **Graphiti temporal knowledge graph**: Tre-tier subgraf — (1) Episode Subgraph (rå samtaler, non-lossy), (2) Semantic Entity Subgraph (extraherede entiteter + relationer), (3) Community Subgraph (klynger af stærkt forbundne entiteter med opsummeringer). Entiteter linkes bidirektionelt til kilde-episoder for provenance.
- **Bi-temporal datamodel**: Hver edge har fire timestamps — `t'_created/t'_expired` (transactional: hvornår Zep lærte/invaliderede fakta) og `t_valid/t_invalid` (event-baseret: hvornår fakta blev/ophørte med at være sand). Muliggør temporale queries og automatisk edge-invalidation ved modstridende information.
- **Hybrid retrieval**: Tre søgefunktioner — cosine semantic similarity, BM25 keyword matching, breadth-first graph traversal — med reranking via Reciprocal Rank Fusion / Maximal Marginal Relevance / cross-encoder. Overgår pure vector RAG på temporal og kausal reasoning.
- **User-centrisk memory**: Organiseret per user med session-scoped API. `memory.get()` returnerer syntetiseret context string fra knowledge graph (long-term) + sidste 4-6 rå beskeder (short-term). Ingestion er asynkron og kan tage minutter.
- **Performance**: 18.5% accuracy improvement over baselines, 90% latency reduktion, <2% af baseline tokens. Bruger prædefinerede Cypher queries (ikke LLM-genererede) for at reducere extraction hallucinations.

**Uløste problemer:**
- **Cross-agent propagation: INGEN.** Arkitekturen er fundamentalt user-centrisk og single-agent scoped. Ingen mekanisme for at dele viden mellem agenter. Graphiti er designet til single-agent knowledge graph construction — ingen distributed collective memory, ingen inter-agent kommunikationsprotokoller.
- **Implicit feedback: INGEN.** Knowledge graph populeres udelukkende via eksplicit LLM-baseret entity/relation extraction fra samtaler. Ingen læring fra implicit brugeradfærd (klik, dwell time, task abandonment, korrektionsfrekvens, preference drift). Fanger hvad brugere *siger* men ikke hvad de *gør*.
- **Intra-session amnesi: DOKUMENTERET SVAGHED.** 17.7% performance drop på single-session spørgsmål (gpt-4o). Root cause: asynkron ingestion skaber blind spot — mid-session facts der er scrollet forbi message buffer men endnu ikke ingesteret i grafen. Zep's paper anerkender dette kræver "further research."
- **Hallucination prevention: BEGRÆNSET.** Prædefinerede Cypher queries + Reflexion-inspireret reflection under extraction, men ingen hallucination-detektion ved retrieval-time. HaluMem benchmark viser memory-systemer (inkl. Zep) "tend to generate and accumulate hallucinations during extraction." Ingen confidence scoring på retrieved facts, ingen contradiction-detection ved query time.
- **Evaluerings-gaps**: DMR benchmark tester kun single-turn fact-retrieval, ikke complex reasoning. Zep's evne til at syntetisere samtalehistorik med structured business data er aldrig evalueret. Avancerede features (classification, extended session mgmt) er cloud-only.

---

### 3. mem0

**Arkitektur:**
- **Extraction-then-Update pipeline**: To-faset LLM-in-the-loop process. Extraction-fasen destillerer candidate memory facts fra beskeder + samtale-summary. Update-fasen retriever top-K semantisk lignende eksisterende memories, derefter vælger LLM'en: `ADD`, `UPDATE`, `DELETE`, eller `NOOP`. Hver operation kræver LLM-invocation.
- **Triple-write storage**: Hver memory skrives til tre stores — (1) vector store (22+ providers: Qdrant, Pinecone, ChromaDB, PGVector, FAISS...) med dense embeddings, (2) optionel graph store (Neo4j, Memgraph, Kuzu...) med `(source, relation, target)` labeled edges, (3) SQLite history log som audit trail. Graph memory er disabled by default.
- **Hybrid retrieval**: Vector similarity search (top-K) + optionel graph traversal (entity-centrisk eller semantic triplet matching). Dual path giver "hvad" (semantic content) + "hvem/hvor" (structural relations). Optionel reranking, men ikke automatisk.
- **Scoped memory-hierarki**: Partitioneret per `user_id`, optionelt `agent_id` og `run_id`. Tre logiske tiers: user memory (persistent), session memory (kort-livet), agent memory (per agent-instans). Strikt ID-baseret isolation.
- **External-to-context storage**: Memories lever uden for LLM context window. "Auto-Recall" injicerer kun relevante memories per turn. Graph-varianten bruger ~14K tokens/samtale vs ~7K for dense-only — men graph giver kun ~2% marginal improvement og kan degradere single-hop performance.

**Uløste problemer:**
- **Cross-agent propagation: INGEN.** `user_id`/`agent_id` partitionering er en hard arkitektural grænse. Ingen mekanisme for at Agent A deler learned memories med Agent B. Ingen federated memory bus, ingen global fact integration layer, ingen access-rights eller inheritance-protokoller. Forskning (arxiv 2603.04740): *"No current system has designed a cross-instance memory inheritance protocol."*
- **Implicit feedback: INGEN.** Ren explicit-fact extraction fra samtale-tekst. Lærer ikke fra bruger-adfærdssignaler (gentagne korrektioner, copy actions, session abandonment, query refinement patterns). HN-thread: *"Mem0 = memory storage + retrieval. Doesn't learn patterns."* Brugerkorrektioner behandles som enhver anden tekst, ikke som strukturerede preference-opdateringer.
- **Intra-session amnesi: DELVIST.** Memories overlever context compaction (external storage), men extraction afhænger af LLM'ens evne til at identificere salience. Lossy summary af ældre kontekst kan miste detaljer. Ingen feedback loop: hvis agenten afslører at den missede et tidligere stated fact, har mem0 ingen mekanisme til at detektere eller korrigere dette intra-session.
- **Hallucination prevention: INGEN.** Ingen safeguard mod at admittere hallucinated content i memory store. LLM-drevet extraction → hvis LLM hallucinerer et "fact", embeddes og gemmes det. A-MAC paper: *"LLM-native approaches lack principled mechanisms for preventing hallucinated content from entering memory."* Ingen confidence scoring, ingen evidence-grounding, ingen verifikation mellem extraction og storage. Hallucinated memories propagerer til fremtidige retrievals.
- **LLM-afhængig consolidation**: Hver memory-operation kræver LLM-invocation → høj latency og cost (A-MAC viser 31% lavere latency med hybrid approach). Begrænset interpretability — svært at auditere hvorfor en specifik memory blev admitted/merged/rejected. Deduplication via LLM-judgment (ikke deterministisk) → memory bloat over tid.

---

### Cross-System Gap Analysis

| Problem | MemGPT/Letta | Zep | mem0 |
|---|---|---|---|
| Cross-agent propagation | Ingen | Ingen | Ingen |
| Implicit feedback learning | Ingen | Ingen | Ingen |
| Intra-session amnesi | Delvist (FIFO eviction) | Dokumenteret svaghed (17.7% drop) | Delvist (lossy extraction) |
| Hallucination prevention | Ingen | Begrænset (extraction-time only) | Ingen |
| Memory structure | Flat/schema-løs | Temporal knowledge graph | Flat vectors + optional graph |
| Multi-hop reasoning | Umuligt uden re-retrieval | Muligt via graph traversal | Begrænset |

**Konklusion**: Alle tre systemer fejler på de samme fire kerneproblemer. Cross-agent propagation og implicit feedback learning er uløste i hele feltet. Intra-session amnesi er kun delvist adresseret. Hallucination prevention er et åbent forskningsspørgsmål — HaluMem benchmark (2025) var den første evaluering overhovedet. Zep har den mest sofistikerede arkitektur (temporal KG), men deler de fundamentale gaps.

---

## Akademisk Litteratur Survey (2024-2026)

_Surveyed 2026-03-10. Fokus: multi-agent memory, koordinering, og uløste problemer._

---

### 4. Collaborative Memory (ICML 2025)

**Paper**: "Collaborative Memory: Multi-User Memory Sharing in LLM Agents with Dynamic Access Control" — Rezazadeh, Li et al. ([arXiv 2505.18279](https://arxiv.org/abs/2505.18279))

**Arkitektur:**
- To-tier memory: private + shared fragments med asymmetrisk, tidsevolverende access control
- Access-kontrol modelleret som **bipartite grafer** der linker users, agents, og resources
- Hver memory-fragment har **immutable provenance** (contributing agents, accessed resources, timestamps)
- Write policy allokerer fragments til private eller shared memory
- Read policy styrer hvad en agent kan eksponere ved query time
- Retrospektive permission checks på hver memory-operation for auditability
- Bruger Attribute-Based Access Control (ABAC)

**Relevans**: Mest sofistikerede access-control model for delt agent-memory publiceret til dato. Løser *hvem må se hvad* men ikke *hvem har brug for hvad*.

---

### 5. A-MEM (NeurIPS 2025)

**Paper**: "A-MEM: Agentic Memory for LLM Agents" ([arXiv 2502.12110](https://arxiv.org/abs/2502.12110)) | [GitHub](https://github.com/agiresearch/A-mem)

**Arkitektur:**
- **Zettelkasten-inspireret** dynamisk memory-organisation
- Nye memories genererer structured notes med kontekst, keywords, tags
- Systemet analyserer historiske memories og etablerer links ved semantisk lighed
- **Retroaktive opdateringer**: nye memories kan trigger opdateringer til eksisterende memories' kontekstuelle repræsentationer
- Memory-grafen self-refiner kontinuerligt

**Relevans**: Retroaktiv opdatering er en nøgle-mekanisme. Ikke inter-agent, men mekanismen kan generaliseres.

---

### 6. Nemori (Aug 2025)

**Paper**: "Nemori: Self-Organizing Agent Memory Inspired by Cognitive Science" ([arXiv 2508.03341](https://arxiv.org/abs/2508.03341)) | [GitHub](https://github.com/nemori-ai/nemori)

**Arkitektur:**
- **Two-Step Alignment** fra Event Segmentation Theory — segmenterer samtalestrømme til semantisk kohærente episoder
- **Predict-Calibrate Principle** fra Free Energy Principle — agenten laver prædiktioner om indkommende information. Prediction gaps driver knowledge integration
- Proaktiv læringsmekanisme i stedet for passiv storage

**Relevans**: Nærmeste eksisterende system til event-driven memory. Predict-Calibrate er intra-agent men princippet kan generaliseres til inter-agent propagation.

---

### 7. KARMA (NeurIPS 2025 Spotlight)

**Paper**: "KARMA: Leveraging Multi-Agent LLMs for Automated Knowledge Graph Enrichment" ([arXiv 2502.06472](https://arxiv.org/abs/2502.06472)) | [GitHub](https://github.com/YuxingLu613/KARMA)

**Arkitektur:**
- Ni kollaborative agenter til KG-berigelse
- **Conflict Resolution Agents** der resolver modsigelser via **LLM-baseret debate**
- Cross-agent verifikation: Relationship Extraction validerer mod Schema Alignment outputs
- 83.1% LLM-verified korrekthed, 18.6% reduktion i conflict edges

**Relevans**: Eneste publicerede system med struktureret multi-agent conflict resolution. Debate-mekanismen er direkte relevant.

---

### 8. MARK (May 2025)

**Paper**: "MARK: Memory Augmented Refinement of Knowledge" ([arXiv 2505.05177](https://arxiv.org/pdf/2505.05177))

**Arkitektur:**
- Society-of-mind med specialiserede memory agents koordineret via microservices
- Memory Builder Service extraherer Residual, User Question, og LLM Response Refined Memories
- **Trust Score (TS)**: Probabilistisk trust-evaluering for at mitigere forkert brugerinformation
- **Persistence Score (PS)**: Bestemmer hvor længe en memory skal overleve
- Combined scoring: `Score = w1*semantic_relevance + w2*recency + w3*frequency + w4*feedback + w5*trust`

**Relevans**: Eneste system med eksplicit trust + persistence scoring. Single-agent, men scoring-modellen kan generaliseres til cross-agent trust.

---

### 9. CodeCRDT (Oct 2025)

**Paper**: "CodeCRDT: Observation-Driven Coordination for Multi-Agent LLM Code Generation" ([arXiv 2510.18893](https://arxiv.org/pdf/2510.18893))

**Arkitektur:**
- Anvender **CRDTs (Conflict-free Replicated Data Types)** til multi-agent koordinering
- Opnår **strong eventual consistency** med deterministisk conflict resolution
- Konvergens under 200ms i 5-agent stress tests
- Zero data loss garanti

**Relevans**: Eneste system der anvender distributed systems consistency-primitiver til multi-agent koordinering. Kun demonstreret for kode, men principperne er generaliserbare til knowledge.

---

### 10. MIRIX (Jul 2025)

**Paper**: "MIRIX: Multi-Agent Memory System for LLM-Based Agents" ([arXiv 2507.07957](https://arxiv.org/abs/2507.07957)) | [GitHub](https://github.com/Mirix-AI/MIRIX)

**Arkitektur:**
- Seks memory-typer: Core, Episodic, Semantic, Procedural, Resource Memory, Knowledge Vault
- Seks Memory Managers + Meta Memory Manager for routing
- **Active Retrieval**: Agenten genererer topic før besvarelse; retrieval feeder ind i system prompt
- 85.4% SOTA på LOCOMO; 35% højere accuracy end RAG baseline

**Relevans**: Mest granulære memory-type-taksonomi. Meta Memory Manager-konceptet er relevant for routing i multi-agent setups.

---

### 11. Intrinsic Memory Agents (Aug 2025, rev. Jan 2026)

**Paper**: "Intrinsic Memory Agents: Heterogeneous Multi-Agent LLM Systems through Structured Contextual Memory" ([arXiv 2508.08997](https://arxiv.org/abs/2508.08997))

**Arkitektur:**
- Adresserer at generel summarization mister rolle-specifikke perspektiver i multi-agent settings
- Per-agent structured memory templates der evolverer intrinsisk med agentens outputs
- Bevarer specialiserede perspektiver mens task-relevant information fastholdes

**Relevans**: Direkte relevant — viser at multi-agent memory IKKE kan være one-size-fits-all. Agenter har brug for perspektiv-bevarende memory.

---

### 12. SAGE (2024-2025)

**Paper**: "SAGE: Self-evolving Agents with Reflective and Memory-augmented Abilities" ([arXiv 2409.00872](https://arxiv.org/abs/2409.00872))

**Arkitektur:**
- **Ebbinghaus forgetting curves** til memory decay simulation
- Dynamisk prioritering af high-value information + pruning af triviel data
- Memory scoring: `S_i = alpha * w_i + beta * f_i` (w_i = time-decay, f_i = access frequency)

**Relevans**: Principled tilgang til memory decay. Vigtigt for garbage collection i multi-agent kontekst.

---

### 13. Øvrige relevante systemer

| System | Fokus | Relevans |
|---|---|---|
| **Cognee** ([GitHub](https://github.com/topoteretes/cognee)) | KG memory engine med agent-scoped layers | Agent-scoped + domain-scoped layer-model |
| **MemOS** ([GitHub](https://github.com/MemTensor/MemOS)) | Memory Operating System med MemCube abstraction | Multi-Cube KB management for isolation/sharing |
| **MegaAgent** (ACL 2025, [arXiv 2408.09955](https://arxiv.org/abs/2408.09955)) | 590-agent system | Vector DB shared memory, admin agents rekrutterer sub-agents |
| **memU** ([GitHub](https://github.com/NevaMind-AI/memU)) | Proaktiv 24/7 agent memory | Tre-lag hierarki, dual-mode retrieval, 1/10 token cost |
| **MemoryOS** ([GitHub](https://github.com/BAI-LAB/MemoryOS)) | Personalized agent memory OS (EMNLP 2025) | Fire moduler: Storage, Updating, Retrieval, Generation |

---

### 14. Surveys & Meta-Resources

- **"Memory in the Age of AI Agents"** (Dec 2025, [arXiv 2512.13564](https://arxiv.org/abs/2512.13564)) — Tre-dimensional taksonomi: Forms, Functions, Dynamics. Identificerer multi-agent memory og trustworthiness som åbne frontiers. [Paper list](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
- **"Memory in LLM-based MAS"** (2025, [TechRxiv](https://www.techrxiv.org/users/1007269/articles/1367390/)) — Rammer transition fra individual-level til collection-level kognition. Taksonomi: shared pools, hybrid designs, hierarkiske designs.
- **MemAgents: ICLR 2026 Workshop** (27. april 2026, [site](https://sites.google.com/view/memagent-iclr26/)) — Eksplicit open problems: neuroscience-inspired memory, benchmarks for long-horizon, standardiserede metrics.
- [Awesome-Memory-for-Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents) (Tsinghua C3I)
- [Awesome-Agent-Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory) (TeleAI)

---

## Identificerede Arkitektur-Mønstre

### Knowledge Sharing Patterns

| Pattern | Eksempel | Styrker | Svagheder |
|---|---|---|---|
| **Shared Memory Pool (Blackboard)** | MegaAgent, CrewAI | Simpelt | Ingen access control, provenance, eller write contention handling |
| **Two-Tier Private/Shared med Provenance** | Collaborative Memory | Sofistikeret access control, auditability | Kun permission-baseret, ikke relevans-baseret |
| **Hierarkisk Individual/Buffer/Collective** | Survey pattern | Multi-indicator evaluation gates | Periodisk sync, ikke real-time |
| **Agent-Scoped Layers** | Cognee | Domæne-specifik deling | Statisk scoping |
| **MemCube Composition** | MemOS | Fleksibel isolation/deling | Kompleks management |

### Conflict Resolution Patterns

| Pattern | Eksempel | Egenskaber |
|---|---|---|
| **LLM-Based Debate** | KARMA | Høj kvalitet, høj latency/cost |
| **Provenance-Based** | Collaborative Memory | Deterministisk, kræver god provenance |
| **CRDT-Based Deterministisk** | CodeCRDT | Zero data loss, strong eventual consistency, <200ms |
| **Orchestrator Serialization** | Survey pattern | Simpelt, single point of bottleneck |

### Memory Quality Patterns

| Pattern | Eksempel | Mekanisme |
|---|---|---|
| **Trust + Persistence Score** | MARK | Multi-faktor probabilistisk scoring |
| **Ebbinghaus Forgetting Curve** | SAGE | Tidsbaseret decay + frekvens-boost |
| **Bi-temporal Validity** | Graphiti/Zep | Valid_at / invalid_at intervals |
| **Retroaktiv Self-Refinement** | A-MEM | Nye facts trigger opdateringer af eksisterende |

---

## Hvad INGEN har bygget — Feltets Fundamentale Gaps

_Verificeret mod al surveyed litteratur og frameworks pr. marts 2026._

### Gap 1: Push-Baseret Inter-Agent Memory Propagation
Ingen system implementerer event-driven push af lært viden fra én agent til en anden. Nemori's Predict-Calibrate er intra-agent. Collaborative Memory's shared pool er pull-baseret. Alt er polling eller query mod shared state. Ingen pub/sub model hvor Agent A's nye fact automatisk propagerer til Agent B baseret på relevans-subscriptions.

### Gap 2: Distributed Consistency for Agent Memory
CodeCRDT demonstrerer CRDTs for kode-koordinering, men ingen har appliceret distributed systems consistency-modeller (CRDTs, vector clocks, causal consistency) til generel agent knowledge/memory. Alle shared memory systemer antager centraliseret store.

### Gap 3: Cross-Agent Conflict Resolution med Temporal Awareness
KARMA har debate. Graphiti har bi-temporal tracking. Ingen kombinerer dem. Intet system detekterer modsatrettede beliefs, overvejer temporal validity af hver belief, og resolver via både provenance og temporal evidens.

### Gap 4: Cross-Agent Trust Propagation
MARK har Trust Score, men kun single-agent. Intet system hvor Agent A's trust i et fact influeres af hvor mange andre agenter der har korroboreret det, eller hvor pålidelige de agenter historisk er. Multi-agent reputation-weighted trust propagation eksisterer ikke.

### Gap 5: Relevans-Baseret Selektiv Deling
Collaborative Memory modellerer *hvem der må*. Cognee modellerer *domæne-scope*. Ingen har bygget: system der automatisk bestemmer hvilke af Agent A's nye facts ville være *nyttige* for Agent B baseret på B's aktuelle task, rolle, og eksisterende viden.

### Gap 6: Koordineret Memory Garbage Collection
Individuelle systemer har forgetting curves (SAGE) og temporal invalidation (Graphiti). I multi-agent settings: ingen protokol for hvornår et shared fact skal fjernes. Hvad hvis én agent stadig afhænger af det? Distributed memory lifecycle management eksisterer ikke.

### Gap 7: Causal Provenance Chains Across Agents
Intet system tracker den kausale kæde af hvordan viden blev afledt på tværs af agent-interaktioner. Hvis Agent C har et belief der originalt blev opdaget af A, raffineret af B, og integreret af C — ingen provenance-kæde fanger denne multi-hop derivation.

### Gap 8: Multi-Agent Memory Benchmarks
Eksisterer ikke. LOCOMO og LongMemEval tester single-agent. DMR tester retrieval accuracy. Ingen benchmark tester: propagation latency, conflict detection rate, shared memory consistency, eller quality degradation curve.

### Gap 9: Memory Schema Evolution
Ingen system håndterer hvad der sker når schema/ontologi for shared memory skal evolvere. Ingen migrations-protokol, ingen schema-versionering.

### Gap 10: Heterogen Memory Format Interoperabilitet
Forskellige agenter bruger forskellige memory-repræsentationer. Ingen translationslag eller protokol for at dele knowledge på tværs af formater uden text som lowest-common-denominator.

---

## Opsummering: Hvad eksisterer vs. hvad gør ikke

| Capability | Eksisterer? | Bedste Implementation | Gap |
|---|---|---|---|
| Shared memory pool | Ja | MegaAgent, CrewAI | Trivielt, ingen sofistikering |
| Access-controlled deling | Ja | Collaborative Memory | Ingen relevans-filtrering |
| Temporal validity of facts | Ja | Graphiti/Zep | Ikke integreret med multi-agent conflict resolution |
| Trust/quality scoring | Delvist | MARK (single-agent) | Ingen cross-agent trust propagation |
| Conflict resolution | Delvist | KARMA (debate), CodeCRDT (CRDT) | Ikke kombineret med temporal awareness |
| Event-driven propagation | Nej | — | Fundamentalt gap |
| Distributed consistency | Nej | — | Fundamentalt gap |
| Koordineret GC/compaction | Nej | — | Fundamentalt gap |
| Multi-agent benchmarks | Nej | — | Fundamentalt gap |
| Causal provenance chains | Nej | — | Fundamentalt gap |
| Memory format interop | Nej | — | Fundamentalt gap |

---

_Dokument opdateret 2026-03-10. Research phase complete. Næste skridt: top-level spec → arkitektur-design._
