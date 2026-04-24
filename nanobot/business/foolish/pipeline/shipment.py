"""Pipeline: Alessandro registers tracking → notify customer → advance to shipped."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ..config import FoolishConfig
from ..db import OrderRepo, MessageRepo
from ..telegram import send_to_customer, send_to_alessandro




TRACKING_TEMPLATE = "Partito. Tracking: {tracking_number} ({carrier}).\nLink: {tracking_url}"

_CARRIER_LINKS = {
    "gls": "https://gls-group.com/track/{tn}",
    "brt": "https://www.brt.it/it/spedizioni/cerca-spedizione/?search={tn}",
    "sda": "https://www.sda.it/wps/portal/Inseguimento?parcelNumber={tn}",
    "poste": "https://www.poste.it/cerca/index.html#/risultati-spedizioni/{tn}",
    "dhl": "https://www.dhl.com/it-it/home/tracking.html?tracking-id={tn}",
    "ups": "https://www.ups.com/track?tracknum={tn}",
    "fedex": "https://www.fedex.com/fedextrack/?tracknumbers={tn}",
    "packlink": "https://app.packlink.com/tracking/{tn}",
}

# Packlink service name substrings → normalized carrier key
_PACKLINK_CARRIER_MAP = {
    "gls": "gls",
    "brt": "brt",
    "bartolini": "brt",
    "sda": "sda",
    "dhl": "dhl",
    "ups": "ups",
    "fedex": "fedex",
    "poste": "poste",
    "nexive": "nexive",
    "tnt": "tnt",
    "dpd": "dpd",
}


async def handle_shipment(
    order_id: int,
    tracking_number: str,
    carrier: str,
    cfg: FoolishConfig,
    order_repo: OrderRepo,
    message_repo: MessageRepo,
) -> str:
    order = await order_repo.get(order_id)
    if order is None:
        return f"Ordine #{order_id} non trovato."
    if order.pipeline_state not in ("preview_sent", "matched"):
        return (
            f"Ordine #{order_id} è in stato '{order.pipeline_state}'. "
            "La spedizione può essere registrata solo dopo che la preview è stata inviata."
        )

    import asyncpg
    pool_url = cfg.database_url
    pool = await asyncpg.create_pool(pool_url, min_size=1, max_size=2)
    try:
        carrier_key = carrier.lower().strip()

        # When carrier is packlink, resolve real carrier + tracking via API
        display_tracking = tracking_number
        display_carrier = carrier_key
        if carrier_key == "packlink" and cfg.packlink_api_key:
            display_tracking, display_carrier = await _resolve_packlink_tracking(
                cfg, tracking_number, display_carrier
            )

        # Save tracking info (tracking_number = Packlink reference for polling;
        # display_tracking/carrier used only in the customer message)
        await pool.execute(
            """UPDATE foolish.orders
               SET tracking_number = $1, tracking_carrier = $2,
                   shipped_at = NOW(), pipeline_state = 'shipped', updated_at = NOW()
               WHERE id = $3""",
            tracking_number, carrier_key, order_id,
        )
        # Mark reserved sheets as shipped
        await pool.execute(
            """UPDATE foolish.sheets
               SET status = 'shipped',
                   shipped_in_order_id = $1,
                   reserved_for_order_id = NULL,
                   updated_at = NOW()
               WHERE reserved_for_order_id = $1""",
            order_id,
        )
        logger.info(
            "Order {} shipped: pl_ref={} carrier={} display_tracking={} display_carrier={}",
            order_id, tracking_number, carrier_key, display_tracking, display_carrier,
        )

        # Build tracking URL using resolved carrier when possible
        url_template = _CARRIER_LINKS.get(display_carrier, "https://parcelsapp.com/en/tracking/{tn}")
        tracking_url = url_template.format(tn=display_tracking)

        body = TRACKING_TEMPLATE.format(
            tracking_number=display_tracking,
            carrier=display_carrier.upper(),
            tracking_url=tracking_url,
        )

        await message_repo.create(
            order_id=order_id,
            direction="outbound",
            stage="tracking",
            body=body,
            recipient=str(order.customer_telegram_id) if order.customer_telegram_id else order.customer_email,
            approved_by_alessandro=True,  # tracking is auto-send
        )

        if order.customer_telegram_id:
            await send_to_customer(cfg, order.customer_telegram_id, body)
            await send_to_alessandro(cfg, f"✅ Tracking inviato al cliente per ordine #{order_id}.")
            return f"Spedizione registrata. Tracking {tracking_number} inviato al cliente."
        else:
            await send_to_alessandro(
                cfg,
                f"✅ Spedizione ordine #{order_id} registrata.\n"
                f"Cliente non collegato su Telegram. Invia tu il tracking:\n\n<pre>{body}</pre>",
            )
            return f"Spedizione registrata. Il cliente non ha Telegram — bozza inviata ad Alessandro."
    finally:
        await pool.close()


async def _resolve_packlink_tracking(
    cfg: "FoolishConfig",
    packlink_reference: str,
    fallback_carrier: str,
) -> tuple[str, str]:
    """Call Packlink API to get the real carrier tracking number and carrier name.

    Returns (carrier_tracking, carrier_key). Falls back to (packlink_reference, 'packlink')
    if the API call fails or data is not yet available.
    """
    try:
        from ..packlink import get_shipment
        shipment = await get_shipment(cfg.packlink_api_key, cfg.packlink_base_url, packlink_reference)

        carrier_tracking = (
            shipment.get("carrier_tracking_id")
            or shipment.get("carrier_reference")
            or shipment.get("carrierReference")
            or ""
        )

        # Try to resolve carrier name from service info
        service_name = (
            shipment.get("service_name")
            or shipment.get("service")
            or shipment.get("carrier")
            or ""
        ).lower()
        carrier_key = fallback_carrier
        for keyword, mapped in _PACKLINK_CARRIER_MAP.items():
            if keyword in service_name:
                carrier_key = mapped
                break

        if carrier_tracking:
            logger.info(
                "Packlink resolved: ref={} → tracking={} carrier={}",
                packlink_reference, carrier_tracking, carrier_key,
            )
            return carrier_tracking, carrier_key

    except Exception as exc:
        logger.warning("Packlink resolve failed for {}: {}", packlink_reference, exc)

    # Fallback: show Packlink reference with Packlink tracking link
    return packlink_reference, "packlink"
