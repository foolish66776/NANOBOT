"""Apply foolish schema to Postgres. Run once on first deploy and after schema changes."""

import asyncio
import os
import sys

import asyncpg


SQL = """
CREATE SCHEMA IF NOT EXISTS foolish;

CREATE TABLE IF NOT EXISTS foolish.sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    serial_code TEXT UNIQUE NOT NULL,
    produced_at DATE NOT NULL,
    format TEXT NOT NULL,
    sku_ref TEXT,
    flock_density TEXT CHECK (flock_density IN ('low','medium','high')),
    flock_color_notes TEXT,
    status TEXT NOT NULL DEFAULT 'in_stock'
        CHECK (status IN ('in_stock','reserved','shipped','defective')),
    photo_urls TEXT[] DEFAULT ARRAY[]::TEXT[],
    reserved_for_order_id BIGINT,
    shipped_in_order_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sheets_status ON foolish.sheets(status);
CREATE INDEX IF NOT EXISTS idx_sheets_format ON foolish.sheets(format);
CREATE INDEX IF NOT EXISTS idx_sheets_reserved ON foolish.sheets(reserved_for_order_id);

CREATE TABLE IF NOT EXISTS foolish.orders (
    id BIGINT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    customer_name TEXT,
    customer_telegram_id BIGINT,
    line_items JSONB NOT NULL,
    total NUMERIC(10,2),
    currency TEXT DEFAULT 'EUR',
    pipeline_state TEXT NOT NULL DEFAULT 'received'
        CHECK (pipeline_state IN (
            'received','eta_pending','eta_confirmed','in_production',
            'matching_pending','matched','preview_sent','shipped',
            'delivered','followup_done','closed'
        )),
    production_eta_days INTEGER,
    tracking_number TEXT,
    tracking_carrier TEXT,
    shipped_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    followup_scheduled_at TIMESTAMPTZ,
    followup_sent_at TIMESTAMPTZ,
    proposed_sheet_ids UUID[] DEFAULT ARRAY[]::UUID[],
    raw_webhook JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_state ON foolish.orders(pipeline_state);
CREATE INDEX IF NOT EXISTS idx_orders_customer_email ON foolish.orders(customer_email);
CREATE INDEX IF NOT EXISTS idx_orders_followup ON foolish.orders(followup_scheduled_at)
    WHERE followup_sent_at IS NULL;

CREATE TABLE IF NOT EXISTS foolish.messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id BIGINT REFERENCES foolish.orders(id),
    direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
    channel TEXT NOT NULL DEFAULT 'telegram',
    recipient TEXT,
    stage TEXT NOT NULL,
    body TEXT NOT NULL,
    media_urls TEXT[] DEFAULT ARRAY[]::TEXT[],
    approved_by_alessandro BOOLEAN DEFAULT NULL,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_order ON foolish.messages(order_id);
CREATE INDEX IF NOT EXISTS idx_messages_approval ON foolish.messages(approved_by_alessandro)
    WHERE approved_by_alessandro IS NULL AND sent_at IS NULL;

CREATE TABLE IF NOT EXISTS foolish.order_sheets (
    order_id BIGINT NOT NULL REFERENCES foolish.orders(id),
    sheet_id UUID NOT NULL REFERENCES foolish.sheets(id),
    allocated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (order_id, sheet_id)
);

CREATE OR REPLACE FUNCTION foolish.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'sheets_updated_at'
    ) THEN
        CREATE TRIGGER sheets_updated_at
            BEFORE UPDATE ON foolish.sheets
            FOR EACH ROW EXECUTE FUNCTION foolish.touch_updated_at();
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'orders_updated_at'
    ) THEN
        CREATE TRIGGER orders_updated_at
            BEFORE UPDATE ON foolish.orders
            FOR EACH ROW EXECUTE FUNCTION foolish.touch_updated_at();
    END IF;
END $$;
"""


async def migrate(db_url: str) -> None:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(SQL)
        print("Migration complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    url = os.environ.get("FOOLISH_DATABASE_URL") or os.environ.get("NANOBOT_MEMORY_DATABASE_URL")
    if not url:
        print("ERROR: set FOOLISH_DATABASE_URL or NANOBOT_MEMORY_DATABASE_URL", file=sys.stderr)
        sys.exit(1)
    asyncio.run(migrate(url))
