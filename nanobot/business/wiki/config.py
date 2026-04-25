"""Wiki business line configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WikiConfig:
    vault_path: Path
    raw_path: Path
    synthesis_trigger_interval: int  # ingest count before proposing synthesis
    lint_stale_days: int             # days before a low-confidence belief is flagged

    @classmethod
    def from_env(cls) -> "WikiConfig":
        return cls(
            vault_path=Path(os.environ.get("WIKI_VAULT_PATH", "~/wiki")).expanduser(),
            raw_path=Path(os.environ.get("WIKI_RAW_PATH", "~/wiki-raw")).expanduser(),
            synthesis_trigger_interval=int(os.environ.get("WIKI_SYNTHESIS_TRIGGER_INTERVAL", "20")),
            lint_stale_days=int(os.environ.get("WIKI_LINT_STALE_DAYS", "30")),
        )


_config: WikiConfig | None = None


def get_config() -> WikiConfig:
    global _config
    if _config is None:
        _config = WikiConfig.from_env()
    return _config
