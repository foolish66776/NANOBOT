"""Formatta i risultati del Council per Telegram e per il file .council.md."""

from __future__ import annotations

from nanobot.council.types import CouncilResult, WorkflowSpec


def format_telegram_summary(spec: WorkflowSpec, result: CouncilResult) -> str:
    """Messaggio breve per Telegram (orchestrator e bot business line).

    Testo < 1000 caratteri, adatto a Markdown Telegram.
    """
    avg = result.avg_score
    avg_str = f"{avg:.1f}/10" if avg is not None else "N/A"

    ok_count = len(result.available_personas)
    total = 6
    personas_str = f"{ok_count}/{total} personas"
    if result.failed_personas:
        failed = ", ".join(r.persona for r in result.failed_personas)
        personas_str += f" (non disponibili: {failed})"

    # Estrai raccomandazione operativa dalla sintesi
    rec = _extract_recommendation(result.synthesis)

    lines = [
        f"🎯 *Council completato — {spec.title}*",
        f"",
        f"📊 Voto medio: *{avg_str}* | Personas: {personas_str}",
        f"",
    ]
    if rec:
        lines.append(f"📋 Raccomandazione: *{rec}*")
        lines.append("")

    # Sintesi breve (prime 400 caratteri)
    synthesis_preview = result.synthesis[:400].strip()
    if len(result.synthesis) > 400:
        synthesis_preview += "..."
    if synthesis_preview:
        lines.append(synthesis_preview)
        lines.append("")

    lines.append(f"📁 Spec: `{spec.spec_id}` | Status aggiornato: `council-pending`")

    return "\n".join(lines)


def format_council_file(spec: WorkflowSpec, result: CouncilResult) -> str:
    """Contenuto completo del file <spec-id>.council.md."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    avg = result.avg_score
    avg_str = f"{avg:.1f}/10" if avg is not None else "N/A"

    lines = [
        f"# Council Report: {spec.title}",
        f"",
        f"Spec ID: `{spec.spec_id}`",
        f"Business line: {spec.business_line}",
        f"Data: {now}",
        f"Voto medio: {avg_str}",
        f"",
        "---",
        "",
        "## Sintesi (Giudice)",
        "",
        result.synthesis or "*Sintesi non disponibile.*",
        "",
        "---",
        "",
        "## Risposte delle singole personas",
        "",
    ]

    for resp in result.responses:
        lines.append(f"### {resp.persona.upper()}")
        lines.append("")
        if resp.ok:
            score_str = f" | Voto: {resp.score}/10" if resp.score is not None else ""
            lines.append(f"*Stato: OK{score_str}*")
            lines.append("")
            lines.append(resp.text)
        else:
            lines.append(f"*Non disponibile: {resp.error}*")
        lines.append("")
        lines.append("---")
        lines.append("")

    if result.failed_personas:
        lines.append("## Personas non disponibili")
        lines.append("")
        for r in result.failed_personas:
            lines.append(f"- **{r.persona}**: {r.error}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _extract_recommendation(synthesis: str) -> str:
    """Estrae la raccomandazione operativa dalla sintesi del giudice."""
    markers = ["Piano d'azione immediato", "Le 2 cose su cui fare all-in", "Il nucleo forte"]
    for line in synthesis.splitlines():
        stripped = line.strip("- *#").strip()
        for marker in markers:
            if stripped.upper().startswith(marker.upper()):
                return stripped[:120]
    return ""
