"""Packlink Pro API client for The Foolish Butcher."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Apikey {api_key}", "Content-Type": "application/json"}


_WEBHOOK_EVENTS = [
    "shipment_trackingUpdated",
    "shipment_delivered",
    "shipment_incidence",
]

# Packlink Pro API uses /webhook (singular) for registration, /webhooks (plural) for listing.
# Some plan tiers respond 404 on API registration — in that case configure manually in the dashboard.
_REGISTER_PATHS = ["/webhook", "/hooks", "/webhooks"]
_LIST_PATHS = ["/webhooks", "/hooks", "/webhook"]


async def register_webhook(api_key: str, base_url: str, callback_url: str) -> list[dict]:
    """Register our callback URL for all tracking events. Returns list of created hooks."""
    created = []
    async with httpx.AsyncClient(timeout=15) as client:
        for event in _WEBHOOK_EVENTS:
            result: dict | None = None
            for path in _REGISTER_PATHS:
                try:
                    resp = await client.post(
                        f"{base_url}{path}",
                        json={"event": event, "url": callback_url},
                        headers=_headers(api_key),
                    )
                    if resp.status_code in (200, 201):
                        result = {"event": event, "status": "ok", "path": path}
                        logger.info("Packlink webhook registered: event={} url={} via {}", event, callback_url, path)
                        break
                    elif resp.status_code == 404:
                        logger.debug("Packlink {} → 404, trying next path", path)
                        continue
                    else:
                        result = {
                            "event": event, "status": "error",
                            "code": resp.status_code, "body": resp.text[:300],
                        }
                        logger.warning("Packlink webhook registration failed: event={} status={} body={}", event, resp.status_code, resp.text[:300])
                        break
                except Exception as exc:
                    result = {"event": event, "status": "error", "error": str(exc)}
                    logger.error("Packlink webhook registration error: event={} exc={}", event, exc)
                    break

            if result is None:
                result = {
                    "event": event, "status": "error",
                    "code": 404, "body": "All paths returned 404 — configure webhook manually in Packlink dashboard.",
                }
            created.append(result)
    return created


async def list_webhooks(api_key: str, base_url: str) -> list[dict]:
    """List currently registered Packlink webhooks."""
    async with httpx.AsyncClient(timeout=15) as client:
        for path in _LIST_PATHS:
            try:
                resp = await client.get(f"{base_url}{path}", headers=_headers(api_key))
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data.get("data", data) if isinstance(data, dict) else data
            except httpx.HTTPStatusError:
                continue
    return []


async def get_shipment(api_key: str, base_url: str, reference: str) -> dict[str, Any]:
    """Get Packlink shipment details by reference (e.g. ES123456789)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base_url}/shipments/{reference}",
            headers=_headers(api_key),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data


async def get_label_url(api_key: str, base_url: str, reference: str) -> str | None:
    """Get PDF label download URL for a Packlink shipment."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base_url}/shipments/{reference}/labels",
            headers=_headers(api_key),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        inner = data.get("data", data) if isinstance(data, dict) else {}
        # Packlink returns {"data": {"label_url": "...", "pdf": "..."}}
        return inner.get("pdf") or inner.get("label_url") or inner.get("url")


def parse_webhook_event(payload: dict) -> dict[str, str]:
    """Normalize a Packlink webhook payload to {reference, carrier_tracking, status}."""
    # Packlink Pro wraps events in a "data" key
    data = payload.get("data", payload)

    reference = (
        data.get("shipment_reference")
        or data.get("reference")
        or data.get("shipmentReference")
        or ""
    )
    carrier_tracking = (
        data.get("carrier_reference")
        or data.get("carrier_tracking_id")
        or data.get("carrierReference")
        or data.get("trackingCode")
        or ""
    )

    state_block = data.get("state", {})
    if isinstance(state_block, dict):
        status = (
            state_block.get("slug")
            or state_block.get("description")
            or state_block.get("carrier_state")
            or ""
        ).upper()
    else:
        status = (
            data.get("status")
            or data.get("StatusMessage")
            or str(state_block)
        ).upper()

    return {
        "reference": reference,
        "carrier_tracking": carrier_tracking,
        "status": status,
    }
