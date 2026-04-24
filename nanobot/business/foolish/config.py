"""Env-driven config for The Foolish Butcher pipeline."""

from __future__ import annotations

import os
from functools import lru_cache


class FoolishConfig:
    database_url: str
    telegram_bot_token: str
    alessandro_chat_id: int
    woo_base_url: str
    woo_consumer_key: str
    woo_consumer_secret: str
    woo_webhook_secret: str
    trust_threshold_date: str
    followup_delay_days: int
    webhook_port: int
    r2_endpoint: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_public_url: str

    def __init__(self) -> None:
        self.database_url = _require("FOOLISH_DATABASE_URL")
        self.telegram_bot_token = _require("FOOLISH_TELEGRAM_BOT_TOKEN")
        self.alessandro_chat_id = int(_require("FOOLISH_ALESSANDRO_CHAT_ID"))
        self.woo_base_url = os.environ.get("FOOLISH_WOO_BASE_URL", "https://thefoolishbutcher.com").rstrip("/")
        self.woo_consumer_key = os.environ.get("FOOLISH_WOO_CONSUMER_KEY", "")
        self.woo_consumer_secret = os.environ.get("FOOLISH_WOO_CONSUMER_SECRET", "")
        self.woo_webhook_secret = os.environ.get("FOOLISH_WOO_WEBHOOK_SECRET", "")
        self.trust_threshold_date = os.environ.get("FOOLISH_TRUST_THRESHOLD_DATE", "2099-01-01")
        self.followup_delay_days = int(os.environ.get("FOOLISH_FOLLOWUP_DELAY_DAYS", "3"))
        self.webhook_port = int(os.environ.get("FOOLISH_WEBHOOK_PORT") or os.environ.get("PORT") or "8910")
        self.r2_endpoint = os.environ.get("FOOLISH_R2_ENDPOINT", "")
        self.r2_bucket = os.environ.get("FOOLISH_R2_BUCKET", "")
        self.r2_access_key_id = os.environ.get("FOOLISH_R2_ACCESS_KEY_ID", "")
        self.r2_secret_access_key = os.environ.get("FOOLISH_R2_SECRET_ACCESS_KEY", "")
        self.r2_public_url = os.environ.get("FOOLISH_R2_PUBLIC_URL", "")


def _require(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


@lru_cache(maxsize=1)
def get_config() -> FoolishConfig:
    return FoolishConfig()
