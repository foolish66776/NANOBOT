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
