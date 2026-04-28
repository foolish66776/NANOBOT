#!/bin/bash
# Sync ~/.nanobot/config.json → NANOBOT_CONFIG_JSON su Railway (nanobot-gateway).
# Usage: ./scripts/sync-config-railway.sh [--dry-run]

set -e

CONFIG_FILE="$HOME/.nanobot/config.json"
RAILWAY_PROJECT="ab6a50e8-3417-4bca-94f6-76e9b1e7dc4f"
RAILWAY_ENV="8318ae2a-a668-45a5-9464-8442c2005ba2"
RAILWAY_SERVICE="33d98df0-354e-45a8-ba59-6514b009336f"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Validate JSON
if ! python3 -c "import json,sys; json.load(open('$CONFIG_FILE'))" 2>/dev/null; then
    echo "❌ $CONFIG_FILE non è JSON valido. Abort." >&2
    exit 1
fi

# Minify + stats
CONFIG_JSON=$(python3 -c "import json,sys; print(json.dumps(json.load(open('$CONFIG_FILE')), ensure_ascii=False))")
CHARS=${#CONFIG_JSON}
BLS=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(', '.join(d.get('businessLines',{}).keys()))")
BOTS=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(', '.join(b['businessLine'] for b in d.get('channels',{}).get('telegram',{}).get('bots',[])))")

echo "📋 Config: $CONFIG_FILE ($CHARS chars)"
echo "   businessLines: $BLS"
echo "   telegram bots: $BOTS"
echo ""

if $DRY_RUN; then
    echo "🔍 Dry-run — nessuna modifica effettuata."
    exit 0
fi

# Link to nanobot-gateway and set variable
cd "$(dirname "$0")/.."
railway link \
    --project "$RAILWAY_PROJECT" \
    --environment "$RAILWAY_ENV" \
    --service "$RAILWAY_SERVICE" 2>/dev/null

railway variable set "NANOBOT_CONFIG_JSON=$CONFIG_JSON"
echo "✅ NANOBOT_CONFIG_JSON aggiornato su Railway."
echo ""
echo "⚠️  Ricorda: Railway fa redeploy automatico. Il gateway si riavvierà tra ~30 secondi."
