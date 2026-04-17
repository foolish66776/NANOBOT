"""Supervisor checkpoints Claude per il pipeline ship-workflow.

Tre checkpoint:
  validate_spec    — verifica spec prima del build
  review_workflow  — verifica JSON workflow dopo il build
  weekly_audit     — report settimanale salute workflow

Ogni checkpoint legge il proprio prompt da ~/.nanobot/supervisor-prompts/.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from loguru import logger

from nanobot.llm.router import LLMRouter

_PROMPTS_DIR = Path("~/.nanobot/supervisor-prompts").expanduser()

Verdict = Literal["APPROVABILE", "APPROVABILE CON MODIFICHE MINORI", "STOP", "DA RIFARE",
                  "COMPLETA", "QUASI COMPLETA", "INCOMPLETA"]


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt supervisor non trovato: {}", path)
    return f"# Supervisor: {name}\n\nPrompt non configurato."


def _extract_verdict(report: str | None, valid_verdicts: list[str]) -> str:
    """Estrae il verdetto finale dal report del supervisor."""
    if not report:
        return "STOP"
    for line in report.splitlines():
        stripped = line.strip("# *-").strip()
        for v in valid_verdicts:
            if stripped.upper().startswith(v.upper()):
                return v
    return "STOP"  # safe default


# ---------------------------------------------------------------------------
# Checkpoint 1 — validate-spec
# ---------------------------------------------------------------------------

async def validate_spec(
    spec_content: str,
    router: LLMRouter | None = None,
) -> tuple[str, Verdict]:
    """Checklist tecnica sulla spec — NON legge council.md.

    Returns:
        (report_text, verdict)
        verdict: COMPLETA | QUASI COMPLETA | INCOMPLETA
    """
    r = router or LLMRouter()
    system = _load_prompt("validate-spec")
    user = f"# Spec da verificare\n\n{spec_content}"

    report = await r.complete(role="validate_spec", system=system, user=user, max_tokens=1000)

    verdict = _extract_verdict(
        report,
        ["QUASI COMPLETA", "COMPLETA", "INCOMPLETA"],
    )
    logger.info("validate-spec verdetto: {}", verdict)
    return report, verdict  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Checkpoint 2 — review-workflow
# ---------------------------------------------------------------------------

async def review_workflow(
    spec_content: str,
    workflow_json_str: str,
    router: LLMRouter | None = None,
) -> tuple[str, Verdict]:
    """Chiama Claude per verificare il workflow JSON generato contro la spec.

    Returns:
        (report_text, verdict)
        verdict: APPROVABILE | DA RIFARE | STOP
    """
    r = router or LLMRouter()
    system = _load_prompt("review-workflow")

    user = (
        f"# Spec approvata\n\n{spec_content}"
        f"\n\n---\n\n# Workflow JSON generato\n\n```json\n{workflow_json_str}\n```"
        f"\n\nVerifica la conformità seguendo le istruzioni del tuo sistema."
    )

    report = await r.complete(role="review_workflow", system=system, user=user, max_tokens=4000)

    verdict = _extract_verdict(report, ["APPROVABILE", "DA RIFARE", "STOP"])
    logger.info("review-workflow verdetto: {}", verdict)
    return report, verdict  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Checkpoint 3 — weekly-audit
# ---------------------------------------------------------------------------

async def weekly_audit(
    workflows_summary: str,
    router: LLMRouter | None = None,
) -> str:
    """Chiama Claude per il report settimanale di salute.

    Args:
        workflows_summary: Testo con lista workflow + statistiche esecuzioni.

    Returns:
        Testo del report.
    """
    r = router or LLMRouter()
    system = _load_prompt("weekly-audit")
    user = (
        "Analizza la salute dei workflow attivi e produci il report settimanale.\n\n"
        f"# Dati workflow\n\n{workflows_summary}"
    )
    return await r.complete(role="weekly_audit", system=system, user=user, max_tokens=4000)
