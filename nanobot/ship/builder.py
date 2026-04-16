"""Builder — genera il JSON del workflow n8n a partire dalla spec validata.

Usa MiniMax M2 (ruolo 'build') con un prompt che conosce il formato n8n.
Il JSON prodotto viene salvato in nanobot-workspace/<business>/workflows/<spec-id>.workflow.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from nanobot.llm.router import LLMRouter

_BUILD_SYSTEM = """Sei un esperto di n8n (versione 1.x) specializzato nella generazione di workflow JSON.

Il tuo compito è leggere una workflow-spec.md e produrre un JSON valido per n8n.

## Regole fondamentali

1. **Produci SOLO il JSON**, senza testo attorno, senza markdown code fences, senza spiegazioni.
2. Il JSON deve essere un oggetto n8n workflow completo con: name, nodes, connections, settings, staticData.
3. Ogni nodo ha: id (UUID), name, type, position [x,y], parameters, typeVersion.
4. Usa nodi n8n standard: Schedule Trigger, HTTP Request, Code, Send Email, Set, If, Merge, ecc.
5. I limiti hard della spec DEVONO essere implementati come nodi reali (es. Limit, Split In Batches, If con filtri).
6. Le credenziali vanno referenziate come {{ $credentials.nomecredenziale }}, mai hardcodate.
7. Il trigger deve corrispondere esattamente a quello dichiarato nella spec (cron expression, webhook path, ecc.).
8. La timezone NON va nei parametri del nodo Schedule Trigger. Va in `settings.timezone` a livello workflow (es. `"settings": {"executionOrder": "v1", "timezone": "Europe/Rome"}`).
8. Non aggiungere nodi non dichiarati nella spec.

## Formato connections

```json
{
  "connections": {
    "NomeNodoA": {
      "main": [[{"node": "NomeNodoB", "type": "main", "index": 0}]]
    }
  }
}
```

## Esempio struttura minima

```json
{
  "name": "Workflow Name",
  "nodes": [
    {
      "id": "uuid-1",
      "name": "Schedule Trigger",
      "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1,
      "position": [250, 300],
      "parameters": {
        "rule": {"interval": [{"field": "hours", "minutesInterval": 24}]}
      }
    }
  ],
  "connections": {},
  "settings": {"executionOrder": "v1"},
  "staticData": null
}
```

Rispondi SOLO con il JSON del workflow, nient'altro.
"""


async def build_workflow(
    spec_content: str,
    spec_id: str,
    business_line: str,
    workspace: Path | None = None,
    router: LLMRouter | None = None,
) -> tuple[dict, Path]:
    """Genera il JSON del workflow n8n dalla spec e lo salva su disco.

    Returns:
        (workflow_dict, saved_path)

    Raises:
        RuntimeError se MiniMax non produce JSON valido dopo 2 tentativi.
    """
    r = router or LLMRouter()
    ws = workspace or Path("~/dev/nanobot-workspace").expanduser()
    output_dir = ws / business_line / "workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{spec_id}.workflow.json"

    user_prompt = (
        f"Genera il workflow n8n JSON per questa spec:\n\n"
        f"---\n{spec_content}\n---\n\n"
        f"Produci SOLO il JSON. Nessun testo aggiuntivo."
    )

    last_error: Exception | None = None
    for attempt in range(1, 3):  # max 2 tentativi
        logger.info("Build workflow attempt {}/2 per spec '{}'", attempt, spec_id)
        raw = await r.complete(
            role="build",
            system=_BUILD_SYSTEM,
            user=user_prompt,
            max_tokens=8000,
        )

        # Prova a parsare il JSON (il modello potrebbe includere markdown)
        raw_clean = _strip_json_fences(raw)
        try:
            workflow = json.loads(raw_clean)
        except json.JSONDecodeError as exc:
            logger.warning("Build attempt {}: JSON non valido: {}", attempt, exc)
            last_error = exc
            # Seconda chance: chiedi una correzione
            user_prompt = (
                f"Il JSON che hai prodotto non è valido: {exc}\n\n"
                f"Riproduci il workflow JSON corretto per questa spec:\n\n"
                f"---\n{spec_content}\n---\n\n"
                f"Produci SOLO il JSON valido."
            )
            continue

        # Salva su disco
        output_path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Workflow JSON salvato in {}", output_path)
        return workflow, output_path

    raise RuntimeError(
        f"Build fallito dopo 2 tentativi per spec '{spec_id}': {last_error}"
    )


def _strip_json_fences(text: str) -> str:
    """Rimuove backtick-fences markdown se presenti."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Prima riga: ```json o ```
        start = 1
        # Ultima riga: ```
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        text = "\n".join(lines[start:end])
    return text.strip()
