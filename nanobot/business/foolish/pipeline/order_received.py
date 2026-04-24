"""Pipeline: new WooCommerce order → DB upsert → Alessandro Telegram alert."""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..config import FoolishConfig
from ..db import MessageRepo, OrderRepo
from ..telegram import eta_inline_keyboard, send_to_alessandro


async def handle_order_received(
    payload: dict[str, Any],
    cfg: FoolishConfig,
    order_repo: OrderRepo,
    message_repo: MessageRepo,
) -> None:
    order_id = int(payload.get("id", 0))
    if not order_id:
        logger.warning("WooCommerce webhook missing order id, skipping")
        return

    billing = payload.get("billing", {})
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    customer_email = billing.get("email", "")

    line_items = [
        {
            "id": item.get("id"),
            "name": item.get("name", ""),
            "quantity": item.get("quantity", 1),
            "sku": item.get("sku", ""),
            "subtotal": item.get("subtotal", ""),
        }
        for item in payload.get("line_items", [])
    ]

    order_data = {
        "id": order_id,
        "customer_email": customer_email,
        "customer_name": customer_name or None,
        "line_items": line_items,
        "total": float(payload.get("total", 0) or 0),
        "currency": payload.get("currency", "EUR"),
        "raw_webhook": payload,
    }

    order = await order_repo.upsert(order_data)
    logger.info("Order {} upserted, state={}", order.id, order.pipeline_state)

    order = await order_repo.update_state(order.id, "eta_pending")

    # Format order summary for Alessandro
    items_text = "\n".join(
        f"  • {item['name']} × {item['quantity']}" for item in line_items
    ) or "  (nessun prodotto)"

    alert_text = (
        f"🛒 <b>Nuovo ordine #{order.id}</b>\n\n"
        f"👤 {customer_name or 'N/D'} — {customer_email}\n"
        f"💶 {order.total:.2f} {order.currency}\n\n"
        f"<b>Prodotti:</b>\n{items_text}\n\n"
        f"<b>Quanto tempo di produzione?</b>"
    )

    keyboard = eta_inline_keyboard(order.id)
    await send_to_alessandro(cfg, alert_text, reply_markup=keyboard)

    await message_repo.create(
        order_id=order.id,
        direction="outbound",
        stage="eta_request",
        body=alert_text,
        recipient=str(cfg.alessandro_chat_id),
        approved_by_alessandro=True,
    )

    logger.info("Alessandro alerted for order {}", order.id)
