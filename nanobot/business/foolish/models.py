"""Pydantic models for The Foolish Butcher domain objects."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class Sheet(BaseModel):
    id: UUID
    serial_code: str
    produced_at: date
    format: str
    sku_ref: str | None = None
    flock_density: str | None = None
    flock_color_notes: str | None = None
    status: str = "in_stock"
    photo_urls: list[str] = []
    reserved_for_order_id: int | None = None
    shipped_in_order_id: int | None = None
    created_at: datetime
    updated_at: datetime


class Order(BaseModel):
    id: int
    customer_email: str
    customer_name: str | None = None
    customer_telegram_id: int | None = None
    line_items: list[dict[str, Any]]
    total: float | None = None
    currency: str = "EUR"
    pipeline_state: str = "received"
    production_eta_days: int | None = None
    tracking_number: str | None = None
    tracking_carrier: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    followup_scheduled_at: datetime | None = None
    followup_sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    id: UUID
    order_id: int | None = None
    direction: str
    channel: str = "telegram"
    recipient: str | None = None
    stage: str
    body: str
    media_urls: list[str] = []
    approved_by_alessandro: bool | None = None
    sent_at: datetime | None = None
    created_at: datetime


# Valid FSM transitions (§9 of CLAUDE.md)
VALID_TRANSITIONS: dict[str, list[str]] = {
    "received": ["eta_pending"],
    "eta_pending": ["eta_confirmed", "received"],
    "eta_confirmed": ["in_production"],
    "in_production": ["matching_pending"],
    "matching_pending": ["matched", "in_production"],
    "matched": ["preview_sent"],
    "preview_sent": ["shipped"],
    "shipped": ["delivered"],
    "delivered": ["followup_done"],
    "followup_done": ["closed"],
}


class InvalidStateTransition(Exception):
    pass
