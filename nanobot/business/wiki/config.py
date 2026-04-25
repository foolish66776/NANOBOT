"""Wiki business line configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WikiConfig:
    vault_path: Path
    raw_path: Path
    synthesis_trigger_interval: int  # ingest count before proposing synthesis
    lint_stale_days: int             # days before a low-confidence belief is flagged
    # Git / Obsidian sync
    git_repo: Optional[str] = None
    git_ssh_key: Optional[str] = None
    git_author_name: str = "nanobot"
    git_author_email: str = "nanobot@concr3tica.it"
    git_auto_push: bool = False

    @classmethod
    def from_env(cls) -> "WikiConfig":
        return cls(
            vault_path=Path(os.environ.get("WIKI_VAULT_PATH", "~/wiki")).expanduser(),
            raw_path=Path(os.environ.get("WIKI_RAW_PATH", "~/wiki-raw")).expanduser(),
            synthesis_trigger_interval=int(os.environ.get("WIKI_SYNTHESIS_TRIGGER_INTERVAL", "20")),
            lint_stale_days=int(os.environ.get("WIKI_LINT_STALE_DAYS", "30")),
            git_repo=os.environ.get("WIKI_GIT_REPO") or None,
            git_ssh_key=os.environ.get("WIKI_GIT_SSH_KEY") or None,
            git_author_name=os.environ.get("WIKI_GIT_AUTHOR_NAME", "nanobot"),
            git_author_email=os.environ.get("WIKI_GIT_AUTHOR_EMAIL", "nanobot@concr3tica.it"),
            git_auto_push=os.environ.get("WIKI_GIT_AUTO_PUSH", "false").lower() == "true",
        )


_config: WikiConfig | None = None


def get_config() -> WikiConfig:
    global _config
    if _config is None:
        _config = WikiConfig.from_env()
    return _config
