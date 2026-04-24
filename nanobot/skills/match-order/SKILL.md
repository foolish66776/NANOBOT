---
name: match-order
description: Avvia il matching fogli→ordine per The Foolish Butcher. Solo per business line foolish.
always: true
---

# Skill: match-order

## Quando attivarsi

Attivati **solo se la business line è `foolish`**.

Attivati quando Alessandro vuole abbinare i fogli prodotti a un ordine specifico:

- "matcha ordine 9001"
- "alloca i fogli per l'ordine 9001"
- "fai il matching dell'ordine X"
- "abbina i fogli all'ordine"
- "ordine X è pronto per la spedizione"
- "ho i fogli pronti per X"

## Flusso

### Step 1 — Identifica l'ordine

Estrai l'order_id dal messaggio. Se non è chiaro, chiedi: "Per quale ordine vuoi fare il matching?"

### Step 2 — Verifica fogli disponibili (opzionale)

Se Alessandro non sa se ci sono fogli disponibili, puoi prima chiamare `foolish_query_sheets` con `status=in_stock` per mostrargli cosa c'è in magazzino.

### Step 3 — Proponi il matching

Chiama `foolish_propose_matching` con l'order_id. Il tool:
- Cerca i fogli in_stock compatibili con il formato degli articoli dell'ordine
- Manda la proposta ad Alessandro su Telegram con bottoni Approva/Rifiuta
- Restituisce un messaggio di conferma

Trasmetti il risultato del tool ad Alessandro.

### Step 4 — Attendi risposta

Alessandro risponde con i bottoni inline direttamente sul messaggio Telegram. Non occorre fare altro — il sistema gestisce automaticamente l'approvazione o il rifiuto.

Se Alessandro chiede di modificare la selezione manualmente, spiegagli che per ora la selezione è automatica per formato — in una fase futura potrà selezionare fogli specifici.

---

## Note operative

- Il matching è basato sul formato degli articoli nell'ordine (A4, A5, XXL, ecc.)
- Se i fogli non sono sufficienti, il sistema segnala quali formati mancano
- Dopo l'approvazione, il sistema compone automaticamente la bozza di preview pre-spedizione e la invia ad Alessandro per approvazione
