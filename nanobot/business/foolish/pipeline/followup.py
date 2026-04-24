"""Pipeline: post-delivery follow-up check-in (cron-driven)."""

from __future__ import annotations

from loguru import logger

from ..config import FoolishConfig
from ..telegram import send_to_customer, send_to_alessandro


FOLLOWUP_TEMPLATE = """\
{first_name}, il pacco dovrebbe essere arrivato qualche giorno fa.

Tutto ok? Hai già provato a tatuarci qualcosa?

Mi interessa davvero — ogni feedback mi serve per il prossimo lotto."""


async def run_followup_cron(cfg: FoolishConfig) -> int:
    """Check for orders due for follow-up and send messages. Returns number of orders processed."""
    import asyncpg

    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=2)
    try:
        due = await pool.fetch(
            """
            SELECT id, customer_name, customer_telegram_id, customer_email
            FROM foolish.orders
            WHERE pipeline_state = 'delivered'
              AND followup_scheduled_at <= NOW()
              AND followup_sent_at IS NULL
            ORDER BY followup_scheduled_at ASC
            LIMIT 10
            """
        )

        if not due:
            return 0

        processed = 0
        for row in due:
            order_id = row["id"]
            first_name = (row["customer_name"] or "").split()[0] if row["customer_name"] else "ciao"
            body = FOLLOWUP_TEMPLATE.format(first_name=first_name)

            try:
                if row["customer_telegram_id"]:
                    await send_to_customer(cfg, row["customer_telegram_id"], body)
                    await pool.execute(
                        "UPDATE foolish.orders SET followup_sent_at = NOW(), pipeline_state = 'followup_done', updated_at = NOW() WHERE id = $1",
                        order_id,
                    )
                    logger.info("Followup sent to customer for order {}", order_id)
                else:
                    # No Telegram — alert Alessandro
                    await send_to_alessandro(
                        cfg,
                        f"📬 Follow-up ordine #{order_id} da inviare manualmente:\n\n<pre>{body}</pre>\n\n"
                        f"Cliente: {row['customer_email']}",
                    )
                    await pool.execute(
                        "UPDATE foolish.orders SET followup_sent_at = NOW(), pipeline_state = 'followup_done', updated_at = NOW() WHERE id = $1",
                        order_id,
                    )
                processed += 1
            except Exception:
                logger.exception("Failed to send followup for order {}", order_id)

        return processed
    finally:
        await pool.close()


async def schedule_followup(order_id: int, cfg: FoolishConfig, delay_days: int | None = None) -> None:
    """Set followup_scheduled_at on an order after delivery confirmed."""
    import asyncpg

    days = delay_days if delay_days is not None else cfg.followup_delay_days
    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=2)
    try:
        await pool.execute(
            f"""UPDATE foolish.orders
                SET delivered_at = NOW(),
                    pipeline_state = 'delivered',
                    followup_scheduled_at = NOW() + INTERVAL '{days} days',
                    updated_at = NOW()
                WHERE id = $1""",
            order_id,
        )
        logger.info("Order {} delivered, followup scheduled in {}d", order_id, days)
    finally:
        await pool.close()
