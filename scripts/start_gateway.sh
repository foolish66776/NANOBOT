#!/bin/bash
set -e

# ── 1. config.json ──────────────────────────────────────────────────────────
mkdir -p ~/.nanobot

if [ -z "$NANOBOT_CONFIG_JSON" ]; then
    echo "ERROR: NANOBOT_CONFIG_JSON env var not set" >&2
    exit 1
fi
echo "$NANOBOT_CONFIG_JSON" > ~/.nanobot/config.json
echo "✓ config.json written"

# ── 2. workspace (Railway Volume o fallback locale) ──────────────────────────
WORKSPACE_DIR="${NANOBOT_WORKSPACE_DIR:-/data/workspace}"
mkdir -p "$WORKSPACE_DIR"
mkdir -p ~/dev
ln -sfn "$WORKSPACE_DIR" ~/dev/nanobot-workspace
echo "✓ Workspace → $WORKSPACE_DIR"

# Clona nanobot-workspace se volume vuoto e credenziali disponibili
if [ -z "$(ls -A "$WORKSPACE_DIR" 2>/dev/null)" ]; then
    if [ -n "$GITHUB_TOKEN" ] && [ -n "$NANOBOT_WORKSPACE_REPO" ]; then
        echo "Volume vuoto — clono workspace da GitHub..."
        git clone --depth=1 \
            "https://$GITHUB_TOKEN@github.com/$NANOBOT_WORKSPACE_REPO" \
            "$WORKSPACE_DIR" || echo "⚠ Clone fallito, parto con workspace vuoto"
    else
        echo "Volume vuoto, nessun repo configurato — workspace inizializzato vuoto"
    fi
fi

# ── 3. Avvia gateway ────────────────────────────────────────────────────────
echo "✓ Avvio nanobot gateway..."
exec nanobot gateway --workspace "$WORKSPACE_DIR"
