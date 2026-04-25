"""LLM classifier — classifica ogni input di Alessandro in un tipo strutturato."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from loguru import logger

from .models import ClassificationResult

if TYPE_CHECKING:
    pass

_SYSTEM_PROMPT = """Sei un classificatore di input per la knowledge base personale di Alessandro Boscarato.

Il tuo unico compito è classificare l'input in uno di questi tipi:
- reflection   → riflessione personale, opinione, punto di vista, pensiero di Alessandro
- source       → link a un articolo, PDF, newsletter, video o testo di fonte esterna
- business_idea → idea di business, opportunità di mercato, progetto da esplorare
- query        → domanda che richiede una risposta attingendo alla knowledge base
- command      → comando esplicito (/wiki /lint /synthesis /content /search)

Rispondi SOLO con un JSON valido, nessun testo extra:
{
  "type": "<tipo>",
  "confidence": <float 0.0-1.0>,
  "extracted_url": "<url o null>",
  "command": "<nome comando senza slash o null>",
  "raw_text": "<testo input normalizzato>"
}

Regole:
- Se l'input inizia con / → type=command, command=nome del comando
- Se contiene un URL e parla di una fonte esterna → type=source, extracted_url=<url>
- Se Alessandro esprime un'opinione, fa una riflessione o nota personale → type=reflection
- Se descrive un'idea di business o opportunità → type=business_idea
- Se fa una domanda ("come la penso su X?", "dimmi...", "cosa ne penso di...") → type=query
- Se ambiguo → scegli il tipo più probabile ma abbassa confidence sotto 0.7
"""

_COMMAND_MAP = {
    "/wiki": "stats",
    "/lint": "lint",
    "/synthesis": "synthesis",
    "/content": "content",
    "/search": "search",
}


def _quick_classify(text: str) -> ClassificationResult | None:
    """Fast path for unambiguous cases (no LLM needed)."""
    stripped = text.strip()

    # Command
    for cmd, name in _COMMAND_MAP.items():
        if stripped.lower().startswith(cmd):
            return ClassificationResult(
                type="command",
                confidence=1.0,
                command=name,
                raw_text=stripped,
            )

    # URL-only input
    url_match = re.match(r"^https?://\S+$", stripped)
    if url_match:
        return ClassificationResult(
            type="source",
            confidence=0.95,
            extracted_url=stripped,
            raw_text=stripped,
        )

    return None


async def classify(text: str, provider=None) -> ClassificationResult:
    """Classify input. Uses quick path when unambiguous, LLM otherwise."""
    quick = _quick_classify(text)
    if quick:
        return quick

    if provider is None:
        logger.warning("No LLM provider for wiki classifier — defaulting to reflection")
        return ClassificationResult(
            type="reflection",
            confidence=0.5,
            raw_text=text,
        )

    try:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        response = await provider.complete(messages=messages, max_tokens=256, temperature=0.0)
        raw_json = _extract_json(response)
        data = json.loads(raw_json)
        result = ClassificationResult(
            type=data["type"],
            confidence=float(data.get("confidence", 0.8)),
            extracted_url=data.get("extracted_url") or None,
            command=data.get("command") or None,
            raw_text=data.get("raw_text", text),
        )
        logger.debug("Classifier: type={} confidence={:.2f} input={}...",
                     result.type, result.confidence, text[:60])
        return result
    except Exception as exc:
        logger.warning("Classifier LLM failed ({}), defaulting to reflection", exc)
        return ClassificationResult(type="reflection", confidence=0.4, raw_text=text)


def _extract_json(text: str) -> str:
    """Extract JSON block from LLM response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return m.group(0)
    return text
