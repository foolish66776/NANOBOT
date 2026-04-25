"""Mem0 helpers scoped ai namespace wiki:*

Mem0 è un mirror sintetico del vault — non la fonte di verità.
La fonte di verità sono sempre i file Markdown.
Mem0 serve per query rapide e per il contesto a lungo termine nelle sessioni Telegram.

Namespace:
  wiki:beliefs  — estratti delle beliefs correnti
  wiki:patterns — estratti dei pattern correnti
  wiki:sources  — metadata fonti ingested
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.memory.base import MemoryBackend
    from .vault import VaultManager


_NAMESPACE_MAP = {
    "beliefs": "wiki:beliefs",
    "patterns": "wiki:patterns",
    "sources": "wiki:sources",
    "business": "wiki:business",
}


async def sync_page_to_mem0(
    backend: "MemoryBackend",
    path: str,
    body: str,
    section: str,
) -> None:
    """Upsert a page summary into the appropriate Mem0 namespace."""
    namespace = _NAMESPACE_MAP.get(section, f"wiki:{section}")
    # Truncate to avoid token overflow
    summary = body[:1200].strip()
    text = f"[wiki/{path}]\n{summary}"
    try:
        await backend.add(text, namespace)
        logger.debug("Mem0 sync: {} → {}", path, namespace)
    except Exception as exc:
        logger.warning("Mem0 sync failed for {}: {}", path, exc)


async def sync_vault_to_mem0(
    backend: "MemoryBackend",
    vault: "VaultManager",
) -> dict:
    """Full resync of vault into Mem0 (use for recovery/migration).

    Returns summary: {synced: N, errors: N}
    """
    summary = {"synced": 0, "errors": 0}
    for section in ("beliefs", "patterns", "sources", "business"):
        for path in vault.list_pages(section):
            try:
                page = vault.read_page(path)
                await sync_page_to_mem0(backend, path, page.body, section)
                summary["synced"] += 1
            except Exception as exc:
                logger.error("Mem0 full sync error for {}: {}", path, exc)
                summary["errors"] += 1
    logger.info("Wiki Mem0 full sync: {}", summary)
    return summary


async def search_wiki_mem0(
    backend: "MemoryBackend",
    query: str,
    sections: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    """Search Mem0 across wiki namespaces. Returns list of relevant text snippets."""
    results: list[str] = []
    target_sections = sections or ["beliefs", "patterns", "business", "sources"]
    for section in target_sections:
        namespace = _NAMESPACE_MAP.get(section, f"wiki:{section}")
        try:
            hits = await backend.search(query, namespace, limit=limit)
            for hit in hits:
                if hasattr(hit, "memory"):
                    results.append(hit.memory)
                elif isinstance(hit, dict):
                    results.append(hit.get("memory", str(hit)))
        except Exception as exc:
            logger.warning("Mem0 search failed for namespace {}: {}", namespace, exc)
    return results
