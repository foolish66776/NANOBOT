"""Tipi di dati del Council."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowSpec:
    """Rappresenta una workflow-spec.md già parsata."""

    spec_id: str
    """ID della spec (es. 2026-04-16-morning-brief)."""

    title: str
    """Titolo leggibile."""

    business_line: str
    """Business line (es. concr3tica)."""

    status: str
    """Status corrente (es. draft)."""

    raw_content: str
    """Contenuto completo del file .md."""

    path: str
    """Path assoluto del file."""

    @classmethod
    def from_file(cls, path: str) -> "WorkflowSpec":
        """Legge e parsa un file workflow-spec.md."""
        from pathlib import Path

        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Spec non trovata: {path}")

        content = p.read_text(encoding="utf-8")
        spec_id = _extract_field(content, "ID:") or p.stem
        title = _extract_title(content) or spec_id
        business_line = _extract_field(content, "Business line:") or "unknown"
        status = _extract_field(content, "Status:") or "draft"

        return cls(
            spec_id=spec_id,
            title=title,
            business_line=business_line,
            status=status,
            raw_content=content,
            path=str(p),
        )


@dataclass
class PersonaResponse:
    """Risposta di una singola persona del Council."""

    persona: str
    """Nome della persona (es. voce-cliente)."""

    text: str
    """Testo della risposta."""

    ok: bool = True
    """False se la chiamata è fallita."""

    error: Optional[str] = None
    """Messaggio di errore se ok=False."""

    score: Optional[float] = None
    """Voto estratto dalla risposta (1-10), se presente."""


@dataclass
class CouncilResult:
    """Risultato completo di una sessione Council."""

    spec_id: str
    responses: list[PersonaResponse] = field(default_factory=list)
    synthesis: str = ""
    """Testo della sintesi del giudice."""

    @property
    def available_personas(self) -> list[PersonaResponse]:
        return [r for r in self.responses if r.ok]

    @property
    def failed_personas(self) -> list[PersonaResponse]:
        return [r for r in self.responses if not r.ok]

    @property
    def avg_score(self) -> Optional[float]:
        scores = [r.score for r in self.available_personas if r.score is not None]
        return sum(scores) / len(scores) if scores else None


# ---------------------------------------------------------------------------
# Helper per parsing minimo del markdown spec
# ---------------------------------------------------------------------------

def _extract_field(content: str, field_name: str) -> str:
    """Estrae il valore di un campo 'Field: valore' dal markdown."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(field_name):
            value = stripped[len(field_name):].strip()
            if value:
                return value
    return ""


def _extract_title(content: str) -> str:
    """Estrae il titolo dall'intestazione '# Workflow Spec: <titolo>'."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Workflow Spec:"):
            return stripped[len("# Workflow Spec:"):].strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""
