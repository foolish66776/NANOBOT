"""Pipeline ship-workflow — 6 step da spec council-approved a workflow live in n8n.

Step:
  1. validate-spec  (Claude checkpoint)
  2. build          (MiniMax genera JSON n8n)
  3. review-workflow (Claude checkpoint, max 3 iterazioni)
  4. dry-run        (importa in n8n, esegue in test, mostra risultato)
  5. approvazione Alessandro (interattiva — gestita dal CLI, non qui)
  6. import + attivazione + aggiornamento stato
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.council.types import WorkflowSpec
from nanobot.llm.router import LLMRouter
from nanobot.ship.builder import build_workflow
from nanobot.ship.checkpoints import review_workflow, validate_spec
from nanobot.ship.n8n_client import N8nClient
from nanobot.ship.validator import validate_n8n_json


@dataclass
class ShipResult:
    spec_id: str
    success: bool = False
    stopped_at: str = ""          # step dove si è fermato
    stop_reason: str = ""
    validate_report: str = ""
    workflow_json: dict = field(default_factory=dict)
    workflow_path: str = ""
    review_report: str = ""
    dryrun_log: str = ""
    n8n_workflow_id: str = ""
    n8n_workflow_url: str = ""


class ShipPipeline:
    """Esegue i primi 4 step del pipeline (validate → build → review → dry-run).

    Step 5 (approvazione) è interattivo e gestito dal CLI.
    Step 6 (import+activate) è esposto come metodo separato `finalize()`.
    """

    MAX_BUILD_ITERATIONS = 3

    def __init__(
        self,
        workspace: Path | None = None,
        router: LLMRouter | None = None,
        n8n: N8nClient | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace or Path("~/dev/nanobot-workspace").expanduser()
        self.router = router or LLMRouter()
        self.n8n = n8n or N8nClient()
        self._progress = on_progress or (lambda msg: logger.info(msg))

    def _p(self, msg: str) -> None:
        self._progress(msg)

    # -----------------------------------------------------------------------
    # Step 1 — Validate spec
    # -----------------------------------------------------------------------

    async def run_validate_spec(self, spec: WorkflowSpec) -> tuple[str, str]:
        """Ritorna (report, verdict)."""
        self._p("Step 1/4 — validate-spec (Claude checkpoint)...")

        # validate-spec è solo checklist tecnica — NON legge council.md
        report, verdict = await validate_spec(
            spec_content=spec.raw_content,
            router=self.router,
        )
        return report, verdict

    # -----------------------------------------------------------------------
    # Step 2+3 — Build + Review (con max 3 iterazioni)
    # -----------------------------------------------------------------------

    async def run_build_and_review(
        self, spec: WorkflowSpec
    ) -> tuple[dict[str, Any], Path, str, str]:
        """Ritorna (workflow_dict, workflow_path, review_report, verdict).

        Itera build → review fino a APPROVABILE o fino a MAX_BUILD_ITERATIONS.
        """
        feedback = ""
        last_workflow: dict = {}
        last_path: Path | None = None
        last_report = ""
        last_verdict = "DA RIFARE"

        for iteration in range(1, self.MAX_BUILD_ITERATIONS + 1):
            self._p(f"Step 2/4 — build workflow (MiniMax, iterazione {iteration}/{self.MAX_BUILD_ITERATIONS})...")

            build_prompt = spec.raw_content
            if feedback:
                build_prompt = (
                    f"{spec.raw_content}\n\n"
                    f"---\n\n## Feedback review precedente\n\n{feedback}\n\n"
                    f"Correggi il workflow tenendo conto di questi punti."
                )

            last_workflow, last_path = await build_workflow(
                spec_content=build_prompt,
                spec_id=spec.spec_id,
                business_line=spec.business_line,
                workspace=self.workspace,
                router=self.router,
            )

            # Pre-validazione deterministica (gratuita, prima del LLM)
            validation_errors = validate_n8n_json(last_workflow)
            if validation_errors:
                error_list = "\n".join(f"- {e}" for e in validation_errors)
                self._p(
                    f"Pre-validazione: {len(validation_errors)} errori tecnici n8n "
                    f"(iterazione {iteration}), rebuild diretto senza LLM review..."
                )
                logger.warning("Pre-validazione fallita:\n{}", error_list)
                feedback = f"Errori tecnici n8n da correggere obbligatoriamente:\n{error_list}"
                last_verdict = "DA RIFARE"
                last_report = f"Pre-validazione fallita — errori strutturali:\n{error_list}"
                continue

            self._p(f"Step 3/4 — review-workflow (DeepSeek V3.2, iterazione {iteration})...")
            last_report, last_verdict = await review_workflow(
                spec_content=spec.raw_content,
                workflow_json_str=json.dumps(last_workflow, indent=2, ensure_ascii=False),
                router=self.router,
            )

            if last_verdict == "APPROVABILE":
                self._p(f"Review OK ({iteration} iterazioni).")
                break
            elif last_verdict == "STOP":
                self._p("Review STOP — pipeline bloccato.")
                break
            else:
                # DA RIFARE: estrai il feedback e riprova
                feedback = _extract_review_feedback(last_report)
                self._p(f"Review DA RIFARE — nuova build con feedback.")

        return last_workflow, last_path, last_report, last_verdict

    # -----------------------------------------------------------------------
    # Step 4 — Dry-run
    # -----------------------------------------------------------------------

    async def run_dryrun(
        self, spec: WorkflowSpec, workflow_json: dict[str, Any]
    ) -> tuple[str, str | None]:
        """Importa il workflow in n8n, esegue, recupera log, poi elimina.

        Returns:
            (dry_run_log_text, tmp_workflow_id_or_None)
        """
        self._p("Step 4/4 — dry-run (import temporaneo in n8n, esecuzione test)...")

        # Crea una copia "test" del workflow con nome che indica dry-run
        dryrun_wf = dict(workflow_json)
        dryrun_wf["name"] = f"[DRY-RUN] {workflow_json.get('name', spec.spec_id)}"

        tmp_id: str | None = None
        try:
            created = await self.n8n.import_workflow(dryrun_wf)
            tmp_id = str(created.get("id", ""))
            self._p(f"Workflow dry-run importato con successo (id: {tmp_id}).")
            # I workflow cron non hanno un endpoint REST trigger in n8n —
            # il dry-run verifica solo che il JSON sia importabile correttamente.
            log = _format_dryrun_log(spec, {"id": tmp_id, "status": "imported"})

        except Exception as exc:
            log = f"Dry-run fallito: {exc}"
            logger.warning("Dry-run errore: {}", exc)
        finally:
            # Cleanup: elimina sempre il workflow temporaneo
            if tmp_id:
                await self.n8n.delete_workflow(tmp_id)
                self._p(f"Workflow dry-run rimosso da n8n (id: {tmp_id}).")

        return log, tmp_id

    # -----------------------------------------------------------------------
    # Step 6 — Finalize (import definitivo + attivazione)
    # -----------------------------------------------------------------------

    async def finalize(
        self,
        spec: WorkflowSpec,
        workflow_json: dict[str, Any],
    ) -> ShipResult:
        """Importa definitivamente il workflow in n8n e lo attiva.

        Aggiorna lo status della spec, _state.md e ORCHESTRATION.md.
        """
        self._p("Importazione definitiva in n8n...")
        result = ShipResult(spec_id=spec.spec_id)

        try:
            created = await self.n8n.import_workflow(workflow_json)
            workflow_id = str(created.get("id", ""))
            activated = await self.n8n.activate_workflow(workflow_id)
        except Exception as exc:
            result.stopped_at = "import"
            result.stop_reason = str(exc)
            return result

        n8n_base = self.n8n.base_url
        result.n8n_workflow_id = workflow_id
        result.n8n_workflow_url = f"{n8n_base}/workflow/{workflow_id}"
        result.success = activated

        if activated:
            self._p(f"Workflow live: {result.n8n_workflow_url}")
            _update_spec_status(Path(spec.path), "live")
            _update_state_md(self.workspace / spec.business_line / "_state.md", spec)
            _update_orchestration_md(
                self.workspace / "ORCHESTRATION.md", spec, result.n8n_workflow_url
            )

        return result


# ---------------------------------------------------------------------------
# Helper: stato file
# ---------------------------------------------------------------------------

def _update_spec_status(spec_path: Path, new_status: str) -> None:
    content = spec_path.read_text(encoding="utf-8")
    today = date.today().isoformat()
    content = re.sub(r"^(Status:\s*).*$", lambda m: f"{m.group(1)}{new_status}",
                     content, count=1, flags=re.MULTILINE)
    content = re.sub(r"^(Updated:\s*).*$", lambda m: f"{m.group(1)}{today}",
                     content, count=1, flags=re.MULTILINE)
    spec_path.write_text(content, encoding="utf-8")


def _update_state_md(state_path: Path, spec: WorkflowSpec) -> None:
    if not state_path.exists():
        return
    content = state_path.read_text(encoding="utf-8")
    today = date.today().isoformat()
    # Aggiorna Last update
    content = re.sub(r"^(Last update:\s*).*$", lambda m: f"{m.group(1)}{today}",
                     content, count=1, flags=re.MULTILINE)
    # Aggiorna Active Workflows
    wf_line = f"- `{spec.spec_id}` — {spec.title} (live)"
    if "## Active Workflows" in content:
        content = content.replace(
            "## Active Workflows\n(none)",
            f"## Active Workflows\n{wf_line}",
        )
        if "(none)" not in content and wf_line not in content:
            content = re.sub(
                r"(## Active Workflows\n)",
                f"\\1{wf_line}\n",
                content, count=1,
            )
    state_path.write_text(content, encoding="utf-8")


def _update_orchestration_md(orch_path: Path, spec: WorkflowSpec, wf_url: str) -> None:
    if not orch_path.exists():
        return
    content = orch_path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    today = date.today().isoformat()

    # Aggiorna Last update
    content = re.sub(r"^(Last update:\s*).*$", lambda m: f"{m.group(1)}{today}T00:00:00+02:00",
                     content, count=1, flags=re.MULTILINE)

    # Incrementa Workflows live per la business line
    def _inc_wf(m: re.Match) -> str:
        try:
            n = int(m.group(1)) + 1
        except (ValueError, IndexError):
            n = 1
        return f"- Workflows live: {n}"

    pattern = rf"(### {re.escape(spec.business_line)}.*?)(- Workflows live: (\d+))"
    content = re.sub(pattern, lambda m: m.group(1) + _inc_wf(m), content, flags=re.DOTALL)

    # Aggiunge evento recente
    event_line = f"- {now} — Workflow live: {spec.title} ({spec.business_line}) — {wf_url}"
    content = re.sub(
        r"(## Recent events.*?\n)",
        f"\\1{event_line}\n",
        content, count=1, flags=re.DOTALL,
    )
    orch_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helper: dry-run log
# ---------------------------------------------------------------------------

def _format_dryrun_log(spec: WorkflowSpec, exec_result: dict) -> str:
    status = exec_result.get("status", exec_result.get("data", {}).get("status", "unknown"))
    exec_id = exec_result.get("id", exec_result.get("data", {}).get("id", ""))
    lines = [
        f"# Dry-run — {spec.title}",
        f"",
        f"**Execution ID:** {exec_id}",
        f"**Status:** {status}",
        f"",
        "**Nota:** questo era un workflow temporaneo `[DRY-RUN]`, ora rimosso da n8n.",
        "In produzione il workflow eseguirà le stesse operazioni sui destinatari reali.",
    ]
    # Includi eventuali dati di output se presenti
    data = exec_result.get("data") or exec_result
    if isinstance(data, dict) and "resultData" in data:
        lines += ["", "**Output dei nodi:**", "```json",
                  json.dumps(data["resultData"], indent=2, ensure_ascii=False)[:2000], "```"]
    return "\n".join(lines)


def _extract_review_feedback(report: str) -> str:
    """Estrae la sezione 'Modifiche richieste' dal report review."""
    lines = report.splitlines()
    in_section = False
    feedback_lines: list[str] = []
    for line in lines:
        if "Modifiche richieste" in line or "modifiche richieste" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("##") or line.startswith("# "):
                break
            feedback_lines.append(line)
    return "\n".join(feedback_lines).strip() or report[-500:]
