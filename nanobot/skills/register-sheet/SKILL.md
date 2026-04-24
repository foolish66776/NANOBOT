---
name: register-sheet
description: Registra un foglio fisico appena prodotto da Alessandro nel database Foolish Butcher tramite linguaggio naturale. Solo per business line foolish.
always: true
---

# Skill: register-sheet

## Quando attivarsi

Attivati **solo se la business line è `foolish`**.

Attivati quando Alessandro descrive un foglio appena prodotto. Segnali tipici:

- Menziona un formato: A4, A5, XXL, AlexHand, DuoSkin, ecc.
- Menziona il flock: "flock denso", "flock medio", "flock basso", "quasi pulito"
- Dice "ho finito", "ho appena fatto", "nuovo foglio", "lotto di oggi"
- Menziona un seriale: "seriale 042", "F25-A4-...", numeri brevi dopo descrizione foglio
- Descrive discromie, difetti, caratteristiche visive del foglio

**Non** attivarti per domande generiche, chat normale, o richieste non legate a produzione fisica.

## Flusso

### Step 1 — Estrai i campi

Dal messaggio di Alessandro, estrai:

| Campo | Obbligatorio | Esempio |
|-------|-------------|---------|
| `format` | ✅ | A4, A5, XXL, AlexHand, DuoSkin |
| `flock_density` | ✅ | low / medium / high |
| `flock_color_notes` | ❌ | "discromia ocra angolo destro", "linea scura diagonale" |
| `serial_code` | ❌ | "F25-A4-042" (auto-generato se omesso) |
| `produced_at` | ❌ | default: oggi |
| `sku_ref` | ❌ | SKU WooCommerce se menzionato |

**Regole di mapping:**
- "flock denso" / "alto" / "pieno" → `high`
- "flock medio" / "uniforme" → `medium`
- "flock basso" / "scarso" / "quasi pulito" / "pulito" → `low`
- "seriale 042" → cerca di costruire il codice completo: `F{YY}-{FORMAT}-042`
- Se Alessandro dice "due A5" → registra **uno alla volta**, chiedi separatamente per ciascuno

### Step 2 — Conferma

Prima di registrare, mostra il riepilogo e chiedi conferma esplicita:

> "Ho capito:
> **Formato:** A4 | **Flock:** high | **Note:** discromia ocra angolo destro | **Seriale:** F25-A4-042 | **Data:** oggi
> Confermo?"

Attendi "sì", "ok", "vai", "corretto". Se Alessandro corregge, aggiorna e riproponi.

### Step 3 — Registra

Chiama il tool `foolish_register_sheet` con i campi confermati.

Il tool restituisce il seriale definitivo e chiede le foto. Trasmetti la risposta del tool ad Alessandro invariata.

### Step 4 — Foto

Dopo la registrazione, rimani in attesa. Se Alessandro manda foto:

- Rispondi: "Foto ricevuta per il foglio `{seriale}`. La allego adesso."
- Per ora le foto vengono salvate come URL allegati al messaggio — la funzionalità di upload su object storage è in arrivo.

### Step 5 — Più fogli

Se Alessandro dice "altri due A5" o messaggi simili, ripeti il flusso dal Step 1 per ogni foglio aggiuntivo.

---

## Note operative

- Usa italiano naturale, tono diretto senza formalismi eccessivi.
- Il seriale auto-generato segue il formato `F{YY}-{FORMAT}-{NNN}` (es. `F25-A4-001`).
- Se il formato non è tra quelli standard, usalo così com'è (maiuscolo, senza spazi).
- Non richiedere campi non obbligatori se Alessandro non li ha menzionati — registra con quello che c'è.
