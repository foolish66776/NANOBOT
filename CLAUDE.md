# CLAUDE.md — Nanobot Refactor: Memory & Multi-Business-Line Architecture

> Briefing for Claude Code. Read this entire file before touching any code.
> Ask clarifying questions only if something is genuinely ambiguous — otherwise, proceed.

---

## 1. Context & Goal

**Project owner:** Alessandro (Concr3tica). Runs AI automation consulting for Italian professional firms. Works primarily in Italian but all code, comments, commit messages, and docs in this repo stay in **English**.

**Current state:** Nanobot (HKUDS/nanobot, ~4k LOC Python) is installed from source in editable mode at this repo path. It works, but `agent/memory.py` is too naive: it loses context and settings as workload grows across sessions and topics. The official roadmap lists "long-term memory" as unimplemented — we are going to implement it.

**Goal:** Turn nanobot from a single-purpose personal assistant into a **multi-business-line personal agent platform** backed by a real memory engine, while keeping nanobot's minimalism philosophy intact. No bloat, no unnecessary abstractions, no over-engineering. Every line of code added must justify itself.

**Non-goals:**
- Do NOT rewrite parts of nanobot that already work (agent loop, skills loader, providers, cron, channels gateway). Touch them only where the refactor requires it.
- Do NOT change the CLI UX drastically — extend it, don't replace it.
- Do NOT introduce heavy new dependencies unless strictly needed.
- Do NOT attempt to migrate existing memories from the old `memory.py` format. Clean start. The old data is backed up at `~/.nanobot.backup-pre-refactor/`.

---

## 2. The Two Problems We're Solving

### 2.1 Memory problem
Current `agent/memory.py` is a flat persistent store with no fact extraction, no user profile, no temporal resolution, no forgetting, no semantic retrieval. As conversations accumulate, signal-to-noise collapses and the agent starts ignoring or forgetting preferences.

**Solution:** Introduce a `MemoryBackend` abstract interface and plug in **Supermemory** (self-hosted on Railway EU) as the default implementation. Supermemory handles fact extraction, static/dynamic profiles, hybrid search (RAG + memory), and automatic forgetting. It's #1 on LongMemEval, LoCoMo, and ConvoMem.

### 2.2 Single-context problem
Alessandro runs multiple parallel workstreams — personal, Concr3tica consulting, Studio Penale AI (client project), an upcoming YouTube channel, and future business ideas. A single flat memory means contexts bleed into each other. He also wants **cross-pollination on demand** (e.g. "what did I learn this month that could become content?").

**Solution:** Introduce a first-class `BusinessContext` concept. Memories are tagged with a hierarchical container tag (`alessandro/personal`, `alessandro/concr3tica`, `alessandro/studio-penale`, `alessandro/youtube`, etc.). Each business line has its own static profile (tone, goals, audience, KPIs) and dynamic memory. The agent loads only the relevant context per turn, but can query across tags on demand.

---

## 3. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Channels (Telegram, WhatsApp)                  │
│   Multiple Telegram bots → single backend, tagged per bot   │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Gateway / Bus                             │
│       Resolves BusinessContext from incoming message         │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Loop (loop.py)                      │
│                                                              │
│   Prompt Builder (context.py)                                │
│     ├── load BusinessContext static profile                  │
│     ├── fetch dynamic profile from MemoryBackend             │
│     ├── fetch top-k relevant memories for current turn       │
│     └── assemble system prompt                               │
│                                                              │
│   LLM call → tool use → loop                                 │
│                                                              │
│   On turn end: MemoryBackend.add(user + assistant msg)       │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  MemoryBackend (interface)                   │
│                                                              │
│   Implementations:                                           │
│     • LocalMemory        (existing, fallback, always works)  │
│     • SupermemoryBackend ← DEFAULT, self-hosted on Railway   │
│     • Mem0Backend        (stub, plan B — see Phase 0)        │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Phased Plan

**Work in phases. Do NOT start a phase until the previous one is verified working.** After each phase, run `nanobot status` and `nanobot agent -m "hello"` to confirm nothing is broken. Commit at the end of each phase with a clear message.

---

### Phase 0 — Supermemory self-hosted on Railway EU

This is a **sub-project, separate from the nanobot codebase**. Work in a sibling directory (`../supermemory-selfhost/`), not inside this repo.

**Steps:**

1. Clone `https://github.com/supermemoryai/supermemory` into `../supermemory-selfhost/`.
2. Read their `CLAUDE.md`, `README.md`, and the `apps/` + `packages/` structure. Identify the **minimum set of services** needed to expose the memory API — typically an API service and a memory engine worker. Do NOT deploy the consumer web app (`apps/web`) or the browser extension.
3. The monorepo targets **Cloudflare Workers**. Railway runs **standard Node/Bun containers**. Adapt what's necessary:
   - If an `apps/api` or equivalent already runs on standard Node/Bun, prefer deploying that as-is.
   - If only a Workers-based entry point exists, adapt it to a standard Node/Bun server. Hono (which Supermemory likely uses) runs identically on both runtimes for HTTP handlers — the friction is usually around Workers-specific bindings: KV, Durable Objects, Queues, R2. Replace each with a Node-compatible equivalent:
     - **KV** → Postgres table or Redis
     - **Durable Objects** → Postgres row with advisory locks, or a lightweight in-process state
     - **Queues** → BullMQ on Redis, or a Postgres-backed queue
     - **R2** → Railway volume or S3-compatible storage
   - Keep changes minimal. Do not refactor their code aesthetically. The goal is "it runs on Node," not "it's pretty."
4. Provision:
   - **Railway Postgres** in the EU region (Drizzle ORM is what Supermemory uses — it should connect via `DATABASE_URL`).
   - **Railway Redis** if needed for queues/cache.
   - Deploy all Supermemory services to **Railway EU region** (same as Studio Penale AI).
5. Generate an API key / auth mechanism for nanobot to use. If Supermemory's API expects `Authorization: Bearer sm_...`, mint one and store it.
6. **Smoke test with curl:**
   ```bash
   # add
   curl -X POST $BASE/v3/memories \
     -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"content":"Alessandro prefers async Python","containerTag":"alessandro/personal"}'

   # search
   curl -X POST $BASE/v3/search \
     -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"q":"coding preferences","containerTag":"alessandro/personal"}'

   # profile
   curl -X POST $BASE/v3/profile \
     -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"containerTag":"alessandro/personal"}'
   ```
   (Exact endpoint paths may differ — verify against Supermemory source. Document the real paths in `SUPERMEMORY_DEPLOY_NOTES.md`.)
7. Write `SUPERMEMORY_DEPLOY_NOTES.md` inside `../supermemory-selfhost/` covering: which services are deployed, env vars, exact API endpoints, auth scheme, and any patches applied to the original source. This is critical for future maintenance.

**Exit criterion:** a reachable HTTPS endpoint on Railway EU implementing `add`, `search`, and `profile`, authenticated by API key, with data persisted in Railway Postgres EU.

**Fallback policy (Plan B — Mem0):**
If after **2–3 full working days** Phase 0 is still blocked by deep Workers-to-Node incompatibilities (not routine debugging — genuine architectural issues that would require forking and rewriting significant portions of Supermemory), **stop and switch to Plan B**:
- Use `mem0ai/mem0` Python library, self-hosted locally with Postgres + pgvector (both of which Alessandro already runs in Studio Penale AI).
- Phase 1 onward stays **identical** — only the backend implementation class changes (you'll implement `Mem0Backend` instead of `SupermemoryBackend` as the default).
- Before switching, write a short `PHASE0_FALLBACK_REPORT.md` explaining exactly what blocked Supermemory and why Mem0 is the right pivot. This is a decision Alessandro needs to see, not just a silent change.

Do not switch earlier than 2 full working days of genuine effort. Do not push past 3 without raising it.

---

### Phase 1 — MemoryBackend interface & LocalMemory refactor

Back inside the nanobot repo.

**Steps:**

1. Convert `nanobot/agent/memory.py` into a package: `nanobot/agent/memory/`. The existing logic moves to `nanobot/agent/memory/local.py`. Keep all existing behavior intact — this phase is pure refactor, no functional change.
2. Create `nanobot/agent/memory/base.py` with the abstract interface:
   ```python
   from abc import ABC, abstractmethod
   from dataclasses import dataclass, field

   @dataclass
   class MemoryHit:
       content: str
       score: float
       metadata: dict = field(default_factory=dict)
       memory_id: str | None = None

   @dataclass
   class UserProfile:
       static: list[str] = field(default_factory=list)   # long-term facts
       dynamic: list[str] = field(default_factory=list)  # recent context

   class MemoryBackend(ABC):
       @abstractmethod
       async def add(
           self,
           content: str,
           container_tag: str,
           metadata: dict | None = None,
       ) -> None: ...

       @abstractmethod
       async def search(
           self,
           query: str,
           container_tag: str,
           limit: int = 10,
       ) -> list[MemoryHit]: ...

       @abstractmethod
       async def get_profile(self, container_tag: str) -> UserProfile: ...

       @abstractmethod
       async def forget(self, memory_id: str, container_tag: str) -> None: ...
   ```
3. Make `LocalMemory(MemoryBackend)` in `memory/local.py` by wrapping the existing logic. `get_profile` can be naive: return the last N unique facts as `dynamic`, leave `static` empty. `forget` deletes by id.
4. Create `nanobot/agent/memory/__init__.py` with a factory:
   ```python
   def build_memory_backend(config: dict) -> MemoryBackend:
       backend = config.get("memory", {}).get("backend", "local")
       if backend == "local":
           return LocalMemory(...)
       if backend == "supermemory":
           return SupermemoryBackend(...)  # added in Phase 2
       if backend == "mem0":
           return Mem0Backend(...)         # optional stub
       raise ValueError(f"Unknown memory backend: {backend}")
   ```
5. Update `agent/loop.py` and `agent/context.py` to use the abstract interface via dependency injection. Concrete backend classes must only be imported inside `memory/__init__.py`.
6. If the old `agent/memory.py` file remains as a shim re-exporting from the package, that's fine — but prefer deleting it and updating imports.

**Exit criterion:** `nanobot agent -m "hello"` runs identically to before, but internally goes through the new abstraction with `backend: "local"` as the effective config (even if not yet written to config.json).

**Commit:** `refactor(memory): introduce MemoryBackend abstraction, port LocalMemory`

---

### Phase 2 — SupermemoryBackend implementation

**Steps:**

1. Create `nanobot/agent/memory/supermemory.py` with `SupermemoryBackend(MemoryBackend)`.
2. Use `httpx.AsyncClient` directly. Do NOT pull in the `supermemory` pip package unless it cleanly supports custom base URLs — a hand-written HTTP client is ~80 lines and keeps us in control of auth, retries, and timeouts.
3. The class takes `base_url`, `api_key`, and an optional `timeout` (default 15s). Use `Authorization: Bearer <key>`. All methods are async.
4. Map interface methods to the REST endpoints you confirmed in Phase 0 (via `SUPERMEMORY_DEPLOY_NOTES.md`).
5. Handle errors gracefully: on network failure or 5xx, log a warning and **fall back to an in-memory cache for the current session** rather than crashing the agent loop. The assistant must keep working even if Supermemory is briefly unreachable.
6. Extend the config schema. Add to `~/.nanobot/config.json`:
   ```json
   "memory": {
     "backend": "supermemory",
     "supermemory": {
       "baseUrl": "https://supermemory-<your-app>.up.railway.app",
       "apiKey": "sm_..."
     }
   }
   ```
   Make the loader tolerant: if `memory` is missing, default to `{"backend": "local"}`.
7. Switch the default backend to `supermemory` **only after** a working smoke test from within nanobot.

**Acceptance test:**
```bash
# Session 1
nanobot agent -m "remember that I prefer async code and FastAPI over Flask"

# Session 2 (fresh process)
nanobot agent -m "what are my web framework preferences?"
# → should mention FastAPI
```

**Commit:** `feat(memory): add SupermemoryBackend, wire as default`

---

### Phase 3 — BusinessContext as first-class concept

**Steps:**

1. New module `nanobot/business/context.py`:
   ```python
   from dataclasses import dataclass, field

   @dataclass
   class BusinessContext:
       id: str                  # "concr3tica"
       name: str                # "Concr3tica"
       container_tag: str       # "alessandro/concr3tica"
       static_profile: str      # multi-line system-prompt fragment
       skills: list[str] = field(default_factory=list)  # enabled skill names, or ["*"] for all
       description: str = ""
   ```
2. Extend config schema — add `businessLines` and `defaultBusinessLine` to `~/.nanobot/config.json`:
   ```json
   "businessLines": {
     "personal": {
       "name": "Personal",
       "containerTag": "alessandro/personal",
       "staticProfile": "TODO: Alessandro's personal assistant context — tone, preferences, daily habits.",
       "skills": ["*"]
     },
     "concr3tica": {
       "name": "Concr3tica",
       "containerTag": "alessandro/concr3tica",
       "staticProfile": "TODO: AI automation consulting for Italian professional firms. Tone: professional, concrete, ROI-focused.",
       "skills": ["web", "github", "calendar"]
     },
     "studio-penale": {
       "name": "Studio Penale AI",
       "containerTag": "alessandro/studio-penale",
       "staticProfile": "TODO: client project for Avv. Bottaccini. GDPR-sensitive. Italian criminal law domain.",
       "skills": ["web"]
     },
     "youtube": {
       "name": "YouTube Channel",
       "containerTag": "alessandro/youtube",
       "staticProfile": "TODO: content creation, audience building, video scripting, thumbnail ideas.",
       "skills": ["web"]
     }
   },
   "defaultBusinessLine": "personal"
   ```
   The `TODO:` placeholders are intentional — Alessandro will flesh them out. Leave them clearly marked.
3. Create `nanobot/business/registry.py`:
   - Loads business lines from config on startup.
   - Exposes `get(id) -> BusinessContext`, `list() -> list[BusinessContext]`, `resolve_from_message(msg, default_id) -> BusinessContext`.
   - `resolve_from_message` is **dumb and deterministic** (no LLM routing in v1): looks for a `/bl:<id>` prefix or a `#<id>` hashtag at the start or end of the message; otherwise returns the default. Strip the marker from the message before passing it to the agent.
4. Wire `BusinessContext` into the agent loop:
   - Gateway resolves the context from the incoming message and passes it down.
   - `context.py` prompt builder uses `context.static_profile` + `memory.get_profile(context.container_tag)` + `memory.search(user_msg, context.container_tag)` to assemble the system prompt.
   - All `memory.add()` calls use `context.container_tag`.
5. Skills filtering: if `context.skills != ["*"]`, the skills loader only loads the named skills for that turn. Keep the existing full-load behavior when `["*"]`.

**Acceptance test:**
```bash
nanobot agent --business youtube -m "idea per un video sugli agenti AI"
nanobot agent --business concr3tica -m "come presento i nostri servizi a uno studio legale"
# memories from the two should NOT leak into each other
```

**Commit:** `feat(business): multi-business-line support via BusinessContext`

---

### Phase 4 — CLI & channel integration

**Steps:**

1. Extend the CLI:
   - `nanobot agent --business <id> -m "..."` — explicit business line for one-shot mode. Falls back to `defaultBusinessLine`.
   - `nanobot business list` — shows all configured lines with id, name, container tag.
   - `nanobot business show <id>` — dumps the static profile and enabled skills.
   - (Optional, nice-to-have) `nanobot business add <id> --name "..." --tag "..."` — adds a line to config. If this adds complexity, skip it — editing JSON by hand is fine for v1.
2. Cron jobs carry a `business` field. Extend `nanobot cron add --business youtube --name "weekly ideas" ...`. Store it in the job definition. When the cron fires, it resolves the BusinessContext before running the agent.
3. **Multi-bot Telegram support.** Extend config:
   ```json
   "channels": {
     "telegram": {
       "enabled": true,
       "bots": [
         { "token": "...", "businessLine": "personal",   "allowFrom": ["<user_id>"] },
         { "token": "...", "businessLine": "concr3tica", "allowFrom": ["<user_id>"] },
         { "token": "...", "businessLine": "youtube",    "allowFrom": ["<user_id>"] }
       ]
     }
   }
   ```
   Keep **backward compatibility**: the old `telegram.token` + `telegram.allowFrom` layout must still work and map to `bots: [{ ..., businessLine: defaultBusinessLine }]`.
   Each bot runs its own polling loop but shares the single agent + memory backend. Messages from a given bot are automatically tagged with that bot's business line — the user doesn't need `/bl:` markers on Telegram.
4. Document the new options in `README.md` under a new section **"Multi-business-line usage"**. Keep it concise.

**Commit:** `feat(cli,channels): business-line-aware CLI, cron, and multi-bot Telegram`

---

### Phase 5 — Cross-pollination queries (optional, do only if Phases 0–4 are stable)

A small but high-value feature: let Alessandro run queries across multiple business lines when he explicitly asks.

**Steps:**

1. Add a special business line id `all` (reserved — do not allow as a user-defined id). When used, `memory.search` is run across all container tags under `alessandro/*` and results are merged and re-ranked by score.
2. CLI: `nanobot agent --business all -m "what did I learn this month that could become YouTube content?"`
3. This requires `SupermemoryBackend.search` to support tag prefix matching. Check Phase 0 notes — if the deployed API supports container tag wildcards or prefix queries, use them. If not, fall back to N parallel searches (one per business line) and merge client-side.

Skip this phase if it turns out to require non-trivial Supermemory API changes. It's a nice-to-have, not a blocker.

**Commit:** `feat(memory): cross-business-line search via 'all' context`

---

## 5. Guardrails & Conventions

- **Language:** code, comments, docs, commit messages → English. User-facing strings that Alessandro will see on Telegram → keep the existing language or leave as-is.
- **Commit style:** conventional commits (`feat:`, `refactor:`, `fix:`, `docs:`, `chore:`). One logical change per commit.
- **No silent failures.** If memory operations fail, log at WARNING level with enough context to debug. Never swallow exceptions.
- **Async all the way.** Nanobot's loop is async — the memory backend must be too. Do not introduce sync-over-async hacks.
- **Config is authoritative.** Never hardcode container tags, business line ids, or API URLs in Python. Everything goes through config.
- **Backward compatibility.** If `~/.nanobot/config.json` is missing new sections, fall back to sensible defaults. An existing user (= Alessandro, right now) must be able to `git pull` this refactor and have nanobot still work without touching config — it should default to `local` backend and `personal` business line.
- **Don't touch what works.** If a phase seems to require modifying `providers/`, `skills/`, `cron/`, or `bus/` beyond adding hooks, stop and ask. Those are stable surfaces.
- **Test manually after each phase.** `nanobot status`, `nanobot agent -m "hello"`, and the phase-specific acceptance test.
- **File size discipline.** Nanobot is ~4k LOC by design. This refactor should add at most ~800 LOC (interface + Supermemory client + business context + CLI glue). If you're writing more, you're probably over-engineering.

---

## 6. What Alessandro Needs to Do Manually

Things Claude Code cannot do on his behalf — call these out clearly when you reach them:

1. **Railway EU deploy** — provisioning projects, Postgres, Redis, and setting env vars is Alessandro's action. You prepare the code, the Dockerfile/railway.json, and a precise deploy checklist; he clicks through.
2. **API keys** — OpenRouter, Brave, Telegram bot tokens, Supermemory API key once deployed. He pastes them into `~/.nanobot/config.json`.
3. **Telegram bot creation** — for multi-bot setup, he creates N bots via `@BotFather` and provides N tokens.
4. **Static profile content** — the `TODO:` placeholders in each business line. He fills them with the actual voice, goals, and constraints of each line.

---

## 7. Quick Sanity Check Before Starting

Before Phase 0, confirm:
- [ ] This repo is a clone of HKUDS/nanobot at a known commit. Run `git log -1` and note the hash in your first commit message.
- [ ] `pip install -e .` succeeded and `nanobot status` runs.
- [ ] `~/.nanobot.backup-pre-refactor/` exists (user created it before this session).
- [ ] You have read `nanobot/agent/memory.py`, `nanobot/agent/loop.py`, `nanobot/agent/context.py`, and `nanobot/config/` to understand the current shape of things. Do this in the very first step — do not start writing code before having read the existing memory and context modules end-to-end.

When all four are true, start Phase 0.

Good luck. Keep it small, keep it readable, keep it shipping.
