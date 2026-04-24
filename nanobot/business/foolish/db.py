"""asyncpg pool and repository classes for The Foolish Butcher."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from loguru import logger

from .models import InvalidStateTransition, Order, Sheet, VALID_TRANSITIONS


_pool: asyncpg.Pool | None = None


async def get_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Order repository
# ---------------------------------------------------------------------------

class OrderRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(self, order_data: dict[str, Any]) -> Order:
        row = await self._pool.fetchrow(
            """
            INSERT INTO foolish.orders
                (id, customer_email, customer_name, line_items, total, currency, raw_webhook)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                customer_email = EXCLUDED.customer_email,
                customer_name  = EXCLUDED.customer_name,
                line_items     = EXCLUDED.line_items,
                total          = EXCLUDED.total,
                raw_webhook    = EXCLUDED.raw_webhook,
                updated_at     = NOW()
            RETURNING *
            """,
            order_data["id"],
            order_data["customer_email"],
            order_data.get("customer_name"),
            json.dumps(order_data["line_items"]),
            order_data.get("total"),
            order_data.get("currency", "EUR"),
            json.dumps(order_data.get("raw_webhook", {})),
        )
        return _row_to_order(row)

    async def get(self, order_id: int) -> Order | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM foolish.orders WHERE id = $1", order_id
        )
        return _row_to_order(row) if row else None

    async def update_state(self, order_id: int, new_state: str) -> Order:
        order = await self.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        allowed = VALID_TRANSITIONS.get(order.pipeline_state, [])
        if new_state not in allowed:
            raise InvalidStateTransition(
                f"Cannot transition order {order_id} from '{order.pipeline_state}' to '{new_state}'. "
                f"Allowed: {allowed}"
            )
        row = await self._pool.fetchrow(
            "UPDATE foolish.orders SET pipeline_state = $1, updated_at = NOW() WHERE id = $2 RETURNING *",
            new_state, order_id,
        )
        logger.info("Order {} state: {} → {}", order_id, order.pipeline_state, new_state)
        return _row_to_order(row)

    async def set_eta(self, order_id: int, days: int) -> Order:
        row = await self._pool.fetchrow(
            "UPDATE foolish.orders SET production_eta_days = $1, updated_at = NOW() WHERE id = $2 RETURNING *",
            days, order_id,
        )
        return _row_to_order(row)

    async def set_customer_telegram_id(self, order_id: int, telegram_id: int) -> None:
        await self._pool.execute(
            "UPDATE foolish.orders SET customer_telegram_id = $1, updated_at = NOW() WHERE id = $2",
            telegram_id, order_id,
        )


# ---------------------------------------------------------------------------
# Message repository
# ---------------------------------------------------------------------------

class MessageRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        order_id: int | None,
        direction: str,
        stage: str,
        body: str,
        recipient: str | None = None,
        approved_by_alessandro: bool | None = None,
        media_urls: list[str] | None = None,
    ) -> "UUID":
        from uuid import UUID
        row = await self._pool.fetchrow(
            """
            INSERT INTO foolish.messages
                (order_id, direction, stage, body, recipient, approved_by_alessandro, media_urls)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            order_id, direction, stage, body, recipient, approved_by_alessandro,
            media_urls or [],
        )
        return row["id"]

    async def mark_sent(self, message_id: str) -> None:
        await self._pool.execute(
            "UPDATE foolish.messages SET sent_at = NOW(), approved_by_alessandro = TRUE WHERE id = $1",
            message_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_order(row: asyncpg.Record) -> Order:
    d = dict(row)
    d["line_items"] = d["line_items"] if isinstance(d["line_items"], list) else json.loads(d["line_items"])
    return Order(**d)
