"""Orchestrator notification tool — sends messages to the Telegram orchestrator bot."""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Testo del messaggio da inviare al bot orchestrator.",
            }
        },
        "required": ["message"],
    }
)
class OrchestratorNotifyTool(Tool):
    """Invia una notifica al bot Telegram orchestrator (nano_orchestratore_bot).

    Richiede le variabili d'ambiente:
      TELEGRAM_ORCHESTRATOR_TOKEN  — token del bot orchestrator
      TELEGRAM_ORCHESTRATOR_CHAT_ID — chat_id di Alessandro su quel bot (configurato in Fase 6)
    """

    @property
    def name(self) -> str:
        return "orchestrator_notify"

    @property
    def description(self) -> str:
        return (
            "Invia un messaggio di notifica al bot Telegram orchestrator. "
            "Usato per notificare eventi come nuove spec, Council completato, workflow approvato."
        )

    async def execute(self, **kwargs: Any) -> str:
        message: str = kwargs["message"]

        token = os.environ.get("TELEGRAM_ORCHESTRATOR_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_ORCHESTRATOR_CHAT_ID", "").strip()

        if not token:
            logger.warning("orchestrator_notify: TELEGRAM_ORCHESTRATOR_TOKEN non configurato, notifica saltata.")
            return "Notifica saltata: TELEGRAM_ORCHESTRATOR_TOKEN non configurato."

        if not chat_id:
            logger.info("orchestrator_notify: TELEGRAM_ORCHESTRATOR_CHAT_ID non ancora configurato (verrà impostato in Fase 6), notifica saltata.")
            return "Notifica saltata: TELEGRAM_ORCHESTRATOR_CHAT_ID non ancora configurato (sarà disponibile dopo Fase 6)."

        try:
            import httpx

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)

            if resp.status_code == 200:
                logger.info("orchestrator_notify: messaggio inviato.")
                return "Notifica inviata all'orchestrator."
            else:
                logger.warning("orchestrator_notify: risposta inattesa {}: {}", resp.status_code, resp.text)
                return f"Notifica fallita: {resp.status_code} — {resp.text[:200]}"

        except ImportError:
            return "Notifica saltata: libreria httpx non disponibile."
        except Exception as exc:
            logger.error("orchestrator_notify: errore imprevisto: {}", exc)
            return f"Notifica fallita: {exc}"
