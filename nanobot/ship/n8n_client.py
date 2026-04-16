"""Client per l'API REST di n8n self-hosted."""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


class N8nClient:
    """Wrapper minimale per le API di n8n necessarie al pipeline ship-workflow."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("N8N_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("N8N_API_KEY", "")
        self.timeout = timeout

        if not self.base_url:
            raise RuntimeError("N8N_BASE_URL non configurata")
        if not self.api_key:
            raise RuntimeError("N8N_API_KEY non configurata")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-N8N-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    # -----------------------------------------------------------------------
    # Workflow CRUD
    # -----------------------------------------------------------------------

    async def list_workflows(self) -> list[dict[str, Any]]:
        """Restituisce tutti i workflow (attivi e non)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/workflows",
                headers=self._headers,
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    async def import_workflow(self, workflow_json: dict[str, Any]) -> dict[str, Any]:
        """Importa un nuovo workflow (non attivo). Restituisce l'oggetto workflow creato."""
        # n8n accetta solo questi campi in POST — tutto il resto è server-generated
        _ALLOWED = {"name", "nodes", "connections", "settings", "staticData", "tags", "active"}
        payload = {k: v for k, v in workflow_json.items() if k in _ALLOWED}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/workflows",
                headers=self._headers,
                json=payload,
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Import workflow fallito {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    async def activate_workflow(self, workflow_id: str) -> bool:
        """Attiva un workflow esistente. Ritorna True se OK."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/workflows/{workflow_id}/activate",
                headers=self._headers,
            )
        if resp.status_code == 200:
            logger.info("Workflow {} attivato.", workflow_id)
            return True
        logger.warning("Attivazione workflow {} fallita: {} {}", workflow_id, resp.status_code, resp.text[:200])
        return False

    async def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Recupera un workflow per ID."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/workflows/{workflow_id}",
                headers=self._headers,
            )
        resp.raise_for_status()
        return resp.json()

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Elimina un workflow (usato per cleanup dry-run)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(
                f"{self.base_url}/api/v1/workflows/{workflow_id}",
                headers=self._headers,
            )
        return resp.status_code in (200, 204)

    # -----------------------------------------------------------------------
    # Executions
    # -----------------------------------------------------------------------

    async def run_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Esegue manualmente un workflow e restituisce l'oggetto execution."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/executions",
                headers=self._headers,
                json={"workflowId": workflow_id},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Run workflow {workflow_id} fallito {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    async def get_executions(
        self,
        workflow_id: str | None = None,
        limit: int = 20,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera le ultime esecuzioni, opzionalmente filtrate per workflow e data."""
        params: dict[str, Any] = {"limit": limit}
        if workflow_id:
            params["workflowId"] = workflow_id
        if since_iso:
            params["lastId"] = since_iso  # n8n filtra per lastId o startedAfter

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/executions",
                headers=self._headers,
                params=params,
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    # -----------------------------------------------------------------------
    # Health check
    # -----------------------------------------------------------------------

    async def ping(self) -> bool:
        """Verifica che l'istanza n8n risponda. Ritorna True se OK."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/workflows",
                    headers=self._headers,
                )
            return resp.status_code == 200
        except Exception:
            return False
