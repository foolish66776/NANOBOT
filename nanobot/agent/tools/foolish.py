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
# Payload CMS tools
# ---------------------------------------------------------------------------


def _cms_client():
    from nanobot.business.foolish.config import get_config
    from nanobot.business.foolish.cms import PayloadClient
    cfg = get_config()
    if not cfg.cms_url:
        raise RuntimeError("FOOLISH_CMS_URL non configurata.")
    if not cfg.cms_admin_email or not cfg.cms_admin_password:
        raise RuntimeError("FOOLISH_CMS_ADMIN_EMAIL / FOOLISH_CMS_ADMIN_PASSWORD non configurate.")
    return PayloadClient(cfg.cms_url, cfg.cms_admin_email, cfg.cms_admin_password)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Filtra per sezione: 'tattoo' o 'pmu'. Ometti per tutti.",
                "enum": ["tattoo", "pmu"],
            },
            "include_inactive": {
                "type": "boolean",
                "description": "Se true mostra anche prodotti non visibili in vetrina. Default false.",
            },
        },
        "required": [],
    }
)
class FoolishGetProductsTool(Tool):
    """Elenca tutti i prodotti del catalogo Foolish Butcher con prezzi e stato stock."""

    @property
    def name(self) -> str:
        return "foolish_get_products"

    @property
    def description(self) -> str:
        return (
            "Elenca i prodotti del catalogo Foolish Butcher dal CMS. "
            "Mostra nome, sezione, varianti con prezzi e disponibilità stock. "
            "Usa per rispondere a domande su prodotti, prezzi, stock attuale."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            client = _cms_client()
            from nanobot.business.foolish.cms import _format_product_summary
            section = kwargs.get("section") or None
            active_only = not kwargs.get("include_inactive", False)
            products = await client.get_products(active_only=active_only, section=section)
            if not products:
                return "Nessun prodotto trovato nel CMS."
            lines = [_format_product_summary(p) for p in products]
            return f"Prodotti ({len(products)}):\n\n" + "\n\n".join(lines)
        except Exception as exc:
            logger.error("foolish_get_products error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "sku": {
                "type": "string",
                "description": "SKU della variante da aggiornare (es. DBL-A4, DUOSKIN-A5-PELLE).",
            },
            "slug": {
                "type": "string",
                "description": "Slug del prodotto da aggiornare (es. t-sheet-dbl). Usa sku O slug.",
            },
            "price": {
                "type": "number",
                "description": "Nuovo prezzo in €. Aggiorna solo la variante con lo SKU indicato.",
            },
            "stock_status": {
                "type": "string",
                "description": "Nuovo stato disponibilità della variante.",
                "enum": ["available", "low", "unavailable"],
            },
            "limited_stock": {
                "type": "boolean",
                "description": "Se true, sposta il prodotto nella sezione Stock Limitato.",
            },
            "active": {
                "type": "boolean",
                "description": "Se false, nasconde il prodotto dalla vetrina.",
            },
        },
        "required": [],
    }
)
class FoolishUpdateProductTool(Tool):
    """Aggiorna prezzo, stock o visibilità di un prodotto Foolish Butcher nel CMS."""

    @property
    def name(self) -> str:
        return "foolish_update_product"

    @property
    def description(self) -> str:
        return (
            "Aggiorna un prodotto nel CMS Foolish Butcher. "
            "Puoi modificare prezzo e stock di una singola variante tramite SKU, "
            "oppure la visibilità dell'intero prodotto tramite slug."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            client = _cms_client()
            from nanobot.business.foolish.cms import _format_product_summary

            sku: str = (kwargs.get("sku") or "").strip().upper()
            slug: str = (kwargs.get("slug") or "").strip()

            if not sku and not slug:
                return "Specifica 'sku' o 'slug' del prodotto da aggiornare."

            product: dict | None = None
            if slug:
                product = await client.find_product_by_slug(slug)
            elif sku:
                product = await client.find_product_by_sku(sku)

            if not product:
                return f"Prodotto non trovato (sku={sku or '—'} slug={slug or '—'})."

            update_data: dict = {}

            if "active" in kwargs:
                update_data["active"] = bool(kwargs["active"])
            if "limited_stock" in kwargs:
                update_data["limitedStock"] = bool(kwargs["limited_stock"])

            # Variant-level updates (price / stockStatus)
            if sku and ("price" in kwargs or "stock_status" in kwargs):
                variants = [dict(v) for v in (product.get("variants") or [])]
                matched = False
                for v in variants:
                    if v.get("sku", "").upper() == sku:
                        if "price" in kwargs:
                            v["price"] = float(kwargs["price"])
                        if "stock_status" in kwargs:
                            v["stockStatus"] = kwargs["stock_status"]
                        matched = True
                        break
                if not matched:
                    return f"SKU '{sku}' non trovato nelle varianti del prodotto '{product.get('name')}'."
                update_data["variants"] = variants

            if not update_data:
                return "Nessuna modifica specificata."

            updated = await client.update_product(product["id"], update_data)
            return f"✅ Prodotto aggiornato:\n\n{_format_product_summary(updated)}"

        except Exception as exc:
            logger.error("foolish_update_product error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Slug URL del prodotto (es. kit-viso-pmu). Solo minuscole e trattini.",
            },
        },
        "required": ["slug"],
    }
)
class FoolishDeactivateProductTool(Tool):
    """Nasconde un prodotto dalla vetrina senza eliminarlo."""

    @property
    def name(self) -> str:
        return "foolish_deactivate_product"

    @property
    def description(self) -> str:
        return (
            "Nasconde un prodotto dalla vetrina Foolish Butcher (active=false). "
            "Il prodotto rimane nel CMS e può essere riattivato. "
            "Usa quando un prodotto è esaurito o temporaneamente fuori produzione."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            client = _cms_client()
            slug = kwargs["slug"].strip()
            product = await client.find_product_by_slug(slug)
            if not product:
                return f"Prodotto con slug '{slug}' non trovato."
            await client.update_product(product["id"], {"active": False})
            return f"✅ Prodotto '{product.get('name')}' (`{slug}`) nascosto dalla vetrina."
        except Exception as exc:
            logger.error("foolish_deactivate_product error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "pipeline_state": {
                "type": "string",
                "description": "Filtra per stato pipeline. Ometti per tutti.",
                "enum": [
                    "received", "eta_pending", "eta_confirmed", "in_production",
                    "matching_pending", "matched", "preview_sent", "shipped",
                    "delivered", "followup_done", "closed",
                ],
            },
            "limit": {
                "type": "integer",
                "description": "Numero massimo di ordini da restituire. Default 15.",
            },
        },
        "required": [],
    }
)
class FoolishGetStorefrontOrdersTool(Tool):
    """Elenca gli ordini ricevuti dallo storefront Foolish Butcher."""

    @property
    def name(self) -> str:
        return "foolish_get_storefront_orders"

    @property
    def description(self) -> str:
        return (
            "Elenca gli ordini storefront Foolish Butcher dal CMS Payload. "
            "Mostra numero ordine, cliente, totale e stato pipeline. "
            "Usa per monitorare ordini recenti o filtrare per stato."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            client = _cms_client()
            limit = int(kwargs.get("limit") or 15)
            pipeline_state = kwargs.get("pipeline_state") or None
            orders = await client.get_orders(pipeline_state=pipeline_state, limit=limit)
            if not orders:
                return "Nessun ordine trovato."

            state_labels = {
                "received": "Ricevuto", "eta_pending": "Att. ETA",
                "eta_confirmed": "ETA ok", "in_production": "In produzione",
                "matching_pending": "Att. matching", "matched": "Abbinato",
                "preview_sent": "Preview inviata", "shipped": "Spedito",
                "delivered": "Consegnato", "followup_done": "Follow-up fatto",
                "closed": "Chiuso",
            }
            lines = []
            for o in orders:
                state = state_labels.get(o.get("pipelineState", ""), o.get("pipelineState", "?"))
                lines.append(
                    f"**#{o.get('orderNumber','?')}** — {o.get('customerName','?')} ({o.get('customerEmail','?')})\n"
                    f"  {o.get('total','?')}€ | {state} | {o.get('createdAt','')[:10]}"
                )
            return f"Ordini ({len(orders)}):\n\n" + "\n\n".join(lines)
        except Exception as exc:
            logger.error("foolish_get_storefront_orders error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "order_number": {
                "type": "string",
                "description": "Numero ordine storefront (es. FS-2026-001).",
            },
            "telegram_id": {
                "type": "integer",
                "description": "Telegram ID numerico del cliente.",
            },
        },
        "required": ["order_number", "telegram_id"],
    }
)
class FoolishLinkCustomerTelegramTool(Tool):
    """Collega il Telegram ID di un cliente a un ordine storefront nel CMS."""

    @property
    def name(self) -> str:
        return "foolish_link_customer_telegram"

    @property
    def description(self) -> str:
        return (
            "Collega il Telegram ID di un cliente a un ordine nel CMS Payload. "
            "Dopo il collegamento, le notifiche ordine vengono inviate su Telegram. "
            "Usa quando il cliente ha avviato il bot con il deep link dell'ordine."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            client = _cms_client()
            import json as _json
            order_number = str(kwargs["order_number"]).strip()
            telegram_id = int(kwargs["telegram_id"])

            orders = await client.get_orders(limit=100)
            order = next((o for o in orders if o.get("orderNumber") == order_number), None)
            if not order:
                return f"Ordine '{order_number}' non trovato nel CMS."

            await client.update_order(order["id"], {"customerTelegramId": str(telegram_id)})
            return (
                f"✅ Cliente collegato:\n"
                f"Ordine {order_number} → Telegram ID `{telegram_id}`\n"
                f"Le prossime notifiche partiranno via Telegram."
            )
        except Exception as exc:
            logger.error("foolish_link_customer_telegram error: {}", exc)
            return f"Errore: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "to_name": {"type": "string", "description": "Nome del destinatario."},
            "to_surname": {"type": "string", "description": "Cognome del destinatario."},
            "to_street": {"type": "string", "description": "Via e numero civico del destinatario."},
            "to_city": {"type": "string", "description": "Città del destinatario."},
            "to_zip": {"type": "string", "description": "CAP del destinatario."},
            "to_country": {"type": "string", "description": "Codice paese ISO 2 lettere (es. IT, DE, FR)."},
            "to_phone": {"type": "string", "description": "Telefono del destinatario (con prefisso internazionale)."},
            "to_email": {"type": "string", "description": "Email del destinatario."},
            "to_company": {"type": "string", "description": "Azienda destinatario (opzionale)."},
            "weight_kg": {"type": "number", "description": "Peso del pacco in kg (es. 0.3 per un foglio A4)."},
            "width_cm": {"type": "number", "description": "Larghezza pacco in cm. Default 30."},
            "height_cm": {"type": "number", "description": "Altezza pacco in cm. Default 5."},
            "length_cm": {"type": "number", "description": "Lunghezza pacco in cm. Default 30."},
            "content": {"type": "string", "description": "Descrizione contenuto per la dogana. Default: 'Practice skin for tattoo'."},
            "content_value": {"type": "number", "description": "Valore dichiarato in EUR. Default 25."},
            "service_id": {"type": "string", "description": "ID servizio Packlink (opzionale). Se omesso la bozza viene creata senza corriere selezionato."},
        },
        "required": ["to_name", "to_surname", "to_street", "to_city", "to_zip", "to_country", "to_phone", "to_email", "weight_kg"],
    }
)
class FoolishCreateShipmentDraftTool(Tool):
    """Crea una bozza di spedizione su Packlink Pro e restituisce il link diretto per revisione e pagamento."""

    @property
    def name(self) -> str:
        return "foolish_create_shipment_draft"

    @property
    def description(self) -> str:
        return (
            "Crea una bozza di spedizione su Packlink Pro con i dati del destinatario. "
            "La spedizione viene creata in stato 'draft' — Alessandro può rivederla e pagarla "
            "direttamente su Packlink senza che venga addebitato nulla automaticamente. "
            "Restituisce il riferimento Packlink e il link diretto alla bozza."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.packlink import create_shipment_draft

            cfg = get_config()
            if not cfg.packlink_api_key:
                return "Errore: FOOLISH_PACKLINK_API_KEY non configurata."

            result = await create_shipment_draft(
                cfg.packlink_api_key,
                cfg.packlink_base_url,
                from_name=cfg.packlink_sender_name,
                from_surname=cfg.packlink_sender_surname,
                from_company=cfg.packlink_sender_company,
                from_street=cfg.packlink_sender_street,
                from_city=cfg.packlink_sender_city,
                from_zip=cfg.packlink_sender_zip,
                from_country=cfg.packlink_sender_country,
                from_phone=cfg.packlink_sender_phone,
                from_email=cfg.packlink_sender_email,
                to_name=kwargs["to_name"],
                to_surname=kwargs["to_surname"],
                to_street=kwargs["to_street"],
                to_city=kwargs["to_city"],
                to_zip=kwargs["to_zip"],
                to_country=kwargs["to_country"],
                to_phone=kwargs["to_phone"],
                to_email=kwargs["to_email"],
                to_company=kwargs.get("to_company", ""),
                weight_kg=float(kwargs["weight_kg"]),
                width_cm=float(kwargs.get("width_cm", 30)),
                height_cm=float(kwargs.get("height_cm", 5)),
                length_cm=float(kwargs.get("length_cm", 30)),
                content=kwargs.get("content", "Practice skin for tattoo"),
                content_value=float(kwargs.get("content_value", 25.0)),
                service_id=kwargs.get("service_id", ""),
            )

            ref = result["reference"]
            url = result["dashboard_url"]
            status = result["status"]

            lines = [
                f"✅ Bozza spedizione creata su Packlink.",
                f"Riferimento: `{ref}`",
                f"Stato: {status}",
                f"Destinatario: {kwargs['to_name']} {kwargs['to_surname']}, {kwargs['to_city']} ({kwargs['to_country']})",
            ]
            if url:
                lines.append(f"👉 Rivedi e paga: {url}")
            else:
                lines.append("Apri Packlink Pro per completare e pagare la spedizione.")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("foolish_create_shipment_draft error: {}", exc)
            return f"Errore creazione bozza Packlink: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "to_country": {"type": "string", "description": "Codice paese ISO destinazione (es. IT, DE, FR)."},
            "to_zip": {"type": "string", "description": "CAP di destinazione."},
            "weight_kg": {"type": "number", "description": "Peso del pacco in kg."},
            "width_cm": {"type": "number", "description": "Larghezza in cm. Default 30."},
            "height_cm": {"type": "number", "description": "Altezza in cm. Default 5."},
            "length_cm": {"type": "number", "description": "Lunghezza in cm. Default 30."},
        },
        "required": ["to_country", "to_zip", "weight_kg"],
    }
)
class FoolishGetShippingServicesTool(Tool):
    """Elenca i servizi di spedizione Packlink disponibili con prezzi per una destinazione e peso."""

    @property
    def name(self) -> str:
        return "foolish_get_shipping_services"

    @property
    def description(self) -> str:
        return (
            "Elenca corrieri e prezzi disponibili su Packlink Pro per una spedizione "
            "dalla sede Foolish verso una destinazione, dato il peso del pacco. "
            "Utile per mostrare ad Alessandro le opzioni prima di creare la bozza."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from nanobot.business.foolish.config import get_config
            from nanobot.business.foolish.packlink import get_available_services

            cfg = get_config()
            if not cfg.packlink_api_key:
                return "Errore: FOOLISH_PACKLINK_API_KEY non configurata."

            services = await get_available_services(
                cfg.packlink_api_key,
                cfg.packlink_base_url,
                from_country=cfg.packlink_sender_country,
                from_zip=cfg.packlink_sender_zip,
                to_country=kwargs["to_country"],
                to_zip=kwargs["to_zip"],
                weight_kg=float(kwargs["weight_kg"]),
                width_cm=float(kwargs.get("width_cm", 30)),
                height_cm=float(kwargs.get("height_cm", 5)),
                length_cm=float(kwargs.get("length_cm", 30)),
            )

            if not services:
                return "Nessun servizio disponibile per questa tratta."

            lines = [f"Servizi disponibili ({kwargs['to_country']} {kwargs['to_zip']}, {kwargs['weight_kg']}kg):\n"]
            for s in services[:10]:
                price = f"€{s['price']:.2f}" if s["price"] else "N/D"
                days = f", {s['transit_days']} gg" if s["transit_days"] else ""
                lines.append(f"• [{s['id']}] {s['name']} — {price}{days}")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("foolish_get_shipping_services error: {}", exc)
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
