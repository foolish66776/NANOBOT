#!/usr/bin/env python3
"""
LocalAI Watchdog — cervello operativo locale sempre attivo.
Classifica email, ordini, tracking con LLM locale (llama-3.2-3b).
Scrive journal su Obsidian. Notifica Telegram solo su URGENTE.
Costa: 0 token DeepSeek.

Uso:  python3 local-ai-watchdog.py
      (gira via cron ogni 5 minuti)
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────────
OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = os.environ.get("WATCHDOG_MODEL", "hf.co/deadbydawn101/gemma-4-E4B-Agentic-Opus-Reasoning-GeminiCLI-GGUF:latest")
MODEL_BACKUP = "tinyllama:latest"

JOURNAL_DIR = Path.home() / "Obsidian" / "_nanobot" / "local-ai-journal"
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

# Token Telegram per notifiche URGENTI (letti da frank.env, mai hardcoded)
TELEGRAM_BOT_TOKEN = os.environ.get("FRANK_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("FRANK_TELEGRAM_CHAT_ID", "")

# ── Classification Prompt ──────────────────────────────────
CLASSIFY_PROMPT = (
    "Classifica il seguente messaggio email in una sola parola: "
    "URGENTE, ORDINE, o SPAM.\n\n"
    "URGENTE = reclami di clienti arrabbiati, pacchi persi o mai arrivati, "
    "problemi di pagamento, richieste di rimborso, minacce legali.\n"
    "ORDINE = nuovi ordini, conferme d'ordine, aggiornamenti tracking, "
    "notifiche di spedizione, domande normali sui prodotti.\n"
    "SPAM = pubblicità non richiesta, phishing, catene, offerte sospette.\n\n"
    "Rispondi con UNA sola parola (URGENTE, ORDINE, o SPAM)."
)


# ── Core Functions ─────────────────────────────────────────
def call_llm(messages: list[dict], model: str = None, max_tokens: int = 15) -> dict:
    """Call LocalAI chat completions. Returns the parsed JSON response."""
    if model is None:
        model = MODEL

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        # Try backup model
        if model != MODEL_BACKUP:
            print(f"  ⚠ {model} failed ({e}), trying {MODEL_BACKUP}...")
            return call_llm(messages, model=MODEL_BACKUP, max_tokens=max_tokens)
        raise


def classify(text: str) -> tuple[str, int]:
    """Classify a message. Returns (category, tokens_used)."""
    messages = [
        {"role": "system", "content": CLASSIFY_PROMPT},
        {"role": "user", "content": text[:1000]},
    ]

    data = call_llm(messages)
    content = data["choices"][0]["message"]["content"].strip().upper()
    tokens = data["usage"]["total_tokens"]

    # Normalize
    content = content.replace('"', "").replace("'", "").replace(".", "").strip()
    if "URGENTE" in content:
        return "URGENTE", tokens
    elif "ORDINE" in content:
        return "ORDINE", tokens
    else:
        return "SPAM", tokens


# ── Journal ─────────────────────────────────────────────────
def write_journal(entry: dict):
    """Append an entry to today's journal JSON."""
    tz = timezone(timedelta(hours=2))
    today = datetime.now(tz).strftime("%Y-%m-%d")
    journal_file = JOURNAL_DIR / f"{today}.json"

    entries = []
    if journal_file.exists():
        try:
            entries = json.loads(journal_file.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            entries = []

    entries.append(entry)
    journal_file.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    )


# ── Telegram ────────────────────────────────────────────────
def notify_telegram(message: str):
    """Send a Telegram notification (only for URGENT items)."""
    try:
        url = (
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )
        payload = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=payload),
            timeout=10,
        )
    except Exception as e:
        print(f"  [ERROR] Telegram notify failed: {e}")


# ── Input Sources ───────────────────────────────────────────
# TODO: Connect to real data sources
# - AgentMail polling (agentmail_list)
# - CMS new orders check
# - Packlink tracking changes

def get_pending_items() -> list[dict]:
    """
    Poll for items to classify.
    Replace stubs with real API calls.
    Returns list of {source, subject, sender, body}.
    """
    items = []

    # --- STUB: AgentMail support inbox ---
    # items.extend(fetch_agentmail_unread("foolish", "support"))

    # --- STUB: CMS new orders ---
    # items.extend(fetch_cms_new_orders())

    return items


# ── Main ────────────────────────────────────────────────────
def main():
    tz = timezone(timedelta(hours=2))
    now = datetime.now(tz)
    print(f"[{now.isoformat()}] LocalAI Watchdog ({MODEL} via Ollama)")

    items = get_pending_items()

    if not items:
        print("  (nessun item da processare)")
        return

    for item in items:
        text = (
            f"Oggetto: {item.get('subject', '')}\n"
            f"Mittente: {item.get('sender', '')}\n"
            f"{item.get('body', '')[:500]}"
        )

        try:
            category, tokens = classify(text)
        except Exception as e:
            print(f"  ✗ Classification error: {e}")
            continue

        entry = {
            "timestamp": now.isoformat(),
            "source": item.get("source", "unknown"),
            "subject": item.get("subject", ""),
            "sender": item.get("sender", ""),
            "category": category,
            "tokens": tokens,
        }
        write_journal(entry)

        if category == "URGENTE":
            msg = (
                f"⚠️ <b>URGENTE — LocalAI Watchdog</b>\n"
                f"📬 {item.get('subject', 'No subject')}\n"
                f"👤 {item.get('sender', 'Unknown')}\n"
                f"🕐 {entry['timestamp']}"
            )
            notify_telegram(msg)
            print(f"  ⚠️  {category} → Telegram sent")
        else:
            print(f"  ✓  {category:8s} | {item.get('subject', '')[:60]}")


if __name__ == "__main__":
    main()
