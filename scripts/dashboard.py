#!/usr/bin/env python3
"""Apre il Nanobot Dashboard sul gateway Railway nel browser.

Uso:
    python3 scripts/dashboard.py
    python3 scripts/dashboard.py --url https://nanobot-gateway-production.up.railway.app --token <TOKEN>

Il token viene letto da $NANOBOT_DASHBOARD_TOKEN se non passato come argomento.
"""

from __future__ import annotations

import argparse
import os
import webbrowser

DEFAULT_URL = "https://nanobot-gateway-production.up.railway.app"


def main():
    parser = argparse.ArgumentParser(description="Apri Nanobot Dashboard")
    parser.add_argument("--url", default=os.environ.get("NANOBOT_GATEWAY_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("NANOBOT_DASHBOARD_TOKEN", ""))
    args = parser.parse_args()

    url = args.url.rstrip("/") + "/dashboard"
    if args.token:
        url += f"?token={args.token}"

    print(f"🤖 Apertura dashboard: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    main()
