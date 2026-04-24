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
from .telegram import (
    download_telegram_file,
    photo_archive_keyboard,
    send_message,
    send_photo_url,
    send_to_alessandro,
)


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

        from .packlink import parse_webhook_event

        raw_events = payload if isinstance(payload, list) else [payload]
        for raw in raw_events:
            event = parse_webhook_event(raw)
            reference = event["reference"]
            carrier_tracking = event["carrier_tracking"]
            status = event["status"]

            if not reference and not carrier_tracking:
                logger.debug("Packlink webhook: no reference or tracking, skipping")
                continue

            logger.info(
                "Packlink event: status={} reference={} carrier_tracking={}",
                status, reference, carrier_tracking,
            )

            # Match order by Packlink reference or carrier tracking number
            row = await pool.fetchrow(
                """SELECT id FROM foolish.orders
                   WHERE pipeline_state = 'shipped'
                     AND (tracking_number = $1 OR tracking_number = $2)
                   LIMIT 1""",
                reference, carrier_tracking,
            )

            if not row:
                logger.warning("Packlink event: no shipped order found for ref={} carrier={}", reference, carrier_tracking)
                continue

            order_id = row["id"]

            _DELIVERED_SLUGS = {"DELIVERED", "DEL", "OK_DEL"}
            _TRANSIT_SLUGS = {"IN_TRANSIT", "IN-TRANSIT", "INTRANSIT", "TRANSIT"}
            _OUT_SLUGS = {"OUT_FOR_DELIVERY", "OUT-FOR-DELIVERY", "OUTFORDELIVERY"}
            _INCIDENT_SLUGS = {"INCIDENCE", "INCIDENT", "EXCEPTION", "FAILED"}

            if any(s in status for s in _DELIVERED_SLUGS):
                from .pipeline.followup import schedule_followup
                await schedule_followup(order_id, cfg)
                await send_to_alessandro(
                    cfg,
                    f"📦 Ordine #{order_id} consegnato (Packlink).\n"
                    f"Follow-up programmato tra {cfg.followup_delay_days} giorni.",
                )
            elif any(s in status for s in _OUT_SLUGS):
                await send_to_alessandro(cfg, f"🚚 Ordine #{order_id} in consegna oggi (Packlink).")
            elif any(s in status for s in _TRANSIT_SLUGS):
                await send_to_alessandro(cfg, f"📫 Ordine #{order_id} in transito (Packlink).")
            elif any(s in status for s in _INCIDENT_SLUGS):
                await send_to_alessandro(
                    cfg,
                    f"⚠️ Problema spedizione ordine #{order_id} (Packlink: {status}).\n"
                    "Controlla il portale Packlink.",
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
    """Process inline keyboard callbacks from Alessandro (ETA, matching, photo archive)."""
    data = callback.get("data", "")
    from_id = callback.get("from", {}).get("id")

    if from_id != cfg.alessandro_chat_id:
        return  # Ignore callbacks from other users

    if data.startswith("photo_archive:"):
        await _handle_photo_archive_callback(data, cfg, pool)
        return

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


async def _handle_photo_archive_callback(data: str, cfg: FoolishConfig, pool) -> None:
    """Handle photo_archive:yes|no:<uuid> callbacks."""
    from uuid import UUID
    parts = data.split(":")
    if len(parts) != 3:
        return
    _, decision, msg_id_str = parts
    try:
        msg_uuid = UUID(msg_id_str)
    except ValueError:
        return

    if decision == "yes":
        await pool.execute(
            "UPDATE foolish.messages SET approved_by_alessandro = TRUE WHERE id = $1",
            msg_uuid,
        )
        await send_to_alessandro(cfg, "✅ Foto archiviata su R2.")
    elif decision == "no":
        row = await pool.fetchrow(
            "SELECT media_urls FROM foolish.messages WHERE id = $1", msg_uuid
        )
        if row and row["media_urls"]:
            try:
                from .r2 import delete_photo
                await delete_photo(cfg, row["media_urls"][0])
            except Exception as exc:
                logger.warning("R2 delete failed: {}", exc)
        await pool.execute(
            "UPDATE foolish.messages SET approved_by_alessandro = FALSE WHERE id = $1",
            msg_uuid,
        )
        await send_to_alessandro(cfg, "🗑️ Foto eliminata da R2.")


async def _handle_customer_start(message: dict, order_id: int, cfg: FoolishConfig, pool) -> None:
    """Customer clicked deep link /start order_<id> — link Telegram ID to order."""
    from_id = message.get("from", {}).get("id")
    first_name = message.get("from", {}).get("first_name", "")

    order_repo = OrderRepo(pool)
    order = await order_repo.get(order_id)

    if order is None:
        await send_message(
            cfg.telegram_bot_token, from_id,
            "Non ho trovato l'ordine associato a questo link. Contatta Alessandro."
        )
        logger.warning("Customer /start with unknown order_id={}", order_id)
        return

    await order_repo.set_customer_telegram_id(order_id, from_id)
    logger.info("Customer linked for order {}: telegram_id={} name={}", order_id, from_id, first_name)

    customer_first = (order.customer_name or "").split()[0] if order.customer_name else (first_name or "ciao")
    await send_message(
        cfg.telegram_bot_token, from_id,
        f"Ciao {customer_first}! 👋\n\n"
        f"Sei collegato all'ordine #{order_id}.\n"
        f"Riceverai qui aggiornamenti diretti da Alessandro.",
    )

    await send_to_alessandro(
        cfg,
        f"🔗 <b>Cliente collegato!</b>\n"
        f"Ordine #{order_id} — {order.customer_name or order.customer_email}\n"
        f"Telegram ID: <code>{from_id}</code> | Nome: {first_name}",
    )


async def _handle_message(message: dict, cfg: FoolishConfig, pool) -> None:
    """Handle messages: /start deep link (any user) + Alessandro commands and photo uploads."""
    from_id = message.get("from", {}).get("id")
    text = (message.get("text") or "").strip()

    # /start order_<id> — handle from ANY user (customer onboarding via deep link)
    if text.startswith("/start"):
        param = text[len("/start"):].strip()
        if param.startswith("order_"):
            id_str = param[len("order_"):]
            if id_str.isdigit():
                await _handle_customer_start(message, int(id_str), cfg, pool)
        return

    if from_id != cfg.alessandro_chat_id:
        return

    # Photo upload flow (Alessandro only)
    photo_list = message.get("photo")
    if photo_list:
        await _handle_photo_message(message, photo_list, cfg, pool)
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


async def _handle_photo_message(message: dict, photos: list, cfg: FoolishConfig, pool) -> None:
    """Alessandro sent a photo. Parse order_id from caption, upload to R2, forward to customer."""
    caption = (message.get("caption") or "").strip()

    # Extract order_id from caption (any token that is a number, optionally prefixed with #)
    order_id: int | None = None
    for token in caption.split():
        clean = token.lstrip("#")
        if clean.isdigit():
            order_id = int(clean)
            break

    if order_id is None:
        await send_to_alessandro(
            cfg,
            "📎 Foto ricevuta. Risendila con il numero ordine come didascalia (es: <code>12345</code>).",
        )
        return

    if not cfg.r2_endpoint or not cfg.r2_bucket:
        await send_to_alessandro(cfg, "⚠️ R2 non configurato (FOOLISH_R2_ENDPOINT / FOOLISH_R2_BUCKET mancanti).")
        return

    # Select largest photo variant
    best = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
    file_id = best["file_id"]

    try:
        photo_bytes = await download_telegram_file(cfg.telegram_bot_token, file_id)
    except Exception as exc:
        logger.error("Photo download failed: {}", exc)
        await send_to_alessandro(cfg, f"⚠️ Errore download foto da Telegram: {exc}")
        return

    try:
        from .r2 import upload_photo
        r2_url = await upload_photo(cfg, order_id, photo_bytes)
    except Exception as exc:
        logger.error("R2 upload failed: {}", exc)
        await send_to_alessandro(cfg, f"⚠️ Errore upload R2: {exc}")
        return

    logger.info("Photo uploaded to R2 for order {}: {}", order_id, r2_url)

    # Forward photo to customer if Telegram is linked
    order_repo = OrderRepo(pool)
    order = await order_repo.get(order_id)
    sent_to_customer = False
    if order and order.customer_telegram_id:
        try:
            first_name = (order.customer_name or "").split()[0] if order.customer_name else "ciao"
            await send_photo_url(
                cfg.telegram_bot_token,
                order.customer_telegram_id,
                r2_url,
                caption=f"{first_name}, ecco il tuo ordine #{order_id} 🎨",
            )
            sent_to_customer = True
        except Exception as exc:
            logger.warning("Failed to send photo to customer: {}", exc)

    # Save to messages table (approved_by_alessandro=None = pending decision)
    message_repo = MessageRepo(pool)
    msg_id = await message_repo.create(
        order_id=order_id,
        direction="outbound",
        stage="photo_preview",
        body=f"Foto ordine #{order_id}",
        media_urls=[r2_url],
        approved_by_alessandro=None,
    )

    customer_status = (
        "✅ Foto inviata al cliente via Telegram."
        if sent_to_customer
        else "⚠️ Cliente senza Telegram collegato — foto non inviata."
    )
    await send_to_alessandro(
        cfg,
        f"{customer_status}\nArchivio la foto su R2?",
        reply_markup=photo_archive_keyboard(str(msg_id)),
    )


def _verify_hmac(secret: bytes, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret, body, hashlib.sha256).digest()
    import base64
    expected_b64 = base64.b64encode(expected).decode()
    return hmac.compare_digest(expected_b64, signature)
