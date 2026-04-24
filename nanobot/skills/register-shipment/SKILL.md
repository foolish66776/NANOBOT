---
name: register-shipment
description: Registra la spedizione di un ordine Foolish Butcher con tracking number e corriere. Solo per business line foolish.
always: true
---

# Skill: register-shipment

## Quando attivarsi

Attivati **solo se la business line è `foolish`**.

Attivati quando Alessandro communica che ha spedito un ordine:

- "ho spedito l'ordine 9001"
- "spedito! tracking GLS 1234567890, ordine 9001"
- "ordine X spedito con BRT, tracking Y"
- "partito il pacco per ordine X"
- "ho consegnato al corriere l'ordine X"

## Flusso

### Step 1 — Estrai i dati

Dal messaggio estrai:

| Campo | Obbligatorio | Esempio |
|-------|-------------|---------|
| `order_id` | ✅ | 9001 |
| `tracking_number` | ✅ | 1234567890, GR123456789IT |
| `carrier` | ✅ | gls, brt, sda, poste, dhl, ups, fedex, packlink |

Se manca il corriere, chiedi: "Con quale corriere?"
Se manca il tracking, chiedi: "Qual è il numero di tracking?"

### Step 2 — Conferma

Mostra il riepilogo prima di registrare:

> "Registro spedizione ordine #9001 — tracking 1234567890 via GLS. Confermo?"

### Step 3 — Registra

Chiama `foolish_register_shipment` con i dati confermati. Il tool:
- Salva tracking e corriere in DB
- Marca i fogli come spediti
- Invia il messaggio di tracking al cliente su Telegram (se collegato)

Trasmetti la risposta del tool ad Alessandro.

---

## Note operative

- I corrieri supportati con link diretto: gls, brt, sda, poste, dhl, ups, fedex, packlink
- Per altri corrieri il link va su parcelsapp.com (aggregatore universale)
- Il messaggio al cliente è auto-inviato senza approvazione (stage tracking, low-variance)
