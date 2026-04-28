"""Async client for the Foolish Butcher Payload CMS REST API."""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

_token_cache: dict[str, Any] = {}  # {"token": str, "expires_at": float}


async def _get_token(base_url: str, email: str, password: str) -> str:
    now = time.time()
    cached = _token_cache.get("token")
    if cached and _token_cache.get("expires_at", 0) > now + 60:
        return cached

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{base_url}/api/users/login",
            json={"email": email, "password": password},
        )
        r.raise_for_status()
        data = r.json()

    token = data.get("token") or data.get("user", {}).get("token")
    if not token:
        raise RuntimeError(f"Payload login failed: {data}")

    exp = data.get("exp") or (now + 7200)
    _token_cache["token"] = token
    _token_cache["expires_at"] = float(exp) if float(exp) > now else now + float(exp)
    logger.debug("Payload CMS token refreshed, expires in {}s", int(_token_cache["expires_at"] - now))
    return token


class PayloadClient:
    def __init__(self, base_url: str, email: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password

    async def _auth_headers(self) -> dict[str, str]:
        token = await _get_token(self._base, self._email, self._password)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def get_products(
        self,
        active_only: bool = False,
        section: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "depth": 1}
        where: dict = {}
        if active_only:
            where["active"] = {"equals": True}
        if section:
            where["section"] = {"equals": section}
        if where:
            import json
            params["where"] = json.dumps(where)

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{self._base}/api/products",
                params=params,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json().get("docs", [])

    async def get_product(self, product_id: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/api/products/{product_id}",
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json()

    async def find_product_by_slug(self, slug: str) -> dict | None:
        import json
        params = {"where": json.dumps({"slug": {"equals": slug}}), "limit": 1}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/api/products",
                params=params,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        docs = r.json().get("docs", [])
        return docs[0] if docs else None

    async def find_product_by_sku(self, sku: str) -> dict | None:
        """Find a product that contains a variant with the given SKU."""
        products = await self.get_products()
        for p in products:
            for v in p.get("variants", []):
                if v.get("sku", "").upper() == sku.upper():
                    return p
        return None

    async def update_product(self, product_id: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.patch(
                f"{self._base}/api/products/{product_id}",
                json=data,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json().get("doc", r.json())

    async def create_product(self, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{self._base}/api/products",
                json=data,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json().get("doc", r.json())

    async def get_orders(
        self,
        pipeline_state: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "sort": "-createdAt", "depth": 0}
        if pipeline_state:
            import json
            params["where"] = json.dumps({"pipelineState": {"equals": pipeline_state}})
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{self._base}/api/orders",
                params=params,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json().get("docs", [])

    async def update_order(self, order_id: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.patch(
                f"{self._base}/api/orders/{order_id}",
                json=data,
                headers=await self._auth_headers(),
            )
            r.raise_for_status()
        return r.json().get("doc", r.json())


def _format_product_summary(p: dict) -> str:
    variants = p.get("variants") or []
    status_icon = "✅" if p.get("active") else "❌"
    limited = " 🔥" if p.get("limitedStock") else ""
    lines = [f"{status_icon} **{p['name']}**{limited} (`{p.get('slug', '?')}`)  [{p.get('section','?').upper()}]"]
    for v in variants:
        stock_icon = {"available": "🟢", "low": "🟡", "unavailable": "🔴"}.get(v.get("stockStatus", ""), "⚪")
        lines.append(
            f"  {stock_icon} {v.get('label','?')}  —  {v.get('price','?')}€  SKU: `{v.get('sku','?')}`"
        )
    return "\n".join(lines)
