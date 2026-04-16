"""Council runner — esegue 6 personas in parallelo con timeout 90s."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from loguru import logger

from nanobot.council.personas_loader import (
    PERSONA_NAMES,
    inject_customer_personas,
    load_business_personas,
    load_judge_prompt,
    load_persona_prompts,
)
from nanobot.council.types import CouncilResult, PersonaResponse, WorkflowSpec
from nanobot.llm.router import LLMRouter

# Timeout totale per il Council (secondi)
_COUNCIL_TIMEOUT = 90.0

# Timeout per singola chiamata persona
_PERSONA_TIMEOUT = 75.0


async def run_council(
    spec: WorkflowSpec,
    *,
    router: LLMRouter | None = None,
    personas_dir: Path | None = None,
    workspace: Path | None = None,
) -> CouncilResult:
    """Esegue il Council completo su una spec.

    - 6 personas in parallelo (asyncio.gather)
    - Timeout totale 90s
    - Fallimento di una persona non blocca le altre
    - Poi chiama il giudice con tutti i risultati

    Args:
        spec: La spec da valutare.
        router: LLMRouter (default: nuovo LLMRouter())
        personas_dir: Override per il directory personas
        workspace: Override per il workspace

    Returns:
        CouncilResult con tutte le risposte e la sintesi del giudice.
    """
    r = router or LLMRouter()

    persona_prompts = load_persona_prompts(personas_dir)
    customer_personas = load_business_personas(spec.business_line, workspace)
    judge_prompt = load_judge_prompt(personas_dir)

    # Inietta customer personas in voce-cliente
    persona_prompts["voce-cliente"] = inject_customer_personas(
        persona_prompts["voce-cliente"], customer_personas
    )

    user_message = _build_spec_message(spec)
    result = CouncilResult(spec_id=spec.spec_id)

    t0 = time.monotonic()
    logger.info("Council avviato per spec '{}' (business: {})", spec.spec_id, spec.business_line)

    # -----------------------------------------------------------------------
    # Fase 1 — 6 personas in parallelo con timeout globale
    # -----------------------------------------------------------------------
    async def call_persona(name: str) -> PersonaResponse:
        system = persona_prompts.get(name, "")
        try:
            text = await asyncio.wait_for(
                r.complete(
                    role="council_persona",
                    persona=name,
                    system=system,
                    user=user_message,
                    max_tokens=1500,
                ),
                timeout=_PERSONA_TIMEOUT,
            )
            score = _extract_score(text)
            logger.info("Persona '{}' completata in {:.1f}s (score: {})", name, time.monotonic() - t0, score)
            return PersonaResponse(persona=name, text=text, ok=True, score=score)
        except asyncio.TimeoutError:
            logger.warning("Persona '{}' timeout dopo {:.1f}s", name, _PERSONA_TIMEOUT)
            return PersonaResponse(
                persona=name, text="", ok=False,
                error=f"Timeout dopo {_PERSONA_TIMEOUT:.0f}s"
            )
        except Exception as exc:
            logger.warning("Persona '{}' errore: {}", name, exc)
            return PersonaResponse(
                persona=name, text="", ok=False, error=str(exc)
            )

    try:
        persona_tasks = [call_persona(name) for name in PERSONA_NAMES]
        responses = await asyncio.wait_for(
            asyncio.gather(*persona_tasks),
            timeout=_COUNCIL_TIMEOUT,
        )
        result.responses = list(responses)
    except asyncio.TimeoutError:
        # Timeout globale: usiamo quello che abbiamo
        logger.warning("Council timeout globale ({}s). Proseguo con le personas disponibili.", _COUNCIL_TIMEOUT)
        # gather con return_exceptions per raccogliere i risultati parziali
        partial = await asyncio.gather(*persona_tasks, return_exceptions=True)
        result.responses = [
            r if isinstance(r, PersonaResponse)
            else PersonaResponse(persona=PERSONA_NAMES[i], text="", ok=False, error="Timeout globale Council")
            for i, r in enumerate(partial)
        ]

    elapsed = time.monotonic() - t0
    ok_count = len(result.available_personas)
    logger.info(
        "Council personas completate in {:.1f}s ({}/{} disponibili)",
        elapsed, ok_count, len(PERSONA_NAMES)
    )

    # -----------------------------------------------------------------------
    # Fase 2 — Giudice
    # -----------------------------------------------------------------------
    remaining = max(0.0, _COUNCIL_TIMEOUT - elapsed)
    if remaining < 5.0:
        logger.warning("Tempo insufficiente per il giudice ({:.1f}s rimanenti). Council incompleto.", remaining)
        result.synthesis = _fallback_synthesis(result)
    else:
        judge_user = _build_judge_message(spec, result)
        try:
            synthesis = await asyncio.wait_for(
                r.complete(
                    role="council_judge",
                    system=judge_prompt,
                    user=judge_user,
                    max_tokens=2000,
                ),
                timeout=min(remaining, 60.0),
            )
            result.synthesis = synthesis
            logger.info("Giudice completato in {:.1f}s", time.monotonic() - t0)
        except asyncio.TimeoutError:
            logger.warning("Giudice timeout.")
            result.synthesis = _fallback_synthesis(result)
        except Exception as exc:
            logger.warning("Giudice errore: {}", exc)
            result.synthesis = _fallback_synthesis(result)

    return result


# ---------------------------------------------------------------------------
# Helper privati
# ---------------------------------------------------------------------------

def _build_spec_message(spec: WorkflowSpec) -> str:
    return (
        f"Valuta la seguente proposta di workflow:\n\n"
        f"---\n{spec.raw_content}\n---\n\n"
        f"Rispondi seguendo le istruzioni del tuo sistema."
    )


def _build_judge_message(spec: WorkflowSpec, result: CouncilResult) -> str:
    parts = [
        f"# Proposta: {spec.title}\n",
        f"**Business line:** {spec.business_line}\n",
        f"---\n{spec.raw_content}\n---\n",
        "\n# Valutazioni delle 6 personas\n",
    ]
    for resp in result.responses:
        if resp.ok:
            parts.append(f"\n## {resp.persona.upper()}\n{resp.text}\n")
        else:
            parts.append(f"\n## {resp.persona.upper()}\n*Non disponibile: {resp.error}*\n")

    parts.append(
        "\nProduce la sintesi finale seguendo le istruzioni del tuo sistema."
    )
    return "\n".join(parts)


def _extract_score(text: str) -> float | None:
    """Estrae un voto numerico 1-10 dalla risposta di una persona."""
    patterns = [
        r"voto[:\s]+(\d+(?:\.\d+)?)\s*/\s*10",
        r"(\d+(?:\.\d+)?)\s*/\s*10",
        r"voto[:\s]+(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                score = float(match.group(1))
                if 1 <= score <= 10:
                    return score
            except ValueError:
                continue
    return None


def _fallback_synthesis(result: CouncilResult) -> str:
    """Sintesi di emergenza quando il giudice non è disponibile."""
    lines = [
        "## Sintesi Council — (sintetizzatore non disponibile)\n",
        f"**Personas disponibili:** {len(result.available_personas)}/{len(PERSONA_NAMES)}\n",
    ]
    if result.failed_personas:
        failed_names = ", ".join(r.persona for r in result.failed_personas)
        lines.append(f"**Personas non disponibili:** {failed_names}\n")

    avg = result.avg_score
    if avg is not None:
        lines.append(f"**Voto medio (parziale):** {avg:.1f}/10\n")

    lines.append("\n*Il giudice non ha potuto completare la sintesi (timeout o errore). Leggi le risposte delle singole personas nel file .council.md.*")
    return "\n".join(lines)
