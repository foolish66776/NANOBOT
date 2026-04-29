# Nanobot — Devlog operativo

Questo file documenta lo stato dell'infrastruttura, le decisioni architetturali e le migliorie apportate nel tempo.
Aggiornarlo ogni volta che si fa un intervento significativo.

---

## Infrastruttura attuale

| Componente | Dove gira | Note |
|---|---|---|
| **nanobot gateway** | Railway (production) | `nanobot-gateway-production.up.railway.app` |
| **Workspace** | Railway volume / `/tmp/workspace` | Clonato da GitHub al primo avvio se volume vuoto |
| **Database (pgvector)** | Railway (pgvector service) | Usato da Mem0 e pipeline Foolish |
| **n8n** | Railway (progetto separato) | `n8n-production-a2da.up.railway.app` |
| **Foolish webhook server** | Railway (servizio separato) | Riceve webhook WooCommerce e Packlink |

> ⚠️ **Il gateway NON gira più in locale.** Qualsiasi modifica al codice va committata e pushata su `main` — Railway fa il redeploy automatico. Non usare `nanobot gateway` in locale per test di produzione.

### Deploy

```bash
# Modifiche → Railway le capta in automatico dal push su main
git push

# Log in tempo reale
railway logs --tail 50

# Variabili d'ambiente
railway variables list
railway variables set CHIAVE=valore
```

### Dashboard di monitoraggio

URL: `https://nanobot-gateway-production.up.railway.app/dashboard`
Login: `manager.tfb@gmail.com` (password in 1Password)
Sessione: 12 ore, poi richiede nuovo login.

---

## Variabili d'ambiente critiche (Railway)

| Variabile | Descrizione |
|---|---|
| `NANOBOT_CONFIG_JSON` | Config completa del gateway (JSON inline) |
| `NANOBOT_WORKSPACE_DIR` | Path workspace nel container |
| `NANOBOT_MEMORY_DATABASE_URL` | Postgres per Mem0 |
| `COHERE_API_KEY` | Embeddings per Mem0 |
| `NANOBOT_DASHBOARD_EMAIL` | Email login dashboard |
| `NANOBOT_DASHBOARD_PASSWORD` | Password login dashboard |
| `FOOLISH_PACKLINK_API_KEY` | API key Packlink Pro |
| `FOOLISH_PACKLINK_SENDER_*` | Indirizzo mittente spedizioni |
| `GITHUB_TOKEN` | Per clone workspace al primo avvio |

---

## Changelog

### 2026-04-29 (sessione pomeriggio)

**Fix volume Railway non scrivibile** (`Dockerfile`)
- Il container girava come utente `nanobot` (UID 1000) ma il volume Railway è owned da root → `/data/workspace` non scrivibile → workspace clonata in `/tmp` ad ogni riavvio e persa al restart
- Fix: rimosso `USER nanobot` dal Dockerfile, il container ora gira come root (compatibile con Railway volumes)
- Effetto: dopo il prossimo deploy, `/data/workspace` sarà scrivibile e il workspace persisterà tra i riavvii

**Fix Mem0 API** (`nanobot/agent/memory/mem0_backend.py`)
- Versione aggiornata di mem0ai non accetta più `user_id`/`agent_id` come parametri top-level in `search()` e `get_all()`
- Fix: spostati in `filters={'user_id': ..., 'agent_id': ...}`
- Effetto: eliminati i WARNING costanti `Top-level entity parameters frozenset({'user_id', 'agent_id'}) are not supported` ad ogni messaggio

**Fix NANOBOT_CONFIG_JSON** (Railway env var)
- `workspace` puntava a `~/dev/nanobot-workspace` (path locale) → aggiornato a `/data/workspace`
- Business line `wiki` mancante dalla config → aggiunta (il bot Telegram era già registrato ma senza profilo)

**Fix Packlink error logging** (`nanobot/business/foolish/packlink.py`)
- `raise_for_status()` non loggava il body delle risposte di errore → impossibile diagnosticare i problemi
- Aggiunta `_raise_with_body()` che logga status + body prima di sollevare l'eccezione
- Trim sull'API key per intercettare spazi residui da Railway variables
- Diagnosi confermata: Packlink restituisce **401** (non 404) — l'API key `FOOLISH_PACKLINK_API_KEY` (64 chars, prefix `a20e`) è sbagliata o scaduta
- **Azione richiesta**: ottenere la chiave corretta da Packlink Pro → Settings → Integrations → API, poi `railway variables set FOOLISH_PACKLINK_API_KEY=<nuova_chiave>`

**Fix foolish-storefront build** (`storefront/package.json`, `storefront/package-lock.json`)
- `next-intl` (aggiunto nella sessione mattina) richiede `@swc/helpers>=0.5.17` ma il lock file aveva `0.5.15`
- Fix: `npm install @swc/helpers@latest` → `0.5.21` installato come dep esplicita

### 2026-04-29

**Dashboard di monitoraggio** (`nanobot/gateway/status_server.py`)
- Aggiunto server HTTP embedded nel gateway (porta `$PORT` Railway)
- Espone `/dashboard` (HTML), `/api/status` (JSON), `/health`
- Login con email + password, sessioni cookie 12h, nessuna registrazione
- Mostra in tempo reale: cron jobs (stato, prossima run, cronologia), sessioni attive, log errori WARNING/ERROR

**Fix Mem0 — Cohere API key**
- `COHERE_API_KEY` era presente nel `.env.local` locale ma mancava su Railway
- Aggiunta con `railway variables set` → gli errori `Mem0Backend: Cohere API key not found` spariti dai log

**Packlink — creazione bozze spedizione** (`nanobot/business/foolish/packlink.py`, `nanobot/agent/tools/foolish.py`)
- Nuovo tool `foolish_create_shipment_draft`: l'orchestratore Foolish può creare bozze di spedizione su Packlink Pro da dati cliente
- Nuovo tool `foolish_get_shipping_services`: elenca corrieri e prezzi disponibili per una tratta prima di creare la bozza
- La bozza viene creata in stato `draft` — nessun addebito automatico, Alessandro rivede e paga su Packlink
- Indirizzo mittente configurato via `FOOLISH_PACKLINK_SENDER_*` (Via Chivasso 36, Castelnuovo Don Bosco, AT)
- Flusso: orchestratore crea bozza → manda link `pro.packlink.com/private/shipments/...` → Alessandro paga → webhook esistente aggancia tracking → notifica cliente

---
