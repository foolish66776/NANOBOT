---
name: produce-workflow-spec
description: Trasforma una discussione su un'idea in un file workflow-spec.md strutturato, pronto per il Council. Skill sempre attiva per tutte le business line.
always: true
---

# Skill: produce-workflow-spec

## Quando attivarsi

Attivati quando Alessandro dice qualcosa come (in qualsiasi forma):

- "Ok, mi sembra che siamo pronti. Fissiamo questa idea."
- "Trasformiamo questa in qualcosa di operativo."
- "Fissiamo la spec."
- "Voglio formalizzare questa idea."
- "Costruiamo il workflow."
- "Mettiamola nero su bianco."
- "Metti giù la spec."

**Non** attivarti per richieste vaghe di brainstorming o domande teoriche. Attivati solo quando c'è un'intenzione chiara di fissare qualcosa di concreto.

## Step 1 — Conferma

Prima di procedere, chiedi conferma esplicita:

> "Ho capito. Prima di costruire il workflow, ti propongo di fissare la spec. Procedo?"

Attendi una conferma esplicita ("sì", "vai", "ok", "procedi"). Se Alessandro modifica lo scope, recepiscilo prima di generare.

## Step 2 — Genera la spec draft

Leggi gli ultimi messaggi della conversazione corrente per estrarre:
- Cosa fa il workflow
- Perché serve
- Trigger (cron / webhook / manual / event)
- Input e output
- Step logici
- Effetti esterni (email, API, file)
- Limiti hard dichiarati da Alessandro
- Rischi menzionati

Genera la spec seguendo **esattamente** questo template (nessun campo omesso):

```
# Workflow Spec: <titolo breve>

ID: <YYYY-MM-DD-slug-kebab-case>
Business line: <personal | concr3tica | studio-penale | youtube>
Status: draft
Created: <ISO date>
Updated: <ISO date>
Persona ref: (vuoto — compilato nel Step 4)

## Cosa fa

<2-3 frasi in italiano, leggibili da un imprenditore in 30 secondi>

## Perché

<contesto: quale problema risolve, perché ora>

## Trigger

Tipo: <cron | webhook | manual | event>
Dettagli: <quando parte, con quale frequenza o condizione>

## Input

<da dove vengono i dati: file CSV, API, form, ecc. Path o URL precisi se disponibili.>

## Step

1. <step 1>
2. <step 2>
...

## Output

<cosa produce: email mandate, file scritti, notifiche, ecc.>

## Effetti esterni dichiarati

<lista esplicita di cosa il workflow tocca fuori dal sistema.
Esempi: "manda email via AgentMail", "scrive su Google Sheet X">

## Limiti hard

<regole inviolabili. Esempi:
- Mai più di N azioni per esecuzione
- Solo tra le 09:00 e le 18:00 ora italiana
- Mai a indirizzi nel file blacklist.csv>

## Frequenza attesa

<ogni quanto ti aspetti che parta>

## Ipotesi di rischio

<cosa potrebbe andare male, lista esplicita>

## Council notes

(compilato dopo Council)

## Approvazione finale

(compilato all'approvazione)
```

**Mostra la spec ad Alessandro come testo** (non salvarla ancora).

## Step 3 — Iterazione spec

Dopo aver mostrato la spec:
- Se Alessandro dice "ok" o varianti → vai allo Step 4
- Se Alessandro suggerisce modifiche → aggiorna la spec e mostrala di nuovo
- Continua finché non c'è un'approvazione esplicita

L'approvazione richiede: "ok", "approvata", "vai", "salvala", "perfetta", o simile. Non interpretare frasi ambigue come approvazione.

## Step 4 — Persona library: cerca o crea

Questo step costruisce il cliente target per il Council.

### 4a — Cerca nella library esistente

Leggi i file in `~/dev/nanobot-workspace/_personas-library/` con `list_dir` o `read_file`.

Per ogni file trovato, leggi il frontmatter (ID, titolo) e le prime righe (Demografia, Psicografia).

Cerca un match per keyword con il contesto della spec (business line, tipo di cliente, settore). Considera un match se almeno 2-3 caratteristiche chiave coincidono (es. commercialista + Nord Italia + 45-55 anni).

### 4b — Proponi riuso o creazione

**Se trovi un match:**
> "Ho trovato un persona simile nella library: `<slug>` — <titolo descrittivo>.
> [mostra le prime 4-5 righe del file]
> Vuoi riutilizzarlo per questa spec, o preferisci crearne uno nuovo?"

Se Alessandro dice "riusa" → vai al 4d con lo slug esistente.
Se Alessandro dice "nuovo" → vai al 4c.

**Se non trovi match:**
> "Non ho trovato un persona adatto nella library. Descrivimi il cliente target di questo workflow: chi è, che lavoro fa, che età ha, dove vive?"

### 4c — Genera nuovo persona

Basandoti sulla risposta di Alessandro e sul contesto della spec, genera il persona con questo template esatto:

```markdown
# Persona: <titolo descrittivo in 3-5 parole>

ID: <slug-kebab-case>
Creato: <ISO date>
Creato per idea: <spec-id>
Riutilizzato da:

## Demografia
<età, genere, città, lavoro, dimensione studio/azienda>

## Psicografia
<come pensa, che rapporto ha con la tecnologia, valori>

## Giornata tipo
<come passa le ore, dove ha i buchi>

## Frustrazioni reali
<problemi concreti, quotidiani, sentiti>

## Cosa lo fa agire
<trigger di azione, cosa lo converte>

## Cosa lo fa ignorare
<trigger di rifiuto, cosa lo fa scappare>
```

Mostra il persona ad Alessandro. Itera se richiede modifiche. Quando approva:

Salva il file in `~/dev/nanobot-workspace/_personas-library/<slug>.md` con `write_file`.

### 4d — Aggiorna persona_ref nella spec

Aggiorna il campo `Persona ref:` nella spec con lo slug del persona scelto:

```
Persona ref: <slug>
```

## Step 5 — Salva il file spec

Determina la business line dalla conversazione (se non è chiara, chiedi).

Path di destinazione:
```
~/dev/nanobot-workspace/<business>/specs/<ID>.md
```

Dove `<ID>` è il campo `ID` della spec (es. `2026-04-16-morning-brief`).

Usa `write_file` per salvare il contenuto completo della spec approvata con il campo `Persona ref:` compilato.

Conferma ad Alessandro il path del file salvato.

## Step 6 — Aggiorna _state.md

Leggi `~/dev/nanobot-workspace/<business>/_state.md`.

Aggiungi la spec nella sezione `## Active Ideas`:

```
- `<ID>` — <titolo> (status: draft, attende Council)
```

Aggiorna il campo `Last update` con la data ISO odierna.

Usa `edit_file` per applicare la modifica.

## Step 7 — Aggiorna ORCHESTRATION.md

Leggi `~/dev/nanobot-workspace/ORCHESTRATION.md`.

Per la business line corretta:
- Incrementa `Specs in pipeline` di 1

Nella sezione `## Recent events (last 7 days)`:
- Aggiungi una riga: `- <ISO date> — Nuova spec: <titolo> (<business>) — attende Council`

Aggiorna `Last update`.

Usa `edit_file` per applicare la modifica.

## Step 8 — Notifica orchestrator

Chiama il tool `orchestrator_notify` con il messaggio:

```
Nuova spec: <titolo> (<business>) — in attesa di Council.
Path: ~/dev/nanobot-workspace/<business>/specs/<ID>.md
Persona: <slug o "non assegnato">
```

Se il tool risponde che l'orchestrator non è ancora configurato, va bene: loggalo e prosegui.

## Step 9 — Messaggio finale ad Alessandro

Informa Alessandro che:
1. La spec è salvata in `<path>`
2. Il persona è in `~/dev/nanobot-workspace/_personas-library/<slug>.md` (se creato)
3. Il prossimo passo è il Council: `nanobot council run <ID>`
4. **Il Council non parte automaticamente** — deve essere lanciato esplicitamente

Esempio di messaggio finale:
> "Spec salvata in `~/dev/nanobot-workspace/<business>/specs/<ID>.md`.
> Persona: `<slug>` (riutilizzato / nuovo).
> Quando sei pronto, lancia il Council con: `nanobot council run <ID>`
> Il Council non parte da solo — aspetta il tuo via."

---

## Note operative

- Una spec = un file. Mai due workflow nello stesso file.
- Lo slug dell'ID deve essere kebab-case, descrittivo, mai spazi.
- Lo slug del persona deve essere descrittivo (es. `commercialista-novara-52`, `founder-saas-b2b`).
- Se la business line non è determinabile dalla conversazione, chiedi prima di salvare.
- Non avviare il Council automaticamente. È un checkpoint umano voluto.
- Se `write_file` fallisce, segnala l'errore ad Alessandro senza simulare il salvataggio.
- Il campo `Riutilizzato da:` nel persona viene aggiornato automaticamente dal runner.py quando il Council viene lanciato — non serve farlo manualmente qui.
