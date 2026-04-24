"""Pipeline: propose sheet-to-order matching → Alessandro approval → reserve sheets."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from loguru import logger

from ..config import FoolishConfig
from ..db import MessageRepo, OrderRepo
from ..models import InvalidStateTransition
from ..telegram import send_to_alessandro


async def propose_matching(
    order_id: int,
    cfg: FoolishConfig,
    pool: asyncpg.Pool,
) -> str:
    """Query available sheets matching the order's line items and send proposal to Alessandro."""
    order_repo = OrderRepo(pool)
    order = await order_repo.get(order_id)
    if order is None:
        return f"Ordine #{order_id} non trovato."

    if order.pipeline_state not in ("in_production", "matching_pending"):
        return (
            f"Ordine #{order_id} è in stato '{order.pipeline_state}'. "
            "Il matching è disponibile solo da 'in_production' o 'matching_pending'."
        )

    # Extract required formats from line items
    required = _parse_line_item_formats(order.line_items)
    if not required:
        return f"Impossibile estrarre i formati richiesti dagli articoli dell'ordine #{order_id}."

    # Query available sheets per format
    proposals: list[dict[str, Any]] = []
    missing_formats: list[str] = []

    for fmt, qty in required.items():
        rows = await pool.fetch(
            """
            SELECT id, serial_code, format, flock_density, flock_color_notes, produced_at
            FROM foolish.sheets
            WHERE status = 'in_stock' AND UPPER(format) = UPPER($1)
            ORDER BY produced_at ASC
            LIMIT $2
            """,
            fmt, qty,
        )
        if len(rows) < qty:
            missing_formats.append(f"{fmt} (serve {qty}, disponibili {len(rows)})")
        for r in rows:
            proposals.append(dict(r))

    if not proposals:
        msg = (
            f"⚠️ Nessun foglio disponibile per l'ordine #{order_id}.\n"
            f"Formati richiesti: {', '.join(f'{q}× {f}' for f, q in required.items())}\n"
            "Produci i fogli mancanti e riprova."
        )
        await send_to_alessandro(cfg, msg)
        return "Nessun foglio disponibile — notifica inviata ad Alessandro."

    # Store proposal in DB
    sheet_ids = [r["id"] for r in proposals]
    await pool.execute(
        "UPDATE foolish.orders SET proposed_sheet_ids = $1, pipeline_state = 'matching_pending', updated_at = NOW() WHERE id = $2",
        sheet_ids, order_id,
    )
    logger.info("Order {} matching proposal: {} sheets", order_id, len(proposals))

    # Compose Telegram message
    sheets_text = "\n".join(
        f"  • <b>{r['serial_code']}</b> — {r['format']} | flock {r['flock_density']}"
        + (f"\n    ↳ {r['flock_color_notes']}" if r.get('flock_color_notes') else "")
        for r in proposals
    )

    warnings = ""
    if missing_formats:
        warnings = f"\n\n⚠️ Formati insufficienti: {', '.join(missing_formats)}"

    alert = (
        f"📦 <b>Proposta matching — ordine #{order_id}</b>\n"
        f"Cliente: {order.customer_name or order.customer_email}\n\n"
        f"<b>Fogli proposti:</b>\n{sheets_text}{warnings}\n\n"
        "Approvi questa allocazione?"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Approva", "callback_data": f"match:{order_id}:approve"},
                {"text": "❌ Rifiuta", "callback_data": f"match:{order_id}:reject"},
            ]
        ]
    }
    await send_to_alessandro(cfg, alert, reply_markup=keyboard)
    return f"Proposta matching inviata ad Alessandro per ordine #{order_id} ({len(proposals)} fogli)."


async def confirm_matching(
    order_id: int,
    cfg: FoolishConfig,
    pool: asyncpg.Pool,
) -> None:
    """Approve matching: reserve sheets, create order_sheets rows, advance order state."""
    order_repo = OrderRepo(pool)
    order = await order_repo.get(order_id)
    if order is None or not order.pipeline_state == "matching_pending":
        logger.warning("confirm_matching called on order {} in state {}", order_id, order.pipeline_state if order else "N/A")
        return

    # Get proposed sheet ids from DB
    row = await pool.fetchrow("SELECT proposed_sheet_ids FROM foolish.orders WHERE id = $1", order_id)
    sheet_ids: list[UUID] = row["proposed_sheet_ids"] if row else []
    if not sheet_ids:
        await send_to_alessandro(cfg, f"⚠️ Nessun foglio nella proposta per ordine #{order_id}. Rilancia il matching.")
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Mark sheets reserved
            await conn.execute(
                "UPDATE foolish.sheets SET status = 'reserved', reserved_for_order_id = $1, updated_at = NOW() WHERE id = ANY($2)",
                order_id, sheet_ids,
            )
            # Insert order_sheets junction
            await conn.executemany(
                "INSERT INTO foolish.order_sheets (order_id, sheet_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                [(order_id, sid) for sid in sheet_ids],
            )
            # Advance state
            await conn.execute(
                "UPDATE foolish.orders SET pipeline_state = 'matched', proposed_sheet_ids = ARRAY[]::UUID[], updated_at = NOW() WHERE id = $1",
                order_id,
            )

    logger.info("Order {} matched: {} sheets reserved", order_id, len(sheet_ids))

    # Compose and send pre-shipment preview to Alessandro for approval
    sheet_rows = await pool.fetch(
        "SELECT serial_code, format, flock_density, flock_color_notes FROM foolish.sheets WHERE id = ANY($1)",
        sheet_ids,
    )
    await _send_preview_draft(order_id, order, sheet_rows, cfg, pool)
    await order_repo.update_state(order_id, "preview_sent")


async def reject_matching(
    order_id: int,
    cfg: FoolishConfig,
    pool: asyncpg.Pool,
) -> None:
    """Reject matching: clear proposal, return order to in_production."""
    await pool.execute(
        "UPDATE foolish.orders SET pipeline_state = 'in_production', proposed_sheet_ids = ARRAY[]::UUID[], updated_at = NOW() WHERE id = $1",
        order_id,
    )
    await send_to_alessandro(
        cfg,
        f"🔄 Matching ordine #{order_id} rifiutato. Ordine riportato in 'in_production'.\n"
        "Produci altri fogli e rilancia il matching quando sei pronto.",
    )
    logger.info("Order {} matching rejected, back to in_production", order_id)


async def _send_preview_draft(
    order_id: int,
    order: Any,
    sheet_rows: list,
    cfg: FoolishConfig,
    pool: asyncpg.Pool,
) -> None:
    """Compose pre-shipment preview and send to Alessandro for approval."""
    first_name = (order.customer_name or "").split()[0] if order.customer_name else "cliente"
    sheets_text = "\n".join(
        f"— {r['format']} (serie {r['serial_code']}):\n  {r['flock_color_notes'] or 'nessuna nota'}"
        for r in sheet_rows
    )
    preview_body = (
        f"{first_name}, ecco cosa ti sto per spedire.\n\n"
        f"{sheets_text}\n\n"
        "Domani parte. Ti mando il tracking appena è in viaggio."
    )

    draft_alert = (
        f"✉️ <b>Bozza preview pre-spedizione</b> — ordine #{order_id}\n\n"
        f"<pre>{preview_body}</pre>\n\n"
        "Questo messaggio verrà inviato al cliente. Approvi?"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Invia al cliente", "callback_data": f"preview:{order_id}:approve"},
                {"text": "✏️ Modifica", "callback_data": f"preview:{order_id}:edit"},
            ]
        ]
    }
    # Store preview body in messages table pending approval
    await pool.execute(
        """
        INSERT INTO foolish.messages (order_id, direction, stage, body, recipient, approved_by_alessandro)
        VALUES ($1, 'outbound', 'preview', $2, $3, NULL)
        """,
        order_id,
        preview_body,
        str(order.customer_telegram_id) if order.customer_telegram_id else order.customer_email,
    )
    await send_to_alessandro(cfg, draft_alert, reply_markup=keyboard)


def _parse_line_item_formats(line_items: list[dict]) -> dict[str, int]:
    """Extract {format: quantity} from WooCommerce line items.

    Tries to match known format keywords in product name or SKU.
    Falls back to treating the product name as the format.
    """
    KNOWN_FORMATS = ["XXL", "AlexHand", "DuoSkin", "A4", "A5", "A3"]
    result: dict[str, int] = {}
    for item in line_items:
        name = (item.get("name") or "").upper()
        sku = (item.get("sku") or "").upper()
        qty = int(item.get("quantity") or 1)
        fmt = None
        for kf in KNOWN_FORMATS:
            if kf.upper() in name or kf.upper() in sku:
                fmt = kf
                break
        if fmt is None:
            # Use first word of product name as format fallback
            words = (item.get("name") or "").split()
            fmt = words[0] if words else "UNKNOWN"
        result[fmt] = result.get(fmt, 0) + qty
    return result
