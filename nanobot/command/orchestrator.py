"""Comandi del bot Telegram orchestrator.

Il bot orchestrator non chatta liberamente: risponde solo a questi comandi
e manda notifiche push (da OrchestratorNotifyTool).

Comandi:
  /status          — panoramica tutte le business line
  /state <bl>      — stato dettagliato di una business line
  /specs           — lista spec attive con status
  /workflows       — workflow live in n8n
  /council-history — ultime 10 sessioni Council

Interceptor:
  Qualsiasi testo non-comando → risponde con la lista comandi disponibili.
"""

from __future__ import annotations

import os
from pathlib import Path

from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext

_WORKSPACE = Path("~/dev/nanobot-workspace").expanduser()
_BUSINESS_LINES = ["personal", "concr3tica", "studio-penale", "youtube"]
_ORCH_BL = "_orchestrator"

_HELP_TEXT = (
    "🤖 *Bot orchestrator* — solo comandi:\n\n"
    "/status — panoramica business line\n"
    "/state \\<business\\> — stato dettagliato\n"
    "/specs — spec attive\n"
    "/workflows — workflow live in n8n\n"
    "/council\\-history — ultime sessioni Council"
)


def _is_orchestrator(ctx: CommandContext) -> bool:
    return (ctx.msg.metadata or {}).get("business_line") == _ORCH_BL


def _reply(ctx: CommandContext, text: str) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=text,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "markdown"},
    )


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

async def cmd_orch_status(ctx: CommandContext) -> OutboundMessage | None:
    if not _is_orchestrator(ctx):
        return None

    orch_path = _WORKSPACE / "ORCHESTRATION.md"
    if not orch_path.exists():
        return _reply(ctx, "⚠️ ORCHESTRATION.md non trovato.")

    # Mostra le sezioni Active Business Lines + Health
    content = orch_path.read_text(encoding="utf-8")
    section = _extract_section(content, "## Active Business Lines", end_marker="## Recent events")
    health = _extract_section(content, "## Health")
    last_update = _extract_field(content, "Last update:")

    lines = [
        f"📊 *Orchestration Status*",
        f"_(aggiornato: {last_update})_\n",
        section.strip(),
        "",
        health.strip(),
    ]
    return _reply(ctx, "\n".join(lines))


# ---------------------------------------------------------------------------
# /state <business>
# ---------------------------------------------------------------------------

async def cmd_orch_state(ctx: CommandContext) -> OutboundMessage | None:
    if not _is_orchestrator(ctx):
        return None

    bl = ctx.args.strip().lower()
    if not bl:
        return _reply(ctx, "Uso: /state \\<business\\>\nEs: /state concr3tica")

    state_path = _WORKSPACE / bl / "_state.md"
    if not state_path.exists():
        known = ", ".join(_BUSINESS_LINES)
        return _reply(ctx, f"❌ Business line `{bl}` non trovata.\nDisponibili: {known}")

    content = state_path.read_text(encoding="utf-8")
    return _reply(ctx, f"📁 *State: {bl}*\n\n{content}")


# ---------------------------------------------------------------------------
# /specs
# ---------------------------------------------------------------------------

async def cmd_orch_specs(ctx: CommandContext) -> OutboundMessage | None:
    if not _is_orchestrator(ctx):
        return None

    lines = ["📋 *Spec attive*\n"]
    found = False
    for bl in _BUSINESS_LINES:
        specs_dir = _WORKSPACE / bl / "specs"
        if not specs_dir.exists():
            continue
        for spec_file in sorted(specs_dir.glob("*.md")):
            if spec_file.name.endswith(".council.md"):
                continue
            content = spec_file.read_text(encoding="utf-8")
            status = _extract_field(content, "Status:")
            title = _extract_title(content) or spec_file.stem
            lines.append(f"• `{spec_file.stem}` — {title} \\[{status}\\] _({bl})_")
            found = True

    if not found:
        lines.append("_(nessuna spec trovata)_")

    return _reply(ctx, "\n".join(lines))


# ---------------------------------------------------------------------------
# /workflows
# ---------------------------------------------------------------------------

async def cmd_orch_workflows(ctx: CommandContext) -> OutboundMessage | None:
    if not _is_orchestrator(ctx):
        return None

    n8n_base = os.environ.get("N8N_BASE_URL", "")
    n8n_key = os.environ.get("N8N_API_KEY", "")
    if not n8n_base or not n8n_key:
        return _reply(ctx, "⚠️ N8N non configurato (N8N_BASE_URL o N8N_API_KEY mancante).")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{n8n_base.rstrip('/')}/api/v1/workflows",
                headers={"X-N8N-API-KEY": n8n_key},
            )
        if resp.status_code != 200:
            return _reply(ctx, f"❌ n8n risposta: {resp.status_code}")

        data = resp.json()
        workflows = data.get("data", data) if isinstance(data, dict) else data
        active = [w for w in workflows if w.get("active")]

        if not active:
            return _reply(ctx, f"🔄 *Workflow live in n8n*\n\n_(nessun workflow attivo)_")

        lines = [f"🔄 *Workflow live in n8n* ({len(active)})\n"]
        for wf in active:
            wf_id = wf.get("id", "")
            wf_name = wf.get("name", wf_id)
            lines.append(f"• `{wf_name}` — [apri]({n8n_base.rstrip('/')}/workflow/{wf_id})")

        return _reply(ctx, "\n".join(lines))

    except Exception as exc:
        return _reply(ctx, f"❌ Errore n8n: {exc}")


# ---------------------------------------------------------------------------
# /council-history
# ---------------------------------------------------------------------------

async def cmd_orch_council_history(ctx: CommandContext) -> OutboundMessage | None:
    if not _is_orchestrator(ctx):
        return None

    lines = ["🎯 *Ultime sessioni Council*\n"]
    entries: list[tuple[str, str, str, str]] = []  # (date, spec_id, title, business)

    for bl in _BUSINESS_LINES:
        specs_dir = _WORKSPACE / bl / "specs"
        if not specs_dir.exists():
            continue
        for council_file in specs_dir.glob("*.council.md"):
            content = council_file.read_text(encoding="utf-8")
            spec_id = council_file.stem.replace(".council", "")
            title = _extract_title(content) or spec_id
            date_str = _extract_field(content, "Data:")[:10]
            entries.append((date_str, spec_id, title, bl))

    entries.sort(reverse=True)
    for date_str, spec_id, title, bl in entries[:10]:
        lines.append(f"• `{spec_id}` — {title} _{date_str}_ _({bl})_")

    if len(lines) == 1:
        lines.append("_(nessuna sessione Council trovata)_")

    return _reply(ctx, "\n".join(lines))


# ---------------------------------------------------------------------------
# Interceptor: non-command messages
# ---------------------------------------------------------------------------

async def orchestrator_fallback(ctx: CommandContext) -> OutboundMessage | None:
    """Risponde a qualsiasi messaggio non-comando sul bot orchestrator."""
    if not _is_orchestrator(ctx):
        return None
    return _reply(ctx, _HELP_TEXT)


# ---------------------------------------------------------------------------
# Helper privati
# ---------------------------------------------------------------------------

def _extract_section(content: str, start_marker: str, end_marker: str = "") -> str:
    lines = content.splitlines()
    in_section = False
    result: list[str] = []
    for line in lines:
        if line.strip().startswith(start_marker.strip()):
            in_section = True
            result.append(line)
            continue
        if in_section:
            if end_marker and line.strip().startswith(end_marker.strip()):
                break
            if not end_marker and line.startswith("## ") and line.strip() != start_marker.strip():
                break
            result.append(line)
    return "\n".join(result)


def _extract_field(content: str, field_name: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(field_name):
            return stripped[len(field_name):].strip()
    return ""


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            # Rimuovi prefissi come "Council Report: " o "Workflow Spec: "
            title = stripped[2:]
            for prefix in ("Council Report: ", "Workflow Spec: ", "# "):
                if title.startswith(prefix):
                    title = title[len(prefix):]
            return title.strip()
    return ""
