"""Packlink status polling — runs every 6 hours as background task in the webhook server."""

from __future__ import annotations

from loguru import logger

from ..config import FoolishConfig
from ..packlink import get_shipment
from ..telegram import send_to_alessandro


_DELIVERED_SLUGS = {"DELIVERED", "DEL", "OK_DEL"}
_OUT_SLUGS = {"OUT_FOR_DELIVERY", "OUTFORDELIVERY", "OUT-FOR-DELIVERY"}
_INCIDENT_SLUGS = {"INCIDENCE", "INCIDENT", "EXCEPTION", "FAILED"}


def _extract_slug(state: object) -> str:
    if isinstance(state, dict):
        return (
            state.get("slug") or state.get("description") or state.get("carrier_state") or ""
        ).upper()
    return str(state).upper()


async def run_packlink_poll(cfg: FoolishConfig) -> dict:
    """Check all shipped-via-Packlink orders and react to status changes.

    Only processes orders where tracking_carrier = 'packlink'.
    Returns summary dict.
    """
    import asyncpg
    from .followup import schedule_followup

    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=3)
    try:
        rows = await pool.fetch(
            """
            SELECT id, tracking_number, customer_name
            FROM foolish.orders
            WHERE pipeline_state = 'shipped'
              AND LOWER(tracking_carrier) = 'packlink'
              AND tracking_number IS NOT NULL
            """
        )

        summary = {"checked": 0, "delivered": 0, "out_for_delivery": 0, "incidents": 0, "errors": 0}

        for row in rows:
            order_id = row["id"]
            reference = row["tracking_number"]
            try:
                shipment = await get_shipment(cfg.packlink_api_key, cfg.packlink_base_url, reference)
                slug = _extract_slug(shipment.get("state") or shipment.get("status") or "")
                summary["checked"] += 1
                logger.debug("Packlink poll: order={} ref={} slug={}", order_id, reference, slug)

                if any(s in slug for s in _DELIVERED_SLUGS):
                    await schedule_followup(order_id, cfg)
                    await send_to_alessandro(
                        cfg,
                        f"📦 Ordine #{order_id} consegnato (rilevato via polling Packlink).\n"
                        f"Follow-up programmato tra {cfg.followup_delay_days} giorni.",
                    )
                    summary["delivered"] += 1

                elif any(s in slug for s in _OUT_SLUGS):
                    await send_to_alessandro(
                        cfg,
                        f"🚚 Ordine #{order_id} in consegna oggi (Packlink: {slug}).",
                    )
                    summary["out_for_delivery"] += 1

                elif any(s in slug for s in _INCIDENT_SLUGS):
                    await send_to_alessandro(
                        cfg,
                        f"⚠️ Problema spedizione ordine #{order_id} (Packlink: {slug}).\n"
                        "Controlla il portale Packlink Pro.",
                    )
                    summary["incidents"] += 1

            except Exception as exc:
                logger.error("Packlink poll error for order {} ref {}: {}", order_id, reference, exc)
                summary["errors"] += 1

        logger.info("Packlink poll completed: {}", summary)
        return summary
    finally:
        await pool.close()
