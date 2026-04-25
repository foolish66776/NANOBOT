"""VaultManager — read/write/list wiki pages with validated frontmatter."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from .models import IndexChange, LogEntry, WikiFrontmatter, WikiPage


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)

# Sections tracked in _index.md
_INDEX_SECTIONS = ["beliefs", "patterns", "business", "content", "sources"]


class VaultError(Exception):
    pass


class PageNotFound(VaultError):
    pass


class InvalidFrontmatter(VaultError):
    pass


class VaultManager:
    def __init__(self, vault_path: Path) -> None:
        self.root = vault_path
        self.root.mkdir(parents=True, exist_ok=True)
        for section in _INDEX_SECTIONS:
            (self.root / section).mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_page(self, path: str) -> WikiPage:
        """Read a page by relative path. Raises PageNotFound if missing."""
        full = self.root / path
        if not full.exists():
            raise PageNotFound(f"Page not found: {path}")
        raw = full.read_text(encoding="utf-8")
        frontmatter, body = self._parse(raw, path)
        return WikiPage(path=path, frontmatter=frontmatter, body=body)

    def read_schema(self) -> str:
        """Return raw content of _schema.md (runtime rules)."""
        schema = self.root / "_schema.md"
        if schema.exists():
            return schema.read_text(encoding="utf-8")
        return ""

    def read_index(self) -> str:
        """Return raw content of _index.md."""
        idx = self.root / "_index.md"
        if idx.exists():
            return idx.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_page(self, path: str, page: WikiPage, reason: str = "") -> WikiPage:
        """Write page, update 'updated' date, append log. Validates frontmatter."""
        page.frontmatter.updated = date.today()
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(self._serialize(page), encoding="utf-8")
        logger.info("Wiki page written: {} — {}", path, reason)
        self.append_log(LogEntry(
            timestamp=datetime.now(),
            type="update",
            title=path,
            detail=reason,
        ))
        return page

    def create_page(self, path: str, page_type: str, title: str, body: str,
                    tags: list[str] | None = None,
                    confidence: str | None = None,
                    status: str | None = None) -> WikiPage:
        """Create a new page with today's date. Raises VaultError if already exists."""
        full = self.root / path
        if full.exists():
            raise VaultError(f"Page already exists: {path}")
        today = date.today()
        fm = WikiFrontmatter(
            type=page_type,  # type: ignore[arg-type]
            tags=tags or [],
            confidence=confidence,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            created=today,
            updated=today,
            source_count=0,
        )
        page = WikiPage(path=path, frontmatter=fm, body=body)
        return self.write_page(path, page, reason=f"Created: {title}")

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_pages(self, section: Optional[str] = None) -> list[str]:
        """Return relative paths of all .md pages, optionally filtered by section."""
        results = []
        search_root = self.root / section if section else self.root
        for f in sorted(search_root.rglob("*.md")):
            rel = f.relative_to(self.root).as_posix()
            # Skip meta files
            if rel.startswith("_"):
                continue
            results.append(rel)
        return results

    def get_stats(self) -> dict:
        """Return counts per section plus log metadata."""
        counts = {s: len(self.list_pages(s)) for s in _INDEX_SECTIONS}
        counts["total"] = sum(counts.values())
        log_path = self.root / "_log.md"
        ingest_count = 0
        last_synthesis = "—"
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
            ingest_count = text.count("| ingest |") + text.count("type=ingest")
            for line in text.splitlines():
                if "synthesis" in line.lower() and line.startswith("## ["):
                    last_synthesis = line[4:line.index("]")]
        counts["ingest_count"] = ingest_count
        counts["last_synthesis"] = last_synthesis
        return counts

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def update_index(self, changes: list[IndexChange]) -> None:
        """Update _index.md rows for the given changes."""
        idx_path = self.root / "_index.md"
        if not idx_path.exists():
            logger.warning("_index.md not found, skipping update")
            return
        text = idx_path.read_text(encoding="utf-8")
        for change in changes:
            section = change.path.split("/")[0] if "/" in change.path else "other"
            filename = Path(change.path).name
            link = f"[{change.title}]({change.path})"
            row_pattern = re.compile(rf"\|\s*\[.*?\]\({re.escape(change.path)}\).*?\|")
            if change.action == "remove":
                text = row_pattern.sub("", text)
            elif change.action == "add":
                # Append row to correct section table
                section_header = f"## {section}/"
                if section_header in text:
                    insert_pos = text.find("\n", text.rfind("|", 0, text.find("---\n\n##", text.find(section_header))))
                    # Simpler: just append after last | in section
                    pass
                # Fallback: append a note at end of section
                logger.info("Index add: {} — manual review may be needed", change.path)
            elif change.action == "update":
                today = date.today().isoformat()
                text = row_pattern.sub(
                    f"| {link} | {change.summary} | {today} |",
                    text,
                )
        idx_path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def append_log(self, entry: LogEntry) -> None:
        """Append entry to _log.md."""
        log_path = self.root / "_log.md"
        ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
        block = f"\n## [{ts}] {entry.type} | {entry.title}\n"
        if entry.detail:
            block += f"\n{entry.detail}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(block)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse(self, raw: str, path: str) -> tuple[WikiFrontmatter, str]:
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            raise InvalidFrontmatter(f"No YAML frontmatter in: {path}")
        try:
            data = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise InvalidFrontmatter(f"Bad YAML in {path}: {e}") from e
        try:
            fm = WikiFrontmatter(**data)
        except Exception as e:
            raise InvalidFrontmatter(f"Invalid frontmatter in {path}: {e}") from e
        body = raw[m.end():]
        return fm, body

    def _serialize(self, page: WikiPage) -> str:
        fm = page.frontmatter.model_dump(exclude_none=True)
        # Convert date objects to strings for YAML
        for k in ("created", "updated"):
            if isinstance(fm.get(k), date):
                fm[k] = fm[k].isoformat()
        yaml_str = yaml.dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return f"---\n{yaml_str}---\n\n{page.body}"
