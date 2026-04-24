"""Foolish Butcher agent tools — sheet registration and order queries."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "description": "Formato del foglio: A5, A4, XXL, AlexHand, DuoSkin, ecc.",
            },
            "flock_density": {
                "type": "string",
                "description": "Densità flock: low, medium o high.",
                "enum": ["low", "medium", "high"],
            },
            "flock_color_notes": {
                "type": "string",
                "description": "Note libere sulle caratteristiche uniche del foglio (discromie, difetti, colori).",
            },
            "serial_code": {
                "type": "string",
                "description": "Codice seriale opzionale (es. F25-A4-042). Se omesso, viene auto-generato.",
            },
            "produced_at": {
                "type": "string",
                "description": "Data di produzione in formato YYYY-MM-DD. Default: oggi.",
            },
            "sku_ref": {
                "type": "string",
                "description": "SKU WooCommerce di riferimento, se noto.",
            },
        },
        "required": ["format", "flock_density"],
    }
)
class FoolishRegisterSheetTool(Tool):
    """Registra un foglio fisico appena prodotto da Alessandro nel database Foolish Butcher."""

    @property
    def name(self) -> str:
        return "foolish_register_sheet"

    @property
    def description(self) -> str:
        return (
            "Registra un foglio fisico (practice skin) nel database Foolish Butcher. "
            "Chiamare solo dopo aver confermato i dettagli con Alessandro. "
            "Restituisce il codice seriale assegnato."
        )

    async def execute(self, **kwargs: Any) -> str:
        db_url = os.environ.get("FOOLISH_DATABASE_URL") or os.environ.get("NANOBOT_MEMORY_DATABASE_URL")
        if not db_url:
            return "Errore: FOOLISH_DATABASE_URL non configurato."

        fmt: str = kwargs["format"].strip().upper()
        flock_density: str = kwargs["flock_density"].lower()
        flock_color_notes: str = kwargs.get("flock_color_notes", "") or ""
        serial_code: str = kwargs.get("serial_code", "") or ""
        produced_at_str: str = kwargs.get("produced_at", "") or ""
        sku_ref: str = kwargs.get("sku_ref", "") or ""

        try:
            import asyncpg

            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
            try:
                today = date.today()
                produced_at = _parse_date(produced_at_str) if produced_at_str else today

                if not serial_code:
                    serial_code = await _generate_serial(pool, fmt, produced_at)

                await pool.execute(
                    """
                    INSERT INTO foolish.sheets
                        (serial_code, produced_at, format, flock_density, flock_color_notes, sku_ref)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    serial_code,
                    produced_at,
                    fmt,
                    flock_density,
                    flock_color_notes or None,
                    sku_ref or None,
                )
                logger.info("Foolish sheet registered: {}", serial_code)
                return (
                    f"✅ Foglio registrato: **{serial_code}**\n"
                    f"Formato: {fmt} | Flock: {flock_density}"
                    + (f" | Note: {flock_color_notes}" if flock_color_notes else "")
                    + f"\nData produzione: {produced_at}\n\n"
                    "Mandami le foto quando sei pronto — le allego al foglio."
                )
            finally:
                await pool.close()

        except Exception as exc:
            logger.error("foolish_register_sheet error: {}", exc)
            return f"Errore nella registrazione: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "integer",
                "description": "ID dell'ordine WooCommerce da matchare con i fogli disponibili.",
            },
        },
        "required": ["order_id"],
    }
)
class FoolishProposeMatchingTool(Tool):
    """Propone il matching fogli→ordine ad Alessandro via Telegram con bottoni approve/reject."""

    @property
    def name(self) -> str:
        return "foolish_propose_matching"

    @property
    def description(self) -> str:
        return (
            "Avvia la proposta di matching per un ordine Foolish Butcher: "
            "cerca i fogli in_stock compatibili con gli articoli dell'ordine e manda la proposta ad Alessandro su Telegram. "
            "Alessandro approva o rifiuta con i bottoni inline."
        )

    async def execute(self, **kwargs: Any) -> str:
        db_url = os.environ.get("FOOLISH_DATABASE_URL") or os.environ.get("NANOBOT_MEMORY_DATABASE_URL")
        if not db_url:
            return "Errore: FOOLISH_DATABASE_URL non configurato."
        order_id = int(kwargs["order_id"])
        try:
            import asyncpg
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.pipeline.matching import propose_matching

            cfg = get_config()
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
            try:
                return await propose_matching(order_id, cfg, pool)
            finally:
                await pool.close()
        except Exception as exc:
            logger.error("foolish_propose_matching error: {}", exc)
            return f"Errore nel matching: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "integer",
                "description": "ID dell'ordine WooCommerce.",
            },
            "tracking_number": {
                "type": "string",
                "description": "Numero di tracking del corriere.",
            },
            "carrier": {
                "type": "string",
                "description": "Nome corriere: gls, brt, sda, poste, dhl, ups, fedex, packlink, ecc.",
            },
        },
        "required": ["order_id", "tracking_number", "carrier"],
    }
)
class FoolishRegisterShipmentTool(Tool):
    """Registra la spedizione di un ordine Foolish Butcher e notifica il cliente con il tracking."""

    @property
    def name(self) -> str:
        return "foolish_register_shipment"

    @property
    def description(self) -> str:
        return (
            "Registra la spedizione di un ordine Foolish Butcher. "
            "Salva tracking number e corriere, marca i fogli come spediti, "
            "invia il messaggio di tracking al cliente via Telegram."
        )

    async def execute(self, **kwargs: Any) -> str:
        db_url = os.environ.get("FOOLISH_DATABASE_URL") or os.environ.get("NANOBOT_MEMORY_DATABASE_URL")
        if not db_url:
            return "Errore: FOOLISH_DATABASE_URL non configurato."
        try:
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.db import OrderRepo, MessageRepo, get_pool
            from nanobot.business.foolish.pipeline.shipment import handle_shipment

            cfg = get_config()
            import asyncpg
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
            try:
                order_repo = OrderRepo(pool)
                message_repo = MessageRepo(pool)
                return await handle_shipment(
                    int(kwargs["order_id"]),
                    kwargs["tracking_number"],
                    kwargs["carrier"],
                    cfg, order_repo, message_repo,
                )
            finally:
                await pool.close()
        except Exception as exc:
            logger.error("foolish_register_shipment error: {}", exc)
            return f"Errore nella registrazione spedizione: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filtra per stato: in_stock, reserved, shipped, defective. Ometti per tutti.",
            },
            "format": {
                "type": "string",
                "description": "Filtra per formato (es. A4, A5, XXL).",
            },
        },
        "required": [],
    }
)
class FoolishQuerySheetsTool(Tool):
    """Elenca i fogli in magazzino con filtri opzionali."""

    @property
    def name(self) -> str:
        return "foolish_query_sheets"

    @property
    def description(self) -> str:
        return "Elenca i fogli Foolish Butcher in magazzino. Utile per vedere cosa è disponibile prima di fare matching."

    async def execute(self, **kwargs: Any) -> str:
        db_url = os.environ.get("FOOLISH_DATABASE_URL") or os.environ.get("NANOBOT_MEMORY_DATABASE_URL")
        if not db_url:
            return "Errore: FOOLISH_DATABASE_URL non configurato."

        status_filter = kwargs.get("status") or None
        format_filter = (kwargs.get("format") or "").strip().upper() or None

        try:
            import asyncpg

            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
            try:
                query = "SELECT serial_code, format, flock_density, flock_color_notes, status, produced_at FROM foolish.sheets WHERE 1=1"
                params: list = []
                if status_filter:
                    params.append(status_filter)
                    query += f" AND status = ${len(params)}"
                if format_filter:
                    params.append(format_filter)
                    query += f" AND format = ${len(params)}"
                query += " ORDER BY produced_at DESC LIMIT 50"

                rows = await pool.fetch(query, *params)
                if not rows:
                    return "Nessun foglio trovato con i filtri specificati."

                lines = [f"**{r['serial_code']}** — {r['format']} | flock {r['flock_density']} | {r['status']} | {r['produced_at']}"
                         + (f"\n  ↳ {r['flock_color_notes']}" if r['flock_color_notes'] else "")
                         for r in rows]
                return f"Fogli trovati ({len(rows)}):\n\n" + "\n".join(lines)
            finally:
                await pool.close()

        except Exception as exc:
            logger.error("foolish_query_sheets error: {}", exc)
            return f"Errore nella query: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {},
        "required": [],
    }
)
class FoolishPacklinkSetupTool(Tool):
    """Registra il webhook Packlink Pro sull'account API e mostra lo stato attuale."""

    @property
    def name(self) -> str:
        return "foolish_packlink_setup_webhook"

    @property
    def description(self) -> str:
        return (
            "Registra il webhook Packlink Pro (tracking events) sul nostro endpoint pubblico. "
            "Usa dopo aver impostato FOOLISH_PACKLINK_API_KEY e FOOLISH_WEBHOOK_BASE_URL. "
            "Mostra anche i webhook già registrati."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.packlink import list_webhooks, register_webhook

            cfg = get_config()
            if not cfg.packlink_api_key:
                return "Errore: FOOLISH_PACKLINK_API_KEY non configurata."
            if not cfg.webhook_base_url:
                return "Errore: FOOLISH_WEBHOOK_BASE_URL non configurata (es. https://xxx.railway.app)."

            callback_url = f"{cfg.webhook_base_url}/hooks/packlink"

            # Show existing hooks first
            try:
                existing = await list_webhooks(cfg.packlink_api_key, cfg.packlink_base_url)
                existing_urls = [h.get("url", "") for h in existing] if existing else []
            except Exception as exc:
                existing_urls = []
                logger.warning("Packlink list_webhooks failed: {}", exc)

            # Register new hooks
            results = await register_webhook(cfg.packlink_api_key, cfg.packlink_base_url, callback_url)

            all_failed = all(r["status"] != "ok" for r in results)

            lines = [f"**Webhook URL target:** `{callback_url}`\n"]
            if existing_urls:
                lines.append("**Già registrati:**")
                lines.extend(f"  • {u}" for u in existing_urls)
                lines.append("")
            lines.append("**Registrazione eventi:**")
            for r in results:
                icon = "✅" if r["status"] == "ok" else "❌"
                path_info = f" (via {r['path']})" if r.get("path") else ""
                detail = f" — {r.get('code', r.get('error', ''))}" if r["status"] != "ok" else ""
                lines.append(f"  {icon} {r['event']}{path_info}{detail}")

            if all_failed:
                lines += [
                    "",
                    "⚠️ **Registrazione API non riuscita.** Configura manualmente nel dashboard Packlink Pro:",
                    f"  Settings → Integrations → Webhooks → Add URL: `{callback_url}`",
                    "  Seleziona tutti gli eventi di tracking disponibili.",
                ]

            return "\n".join(lines)

        except Exception as exc:
            logger.error("foolish_packlink_setup_webhook error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "Riferimento Packlink della spedizione (es. ES123456789012345678).",
            },
        },
        "required": ["reference"],
    }
)
class FoolishPacklinkGetLabelTool(Tool):
    """Recupera il link PDF dell'etichetta di spedizione da Packlink Pro."""

    @property
    def name(self) -> str:
        return "foolish_packlink_get_label"

    @property
    def description(self) -> str:
        return (
            "Recupera l'URL del PDF dell'etichetta Packlink per un riferimento di spedizione. "
            "Utile quando Alessandro ha creato la spedizione su Packlink e vuole il link diretto all'etichetta."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.packlink import get_label_url, get_shipment

            cfg = get_config()
            if not cfg.packlink_api_key:
                return "Errore: FOOLISH_PACKLINK_API_KEY non configurata."

            reference = kwargs["reference"].strip()
            shipment = await get_shipment(cfg.packlink_api_key, cfg.packlink_base_url, reference)

            carrier_tracking = (
                shipment.get("carrier_tracking_id")
                or shipment.get("carrier_reference")
                or shipment.get("trackingCode")
                or "N/D"
            )
            state = shipment.get("state") or shipment.get("status") or "N/D"
            if isinstance(state, dict):
                state = state.get("description") or state.get("slug") or str(state)

            label_url = await get_label_url(cfg.packlink_api_key, cfg.packlink_base_url, reference)

            lines = [
                f"**Packlink ref:** `{reference}`",
                f"**Carrier tracking:** `{carrier_tracking}`",
                f"**Stato:** {state}",
            ]
            if label_url:
                lines.append(f"**Etichetta PDF:** {label_url}")
            else:
                lines.append("**Etichetta:** non ancora disponibile.")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("foolish_packlink_get_label error: {}", exc)
            return f"Errore: {exc}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _generate_serial(pool, fmt: str, produced_at: date) -> str:
    yy = str(produced_at.year)[2:]
    fmt_slug = re.sub(r"[^A-Z0-9]", "", fmt.upper())
    prefix = f"F{yy}-{fmt_slug}-"
    row = await pool.fetchrow(
        "SELECT MAX(serial_code) as mx FROM foolish.sheets WHERE serial_code LIKE $1",
        f"{prefix}%",
    )
    mx = row["mx"] if row else None
    if mx:
        try:
            last_num = int(mx.split("-")[-1])
        except ValueError:
            last_num = 0
    else:
        last_num = 0
    return f"{prefix}{last_num + 1:03d}"


def _parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()
