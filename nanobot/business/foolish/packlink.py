"""Packlink Pro API client for The Foolish Butcher."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger


def _headers(api_key: str) -> dict:
    key = api_key.strip()
    # Packlink Pro API accepts both "Apikey" and "Bearer" — logged for debug
    scheme = "Bearer"
    logger.debug("Packlink auth: scheme={} key_len={} key_prefix={}", scheme, len(key), key[:4])
    return {"Authorization": f"{scheme} {key}", "Content-Type": "application/json"}


def _raise_with_body(resp: "httpx.Response") -> None:
    """Raise HTTPStatusError logging the response body first."""
    if resp.is_error:
        logger.error(
            "Packlink API error: {} {} — body: {}",
            resp.status_code,
            resp.request.url,
            resp.text[:500],
        )
        resp.raise_for_status()


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
        _raise_with_body(resp)
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
        _raise_with_body(resp)
        data = resp.json()
        inner = data.get("data", data) if isinstance(data, dict) else {}
        # Packlink returns {"data": {"label_url": "...", "pdf": "..."}}
        return inner.get("pdf") or inner.get("label_url") or inner.get("url")


async def get_available_services(
    api_key: str,
    base_url: str,
    from_country: str,
    from_zip: str,
    to_country: str,
    to_zip: str,
    weight_kg: float,
    width_cm: float = 30,
    height_cm: float = 5,
    length_cm: float = 30,
) -> list[dict[str, Any]]:
    """Elenca i servizi di spedizione disponibili con prezzi per una tratta e un pacco."""
    params = {
        "from[country]": from_country.upper(),
        "from[zip]": from_zip,
        "to[country]": to_country.upper(),
        "to[zip]": to_zip,
        "packages[0][width]": width_cm,
        "packages[0][height]": height_cm,
        "packages[0][length]": length_cm,
        "packages[0][weight]": weight_kg,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{base_url}/services/available",
            headers=_headers(api_key),
            params=params,
        )
        _raise_with_body(resp)
        data = resp.json()
        services = data.get("data", data) if isinstance(data, dict) else data
        result = []
        for s in (services if isinstance(services, list) else []):
            result.append({
                "id": s.get("id") or s.get("service_id") or "",
                "name": s.get("name") or s.get("service_name") or "",
                "carrier": s.get("carrier_name") or s.get("carrier") or "",
                "price": s.get("base_price") or s.get("price") or s.get("total_price") or 0,
                "currency": s.get("currency") or "EUR",
                "transit_days": s.get("transit_days") or s.get("delivery_days") or "",
                "dropoff": s.get("dropoff") or False,
            })
        result.sort(key=lambda x: float(x["price"] or 0))
        return result


async def create_shipment_draft(
    api_key: str,
    base_url: str,
    *,
    # mittente
    from_name: str,
    from_surname: str,
    from_company: str,
    from_street: str,
    from_city: str,
    from_zip: str,
    from_country: str,
    from_phone: str,
    from_email: str,
    # destinatario
    to_name: str,
    to_surname: str,
    to_street: str,
    to_city: str,
    to_zip: str,
    to_country: str,
    to_phone: str,
    to_email: str,
    to_company: str = "",
    # pacco
    weight_kg: float,
    width_cm: float = 30,
    height_cm: float = 5,
    length_cm: float = 30,
    # contenuto
    content: str = "Practice skin for tattoo",
    content_value: float = 25.0,
    # servizio (opzionale — se omesso resta bozza da completare nel dashboard)
    service_id: str = "",
) -> dict[str, Any]:
    """Crea una bozza di spedizione su Packlink Pro. Restituisce reference + URL dashboard."""
    payload: dict[str, Any] = {
        "from": {
            "name": from_name,
            "surname": from_surname,
            "company": from_company,
            "street1": from_street,
            "city": from_city,
            "zip_code": from_zip,
            "country": from_country.upper(),
            "phone": from_phone,
            "email": from_email,
        },
        "to": {
            "name": to_name,
            "surname": to_surname,
            "company": to_company,
            "street1": to_street,
            "city": to_city,
            "zip_code": to_zip,
            "country": to_country.upper(),
            "phone": to_phone,
            "email": to_email,
        },
        "packages": [
            {
                "width": width_cm,
                "height": height_cm,
                "length": length_cm,
                "weight": weight_kg,
            }
        ],
        "content": content,
        "content_value": content_value,
        "source": "api",
    }
    if service_id:
        payload["service_id"] = service_id

    _key = api_key.strip()
    logger.debug("Packlink create_shipment_draft: key_len={} key_prefix={}", len(_key), _key[:4])
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{base_url}/shipments",
            headers=_headers(_key),
            json=payload,
        )
        _raise_with_body(resp)
        data = resp.json()
        inner = data.get("data", data) if isinstance(data, dict) else data

    reference = (
        inner.get("shipment_custom_reference")
        or inner.get("reference")
        or inner.get("id")
        or ""
    )
    dashboard_url = f"https://pro.packlink.com/private/shipments/{reference}" if reference else ""

    logger.info(
        "Packlink draft created: ref={} to={} {} {}",
        reference, to_name, to_surname, to_city,
    )
    return {
        "reference": reference,
        "dashboard_url": dashboard_url,
        "status": inner.get("state") or inner.get("status") or "draft",
        "raw": inner,
    }


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
