"""Pipeline: Alessandro replies with ETA → update DB → send pre-production message to customer."""

from __future__ import annotations

from loguru import logger

from ..config import FoolishConfig
from ..db import MessageRepo, OrderRepo
from ..telegram import send_to_customer, send_to_alessandro


PRE_PRODUCTION_TEMPLATE = """\
Ciao {first_name},

Ordine ricevuto.{returning_note}

Sto producendo personalmente quello che hai ordinato. Tempo stimato: {eta_days} giorni.

Quando i fogli sono pronti ti scrivo con le foto di cosa ti arriva, così sai esattamente \
cosa ti sto spedendo — non due fogli identici escono dalla mia produzione.

Alessandro"""


async def handle_eta_confirmed(
    order_id: int,
    eta_days: int,
    cfg: FoolishConfig,
    order_repo: OrderRepo,
    message_repo: MessageRepo,
) -> None:
    order = await order_repo.get(order_id)
    if order is None:
        logger.error("handle_eta_confirmed: order {} not found", order_id)
        return

    order = await order_repo.set_eta(order_id, eta_days)
    order = await order_repo.update_state(order_id, "eta_confirmed")

    first_name = (order.customer_name or "").split()[0] if order.customer_name else "caro cliente"
    body = PRE_PRODUCTION_TEMPLATE.format(
        first_name=first_name,
        returning_note="",
        eta_days=eta_days,
    )

    if order.customer_telegram_id:
        # Send directly to customer
        await send_to_customer(cfg, order.customer_telegram_id, body)
        await message_repo.create(
            order_id=order_id,
            direction="outbound",
            stage="pre_production",
            body=body,
            recipient=str(order.customer_telegram_id),
            approved_by_alessandro=True,
        )
        logger.info("Pre-production message sent to customer for order {}", order_id)
    else:
        # Customer not linked yet — send draft to Alessandro for manual forwarding
        draft_alert = (
            f"✉️ <b>Bozza messaggio pre-produzione</b> (ordine #{order_id})\n\n"
            f"Il cliente non ha ancora collegato Telegram. Invia tu questo messaggio via email:\n\n"
            f"<pre>{body}</pre>\n\n"
            f"Per collegarlo: <code>/link {order_id} @username_telegram</code>"
        )
        await send_to_alessandro(cfg, draft_alert)
        await message_repo.create(
            order_id=order_id,
            direction="outbound",
            stage="pre_production",
            body=body,
            recipient=order.customer_email,
            approved_by_alessandro=None,
        )
        logger.info("Customer not linked for order {}, draft sent to Alessandro", order_id)

    order = await order_repo.update_state(order_id, "in_production")
    logger.info("Order {} advanced to in_production", order_id)
