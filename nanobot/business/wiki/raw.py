"""RawStore — archivia input grezzi da Alessandro prima del processing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from .models import RawEntry


class RawStore:
    def __init__(self, raw_path: Path) -> None:
        self.root = raw_path
        self.root.mkdir(parents=True, exist_ok=True)

    def store_raw(
        self,
        content: str | bytes,
        content_type: str,
        source_url: Optional[str] = None,
        metadata: dict | None = None,
    ) -> RawEntry:
        """Archive raw input. Returns RawEntry with UUID and paths."""
        raw_id = str(uuid.uuid4())
        today = datetime.now().strftime("%Y-%m-%d")
        day_dir = self.root / today
        day_dir.mkdir(parents=True, exist_ok=True)

        ext = _ext_for_type(content_type)
        raw_path = day_dir / f"{raw_id}.{ext}"
        meta_path = day_dir / f"{raw_id}.meta.json"

        if isinstance(content, bytes):
            raw_path.write_bytes(content)
        else:
            raw_path.write_text(content, encoding="utf-8")

        meta = {
            "id": raw_id,
            "content_type": content_type,
            "source_url": source_url,
            "created_at": datetime.now().isoformat(),
            **(metadata or {}),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info("Raw stored: {} type={}", raw_id, content_type)
        return RawEntry(
            id=raw_id,
            path=str(raw_path),
            meta_path=str(meta_path),
            content_type=content_type,
            source_url=source_url,
            created_at=meta["created_at"],
        )

    def get_raw(self, raw_id: str) -> RawEntry:
        """Retrieve a raw entry by UUID."""
        for meta_file in self.root.rglob(f"{raw_id}.meta.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            ext = _ext_for_type(meta["content_type"])
            raw_file = meta_file.parent / f"{raw_id}.{ext}"
            return RawEntry(
                id=raw_id,
                path=str(raw_file),
                meta_path=str(meta_file),
                content_type=meta["content_type"],
                source_url=meta.get("source_url"),
                created_at=meta["created_at"],
            )
        raise FileNotFoundError(f"Raw entry not found: {raw_id}")


def _ext_for_type(content_type: str) -> str:
    return {"pdf": "pdf", "text": "txt", "url": "txt"}.get(content_type, "txt")
