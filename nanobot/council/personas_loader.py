"""Carica i prompt delle personas Council e le personas cliente della business line."""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

# Tutte le personas nell'ordine in cui girano nel Council
PERSONA_NAMES = [
    "voce-cliente",
    "vc-unicorni",
    "bartlett",
    "visionario",
    "jobs",
    "munger",
]

_DEFAULT_PERSONAS_DIR = Path("~/.nanobot/council-personas").expanduser()
_DEFAULT_WORKSPACE = Path("~/dev/nanobot-workspace").expanduser()


def load_persona_prompts(
    personas_dir: Path | None = None,
) -> dict[str, str]:
    """Carica i prompt delle 6 personas dal directory council-personas.

    Returns:
        Dict {persona_name: system_prompt_text}
    """
    base = personas_dir or _DEFAULT_PERSONAS_DIR
    result: dict[str, str] = {}

    for name in PERSONA_NAMES:
        path = base / f"{name}.md"
        if path.exists():
            result[name] = path.read_text(encoding="utf-8")
        else:
            logger.warning("Prompt persona non trovato: {}", path)
            result[name] = f"# Council Persona: {name}\n\nPrompt non configurato."

    return result


def load_judge_prompt(personas_dir: Path | None = None) -> str:
    """Carica il prompt del giudice."""
    base = personas_dir or _DEFAULT_PERSONAS_DIR
    path = base / "giudice.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Prompt giudice non trovato: {}", path)
    return "# Council Judge\n\nPrompt non configurato."


def load_business_personas(
    business_line: str,
    workspace: Path | None = None,
) -> str:
    """Carica il contenuto di _personas.md per la business line.

    Returns:
        Testo del file _personas.md, o stringa vuota se non presente.
    """
    ws = workspace or _DEFAULT_WORKSPACE
    path = ws / business_line / "_personas.md"
    if path.exists():
        content = path.read_text(encoding="utf-8")
        # Se è ancora il template vuoto, avvisiamo ma lo includiamo comunque
        if "TODO" in content:
            logger.warning(
                "_personas.md per '{}' non è ancora compilato. "
                "Il Council userà un cliente generico.",
                business_line,
            )
        return content
    logger.warning("_personas.md non trovato per business line '{}'", business_line)
    return ""


def inject_customer_personas(
    voce_cliente_prompt: str,
    customer_personas: str,
) -> str:
    """Inietta le customer personas nel prompt voce-cliente.

    Se le personas sono presenti, le aggiunge come contesto identitario.
    """
    if not customer_personas or "TODO" in customer_personas:
        # Nessuna persona reale: usa un generico
        injection = (
            "\n\n## Il tuo profilo\n\n"
            "Il _personas.md di questa business line non è ancora compilato. "
            "Interpreta un cliente generico plausibile per questo tipo di business, "
            "basandoti sulla spec che leggi. Sii realistico, non generico."
        )
    else:
        injection = f"\n\n## Il tuo profilo (dal _personas.md)\n\n{customer_personas}"

    return voce_cliente_prompt + injection
