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

## Step 3 — Iterazione

Dopo aver mostrato la spec:
- Se Alessandro dice "ok" o varianti → vai allo Step 4
- Se Alessandro suggerisce modifiche → aggiorna la spec e mostrala di nuovo
- Continua finché non c'è un'approvazione esplicita

L'approvazione richiede: "ok", "approvata", "vai", "salvala", "perfetta", o simile. Non interpretare frasi ambigue come approvazione.

## Step 4 — Salva il file

Determina la business line dalla conversazione (se non è chiara, chiedi).

Path di destinazione:
```
~/dev/nanobot-workspace/<business>/specs/<ID>.md
```

Dove `<ID>` è il campo `ID` della spec (es. `2026-04-16-morning-brief`).

Usa `write_file` per salvare il contenuto completo della spec approvata.

Conferma ad Alessandro il path del file salvato.

## Step 5 — Aggiorna _state.md

Leggi `~/dev/nanobot-workspace/<business>/_state.md`.

Aggiungi la spec nella sezione `## Active Ideas`:

```
- `<ID>` — <titolo> (status: draft, attende Council)
```

Aggiorna il campo `Last update` con la data ISO odierna.

Usa `edit_file` per applicare la modifica.

## Step 6 — Aggiorna ORCHESTRATION.md

Leggi `~/dev/nanobot-workspace/ORCHESTRATION.md`.

Per la business line corretta:
- Incrementa `Specs in pipeline` di 1

Nella sezione `## Recent events (last 7 days)`:
- Aggiungi una riga: `- <ISO date> — Nuova spec: <titolo> (<business>) — attende Council`

Aggiorna `Last update`.

Usa `edit_file` per applicare la modifica.

## Step 7 — Notifica orchestrator

Chiama il tool `orchestrator_notify` con il messaggio:

```
Nuova spec: <titolo> (<business>) — in attesa di Council.
Path: ~/dev/nanobot-workspace/<business>/specs/<ID>.md
```

Se il tool risponde che l'orchestrator non è ancora configurato, va bene: loggalo e prosegui.

## Step 8 — Messaggio finale ad Alessandro

Informa Alessandro che:
1. La spec è salvata in `<path>`
2. Il prossimo passo è il Council: `nanobot council run <ID>`
3. **Il Council non parte automaticamente** — deve essere lanciato esplicitamente da Alessandro

Esempio di messaggio finale:
> "Spec salvata in `~/dev/nanobot-workspace/<business>/specs/<ID>.md`.
> Quando sei pronto, lancia il Council con: `nanobot council run <ID>`
> Il Council non parte da solo — aspetta il tuo via."

---

## Note operative

- Una spec = un file. Mai due workflow nello stesso file.
- Lo slug dell'ID deve essere kebab-case, descrittivo, mai spazi.
- Se la business line non è determinabile dalla conversazione, chiedi prima di salvare.
- Non avviare il Council automaticamente. È un checkpoint umano voluto.
- Se `write_file` fallisce, segnala l'errore ad Alessandro senza simulare il salvataggio.
