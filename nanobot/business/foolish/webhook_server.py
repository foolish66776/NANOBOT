"""aiohttp webhook server for The Foolish Butcher pipeline.

Routes:
  GET  /health/foolish          — health check
  POST /hooks/woocommerce       — WooCommerce order.created / order.updated
  POST /hooks/telegram/foolish  — Telegram updates (callback_query for ETA buttons)
"""

from __future__ import annotations

import hashlib
import hmac
import json

from aiohttp import web
from loguru import logger

from .config import FoolishConfig
from .db import MessageRepo, OrderRepo, get_pool
from .pipeline.eta_confirmed import handle_eta_confirmed
from .pipeline.order_received import handle_order_received
from .telegram import send_to_alessandro


def build_app(cfg: FoolishConfig) -> web.Application:
    app = web.Application()
    handler = WebhookHandler(cfg)
    app.router.add_get("/health/foolish", handler.health)
    app.router.add_post("/hooks/woocommerce", handler.woocommerce)
    app.router.add_post("/hooks/packlink", handler.packlink)
    app.router.add_post("/hooks/telegram/foolish", handler.telegram_update)
    app.on_startup.append(lambda _app: _startup(_app, cfg))
    app.on_cleanup.append(_cleanup)
    return app


async def _startup(app: web.Application, cfg: FoolishConfig) -> None:
    from .db import get_pool
    app["pool"] = await get_pool(cfg.database_url)
    app["cfg"] = cfg
    logger.info("Foolish webhook server started")


async def _cleanup(app: web.Application) -> None:
    from .db import close_pool
    await close_pool()


class WebhookHandler:
    def __init__(self, cfg: FoolishConfig) -> None:
        self._cfg = cfg

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "foolish"})

    async def woocommerce(self, request: web.Request) -> web.Response:
        body = await request.read()

        if self._cfg.woo_webhook_secret:
            sig = request.headers.get("X-WC-Webhook-Signature", "")
            if not _verify_hmac(self._cfg.woo_webhook_secret.encode(), body, sig):
                logger.warning("WooCommerce webhook: invalid signature")
                return web.Response(status=401, text="Invalid signature")

        topic = request.headers.get("X-WC-Webhook-Topic", "")
        logger.info("WooCommerce webhook received: topic={} content-type={} body_len={} body_preview={}",
                    topic, request.content_type, len(body), body[:200])

        try:
            payload = json.loads(body)
        except Exception:
            logger.warning("WooCommerce webhook: non-JSON body, ignoring (topic={})", topic)
            return web.Response(status=200, text="ok")

        logger.info("WooCommerce webhook parsed: topic={} order={}", topic, payload.get("id"))

        pool = request.app["pool"]
        cfg = request.app["cfg"]
        order_repo = OrderRepo(pool)
        message_repo = MessageRepo(pool)

        if topic in ("order.created", "order.updated"):
            await handle_order_received(payload, cfg, order_repo, message_repo)

        return web.Response(status=200, text="ok")

    async def packlink(self, request: web.Request) -> web.Response:
        """Handle Packlink delivery status webhooks."""
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        cfg = request.app["cfg"]
        pool = request.app["pool"]

        # Packlink sends an array of shipment events
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            status = (event.get("status") or event.get("StatusMessage") or "").upper()
            tracking = event.get("trackingCode") or event.get("reference") or ""
            if not tracking:
                continue

            logger.info("Packlink event: status={} tracking={}", status, tracking)

            if "DELIVERED" in status or status in ("DEL", "OK_DEL"):
                # Find order by tracking number
                row = await pool.fetchrow(
                    "SELECT id FROM foolish.orders WHERE tracking_number = $1 AND pipeline_state = 'shipped'",
                    tracking,
                )
                if row:
                    from .pipeline.followup import schedule_followup
                    await schedule_followup(row["id"], cfg)
                    from .telegram import send_to_alessandro
                    await send_to_alessandro(
                        cfg,
                        f"📦 Ordine #{row['id']} consegnato (Packlink). "
                        f"Follow-up programmato tra {cfg.followup_delay_days} giorni.",
                    )

        return web.Response(status=200, text="ok")

    async def telegram_update(self, request: web.Request) -> web.Response:
        """Handle Telegram updates routed here (callback queries for ETA buttons)."""
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        expected = self._cfg.woo_webhook_secret  # reuse same secret for simplicity
        if expected and secret != expected:
            return web.Response(status=401, text="Unauthorized")

        try:
            update = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        pool = request.app["pool"]
        cfg = request.app["cfg"]

        callback = update.get("callback_query")
        if callback:
            await _handle_callback(callback, cfg, pool)

        message = update.get("message")
        if message:
            await _handle_message(message, cfg, pool)

        return web.Response(status=200, text="ok")


async def _handle_callback(callback: dict, cfg: FoolishConfig, pool) -> None:
    """Process inline keyboard callbacks from Alessandro (ETA selection)."""
    data = callback.get("data", "")
    from_id = callback.get("from", {}).get("id")

    if from_id != cfg.alessandro_chat_id:
        return  # Ignore callbacks from other users

    if data.startswith("eta:"):
        parts = data.split(":")
        if len(parts) == 3:
            _, order_id_str, days_str = parts
            if days_str == "custom":
                await send_to_alessandro(
                    cfg,
                    f"Digita il numero di giorni per l'ordine #{order_id_str} (solo il numero):",
                )
                return
            try:
                order_id = int(order_id_str)
                eta_days = int(days_str)
            except ValueError:
                return

            order_repo = OrderRepo(pool)
            message_repo = MessageRepo(pool)
            await handle_eta_confirmed(order_id, eta_days, cfg, order_repo, message_repo)

            await send_to_alessandro(
                cfg,
                f"✅ ETA confermata: {eta_days} giorni per ordine #{order_id}. Messaggio pre-produzione inviato.",
            )


async def _handle_message(message: dict, cfg: FoolishConfig, pool) -> None:
    """Handle text messages from Alessandro (e.g. custom ETA reply, /link command)."""
    from_id = message.get("from", {}).get("id")
    text = (message.get("text") or "").strip()

    if from_id != cfg.alessandro_chat_id:
        return

    if text.startswith("/link"):
        parts = text.split()
        if len(parts) == 3:
            _, order_id_str, tg_handle = parts
            await send_to_alessandro(
                cfg,
                f"Linking manuale non ancora implementato — per ora registra il telegram_id numerico "
                f"con /linkid {order_id_str} <id_numerico>",
            )
        return

    if text.startswith("/linkid"):
        parts = text.split()
        if len(parts) == 3:
            _, order_id_str, tg_id_str = parts
            try:
                order_id = int(order_id_str)
                tg_id = int(tg_id_str)
            except ValueError:
                await send_to_alessandro(cfg, "Formato errato. Usa: /linkid <order_id> <telegram_id>")
                return
            order_repo = OrderRepo(pool)
            await order_repo.set_customer_telegram_id(order_id, tg_id)
            await send_to_alessandro(cfg, f"✅ Ordine #{order_id} collegato a Telegram ID {tg_id}")
        return

    # Custom ETA reply: if Alessandro simply types a number after the bot asked for custom ETA,
    # we can't easily track context here — just acknowledge.
    if text.isdigit():
        await send_to_alessandro(
            cfg,
            f"Numero ricevuto: {text}. Specifica ordine con formato: eta <order_id> {text}",
        )


def _verify_hmac(secret: bytes, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret, body, hashlib.sha256).digest()
    import base64
    expected_b64 = base64.b64encode(expected).decode()
    return hmac.compare_digest(expected_b64, signature)
