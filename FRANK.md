# FRANK.md — Patch Registry

This file tracks every modification made to nanobot for the Frank deployment.
**After every upstream merge, re-apply any patch that was overwritten and update the status here.**

**Last upstream sync**: `2026-07-08` — merged `upstream/main` (197 commits, 2026-06-24 → 2026-07-07). All patches P02–P06 verified intact. See P07 for the one real conflict. Also fixed a pre-existing bug found by `ruff` (F823) in the `/hooks/foolish-storefront-cron` webhook handler in `nanobot/cli/commands.py`: a local variable named `cron` shadowed the outer `CronService` instance used by the `/hooks/foolish-storefront-order` handler, silently breaking the "verifica pipeline a +5 min" follow-up job since 2026-06-08 (the exception was swallowed by a `except Exception` and only logged as a warning).

---

## How to use after an upstream merge

1. Read this file in full.
2. For each patch, check if the target file still contains the change (grep for the key symbol or function name).
3. Re-apply any patch that was lost. Mark it `re-applied` with the date.
4. Run `ruff check nanobot/` and restart Frank.

## Secrets and environment

- Shared process variables live in `~/.nanobot/.env`.
- Frank-specific secrets live in `~/.config/nanobot/frank.env`.
- The systemd unit loads both files; do not reintroduce secrets into the unit file.
- Keep `frank.env` readable only by the deploy user (`chmod 600`).
- If a secret was exposed in chat, logs, or a unit file, rotate it and refresh the env file before the next restart.

### Frank env schema

Keep the service-specific file grouped like this:

```text
# Core runtime
NANOBOT_OBSIDIAN_VAULT

# Model / LLM providers
DEEPSEEK_API_KEY
OPENROUTER_API_KEY

# Telegram / restart notifications
FRANK_TELEGRAM_BOT_TOKEN
FRANK_TELEGRAM_CHAT_ID

# Shipping / storefront
PACKLINK_API_KEY
PACKLINK_SENDER_NAME
PACKLINK_SENDER_SURNAME
PACKLINK_SENDER_STREET
PACKLINK_SENDER_CITY
PACKLINK_SENDER_ZIP
PACKLINK_SENDER_COUNTRY
PACKLINK_SENDER_PHONE
PACKLINK_SENDER_EMAIL
FOOLISH_WOO_BASE_URL
FOOLISH_WOO_CONSUMER_KEY
FOOLISH_WOO_CONSUMER_SECRET
FOOLISH_PAYLOAD_URL
FOOLISH_PAYLOAD_EMAIL
FOOLISH_PAYLOAD_PASSWORD
FOOLISH_PAYLOAD_SECRET
FOOLISH_STOREFRONT_DIR
FOOLISH_CUSTOMER_BOT_TOKEN
FOOLISH_CUSTOMER_WH_SECRET

# Analytics / reporting
RESEND_API_KEY
FOOLISH_UMAMI_URL
FOOLISH_UMAMI_USERNAME
FOOLISH_UMAMI_PASSWORD
FOOLISH_UMAMI_WEBSITE_ID

# Integrations
AGENTMAIL_CONCR3TICA_API_KEY
AGENTMAIL_FOOLISH_API_KEY
GITHUB_TOKEN
```

Shared process variables such as `TAVILY_API_KEY` stay in `~/.nanobot/.env`.

---

## Patches

### P01 — REVERTED (non necessario)
**Status**: reverted `2026-06-15`
**Problema originale**: notifiche spam da cron job.
**Causa vera**: i job senza notifica erano configurati come cron Telegram invece di stare nell'heartbeat. Frank ha già l'architettura giusta — cron Telegram per notifiche, heartbeat per job silenti. Non serve patch al codice.
**Azione**: nessuna modifica a `loop.py`. Se un job spamma, dirlo a Frank e farglielo spostare nell'heartbeat.

---

### P02 — Env vars override config JSON api_key
**Status**: applied `2026-06-15`
**Commit**: `ebf8c391`
**Problem**: Railway env vars (e.g. `MINIMAX_API_KEY`) were ignored when a stale key was embedded in `NANOBOT_CONFIG_JSON`. Env vars must win.
**Files changed**:
- `nanobot/config/schema.py` — `get_api_key()`: before returning `p.api_key`, check `os.environ.get(spec.env_key)` via provider registry

**Key invariant to verify after merge**:
```python
# In schema.py get_api_key(), env var lookup must exist before return:
if model:
    from nanobot.providers.registry import find_by_name
    ...
    env_val = os.environ.get(spec.env_key, "").strip()
    if env_val:
        return env_val
```

---

### P03 — VisionAugmentedProvider + vision chain
**Status**: applied `2026-06-15`
**Commit**: `448ca84b` (partial), `67f30584`
**Problem**: Text-only models (DeepSeek) couldn't handle image inputs. Need automatic image→text fallback.
**Files added**:
- `nanobot/providers/vision_augmented_provider.py`
- `nanobot/providers/vision_chain.py`

**Key invariant to verify after merge**: both files exist and `factory.py` wraps providers with `VisionAugmentedProvider`.

---

### P04 — Custom tools
**Status**: applied `2026-06-15`
**Commit**: `448ca84b`
**Files added** (Frank-specific tools, not upstream):
- `nanobot/agent/tools/playwright_render.py` — HTML/CSS → PNG/PDF
- `nanobot/agent/tools/subconscious.py` — Obsidian vault FTS + memory sync
- `nanobot/agent/tools/packlink.py` — Packlink Pro shipping

These files are Frank-specific and will never be in upstream. Verify they exist after merge.

---

### P05 — Subconscious subsystem
**Status**: applied `2026-06-15`
**Commit**: `448ca84b`
**Files added**:
- `nanobot/subconscious/__init__.py`
- `nanobot/subconscious/store.py`
- `nanobot/subconscious/vault.py`
- `nanobot/subconscious/sync.py`

Verify after merge: directory `nanobot/subconscious/` exists with all 4 files.

---

### P06 — Skills (Frank-specific)
**Status**: applied `2026-06-15`
**Commit**: `448ca84b`, `942226f3`
**Files added** (not upstream):
- `nanobot/skills/packlink/SKILL.md`
- `nanobot/skills/spark/SKILL.md`
- `nanobot/skills/subconscious/SKILL.md`
- `nanobot/skills/apps/SKILL.md`

Verify after merge: all 4 skill directories exist.

---

### P07 — WhatsApp bridge dropped in favor of upstream neonize rewrite
**Status**: applied `2026-07-08`
**Commit**: merge of `upstream/main` (`2a9e288d "refactor(whatsapp): replace bridge with neonize"`)
**Decision**: upstream removed the Node.js/TypeScript bridge (`bridge/`) entirely and rewrote
`nanobot/channels/whatsapp.py` to talk to WhatsApp Web directly via the `neonize` Python library.
WhatsApp was already disabled on Frank (`config.channels.whatsapp.enabled: false`, systemd unit
`nanobot-whatsapp-bridge.service` inactive), so we took upstream's version instead of keeping the
old bridge — no working feature was lost.

**Files removed** (were Frank-tracked, now gone — do not re-add):
- `bridge/` (whole directory: `package.json`, `src/*.ts`, `tsconfig.json`)

**Stale, not in git — clean up manually if/when WhatsApp gets re-enabled**:
- `~/.nanobot/whatsapp-bridge.service` (old systemd unit file, references `bridge/dist/index.js` which no longer exists)
- systemd user unit `nanobot-whatsapp-bridge.service` (disabled, inactive — safe to `systemctl --user disable --now` and delete)
- `~/.nanobot/whatsapp-auth/` (old bridge's auth session — incompatible with neonize's auth format; a fresh QR pairing will be needed)

**Key invariant to verify after merge**: `bridge/` directory does not exist; `nanobot/channels/whatsapp.py` imports `neonize`, not a bridge websocket client.

---

### P08 — REVERTED: local Ollama watchdog for email/order triage
**Status**: reverted `2026-07-08`
**Problema**: idea di usare un modello locale (Ollama) per classificare email/ordini in arrivo (URGENTE/ORDINE/SPAM) ogni 5 minuti, per risparmiare token DeepSeek su un task di triage semplice.
**Perché è stato abbandonato**:
- Il servizio di sistema `ollama.service` era in crash-loop da giorni (91.000+ tentativi di riavvio falliti, `mkdir /usr/share/ollama: permission denied`) — mai raggiungibile su `127.0.0.1:11434`.
- `scripts/local-ai-watchdog.py` (rimosso) aveva `get_pending_items()` non implementato — nessun collegamento reale ad AgentMail o al CMS, solo stub. Anche con Ollama funzionante, il job non avrebbe mai classificato nulla.
- Il cron `local-ai-watchdog` girava ogni 5 minuti come no-op silenzioso, senza che nessuno se ne accorgesse.
**Decisione**: `ollama.service` fermato e disabilitato (richiede sudo, eseguito manualmente). Cron `local-ai-watchdog` disabilitato in `~/.nanobot/cron/jobs.json`. Script rimosso dal repo. Email/ordini/CMS tornano sotto Frank stesso, tramite i tool esistenti (`agentmail_list`, `packlink_track`) e il cron `cms-packlink-check`.
**Se si riprova in futuro**: prima verificare che Ollama resti stabile per qualche giorno prima di costruirci sopra un job, e scrivere `get_pending_items()` per intero (non solo lo scaffold) prima di attivare il cron.

---

## Architettura cron di Frank

**REGOLA FONDAMENTALE — non modificare il codice se arriva spam da cron.**

Frank usa due meccanismi distinti per i job periodici:

| Meccanismo | Quando usarlo | Come si configura |
|---|---|---|
| **Cron Telegram** (`~/.nanobot/cron/jobs.json`) | Job che devono mandare una notifica all'utente | Frank lo crea con il tool `cron` |
| **Heartbeat** (task list interna) | Job silenti, check periodici, manutenzione | Frank lo aggiunge alla sua task list interna |

Se un job nel cron Telegram non ha nulla da comunicare ma viene eseguito comunque, il problema è che **quel job sta nel posto sbagliato** — va spostato nell'heartbeat. Non serve toccare `loop.py` o aggiungere flag `silent`.

**Se arriva spam**: dire a Frank su Telegram "sposta X nell'heartbeat" — lui sa già come farlo.

**Non fare mai**: aggiungere logica di soppressione in `loop.py` per silenziare cron turn. È stato fatto per errore il 2026-06-15 e poi revertito (vedi P01).

---

## Jobs config (`~/.nanobot/cron/jobs.json`)

Tutti i job qui sotto devono stare nel cron Telegram perché **consegnano sempre qualcosa** all'utente. Se un job smette di avere senso come notifica, va rimosso da qui e aggiunto all'heartbeat.

| job name | note |
|---|---|
| `scribacchino-digest` | riassunto giornaliero |
| `cms-packlink-check` | stato spedizioni |
| `sebo-concept-8am` | concepts mattina |
| `sebo-concept-4pm` | concepts pomeriggio |
