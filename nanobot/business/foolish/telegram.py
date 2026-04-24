"""Telegram helpers for The Foolish Butcher pipeline.

Direct HTTP calls via httpx — bypasses nanobot channel abstraction
so the pipeline can send messages independently from the agent loop.
"""

from __future__ import annotations

import httpx
from loguru import logger

from .config import FoolishConfig


_BASE = "https://api.telegram.org/bot{token}/{method}"
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{file_path}"


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


async def download_telegram_file(token: str, file_id: str) -> bytes:
    """Download a file from Telegram by file_id, return raw bytes."""
    get_file_url = _BASE.format(token=token, method="getFile")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(get_file_url, params={"file_id": file_id})
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]
        download_url = _FILE_BASE.format(token=token, file_path=file_path)
        resp = await client.get(download_url, timeout=60)
        resp.raise_for_status()
        return resp.content


async def send_photo_url(
    token: str,
    chat_id: int | str,
    photo_url: str,
    caption: str | None = None,
) -> dict:
    """Send a photo to a Telegram chat using a public URL."""
    url = _BASE.format(token=token, method="sendPhoto")
    payload: dict = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        payload["caption"] = caption
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def photo_archive_keyboard(message_id: str) -> dict:
    """Inline keyboard asking Alessandro whether to archive the photo."""
    return {
        "inline_keyboard": [[
            {"text": "💾 Archivia", "callback_data": f"photo_archive:yes:{message_id}"},
            {"text": "🗑️ Non archiviare", "callback_data": f"photo_archive:no:{message_id}"},
        ]]
    }


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
