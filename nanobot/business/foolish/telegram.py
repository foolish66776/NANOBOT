"""Telegram helpers for The Foolish Butcher pipeline.

Direct HTTP calls via httpx — bypasses nanobot channel abstraction
so the pipeline can send messages independently from the agent loop.
"""

from __future__ import annotations

import httpx
from loguru import logger

from .config import FoolishConfig


_BASE = "https://api.telegram.org/bot{token}/{method}"


async def send_message(
    token: str,
    chat_id: int | str,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str = "HTML",
) -> dict:
    url = _BASE.format(token=token, method="sendMessage")
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_to_alessandro(cfg: FoolishConfig, text: str, reply_markup: dict | None = None) -> dict:
    return await send_message(cfg.telegram_bot_token, cfg.alessandro_chat_id, text, reply_markup)


async def send_to_customer(
    cfg: FoolishConfig,
    telegram_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict:
    return await send_message(cfg.telegram_bot_token, telegram_id, text, reply_markup)


def eta_inline_keyboard(order_id: int) -> dict:
    """Inline keyboard for Alessandro to reply with ETA days."""
    buttons = [
        [
            {"text": "3 gg", "callback_data": f"eta:{order_id}:3"},
            {"text": "5 gg", "callback_data": f"eta:{order_id}:5"},
            {"text": "7 gg", "callback_data": f"eta:{order_id}:7"},
        ],
        [
            {"text": "10 gg", "callback_data": f"eta:{order_id}:10"},
            {"text": "14 gg", "callback_data": f"eta:{order_id}:14"},
            {"text": "✏️ Digita ETA", "callback_data": f"eta:{order_id}:custom"},
        ],
    ]
    return {"inline_keyboard": buttons}
