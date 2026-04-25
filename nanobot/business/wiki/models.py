"""Pydantic models for the wiki business line."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator


class WikiFrontmatter(BaseModel):
    type: Literal["belief", "pattern", "business", "content", "source"]
    tags: list[str] = []
    confidence: Optional[Literal["high", "medium", "low"]] = None   # beliefs only
    status: Optional[Literal["active", "exploring", "archived"]] = None  # business + content
    created: date
    updated: date
    source_count: int = 0

    @field_validator("confidence", mode="before")
    @classmethod
    def _validate_confidence(cls, v: Any) -> Any:
        return v or None

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> Any:
        return v or None


class WikiPage(BaseModel):
    path: str          # relative to vault root, e.g. "beliefs/come-valuto-opportunita.md"
    frontmatter: WikiFrontmatter
    body: str          # full markdown body excluding frontmatter block


class RawEntry(BaseModel):
    id: str            # UUID
    path: str          # full path to raw file
    meta_path: str     # full path to .meta.json
    content_type: str  # "text" | "url" | "pdf"
    source_url: Optional[str] = None
    created_at: str    # ISO timestamp


class ClassificationResult(BaseModel):
    type: Literal["reflection", "source", "business_idea", "query", "command"]
    confidence: float  # 0.0–1.0
    extracted_url: Optional[str] = None  # if type=source and input contains URL
    command: Optional[str] = None        # if type=command: "lint"|"synthesis"|"content"|"search"|"stats"
    raw_text: str                        # normalized input


class IndexChange(BaseModel):
    path: str
    title: str
    summary: str
    action: Literal["add", "update", "remove"]


class LogEntry(BaseModel):
    timestamp: datetime
    type: str      # "ingest" | "update" | "query" | "synthesis" | "lint" | "init"
    title: str
    detail: str = ""
