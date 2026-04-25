"""Wiki agent tools — vault read/write, ingest, query, stats."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters


def _get_vault() -> "VaultManager":
    from nanobot.business.wiki.config import get_config
    from nanobot.business.wiki.vault import VaultManager
    return VaultManager(get_config().vault_path)


def _get_raw() -> "RawStore":
    from nanobot.business.wiki.config import get_config
    from nanobot.business.wiki.raw import RawStore
    return RawStore(get_config().raw_path)


# ---------------------------------------------------------------------------
# WikiStatsTool
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiStatsTool(Tool):
    """Mostra statistiche del vault wiki (conteggi per sezione, ingest count, ultima synthesis)."""

    @property
    def name(self) -> str:
        return "wiki_stats"

    @property
    def description(self) -> str:
        return (
            "Restituisce le statistiche del vault wiki: numero di pagine per sezione "
            "(beliefs, patterns, business, content, sources), contatore ingest totale, "
            "data ultima synthesis. Usare in risposta al comando /wiki."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            vault = _get_vault()
            stats = vault.get_stats()
            lines = [
                "📚 **Wiki stats**",
                f"• Beliefs: {stats['beliefs']}",
                f"• Patterns: {stats['patterns']}",
                f"• Business: {stats['business']}",
                f"• Content: {stats['content']}",
                f"• Sources: {stats['sources']}",
                f"• Totale: {stats['total']}",
                f"• Ingest totali: {stats['ingest_count']}",
                f"• Ultima synthesis: {stats['last_synthesis']}",
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.error("wiki_stats error: {}", exc)
            return f"Errore lettura stats vault: {exc}"


# ---------------------------------------------------------------------------
# WikiReadPageTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path relativo alla root del vault, es. 'beliefs/come-valuto-opportunita.md'",
        },
    },
    "required": ["path"],
})
class WikiReadPageTool(Tool):
    """Legge una pagina dal vault wiki."""

    @property
    def name(self) -> str:
        return "wiki_read_page"

    @property
    def description(self) -> str:
        return "Legge il contenuto di una pagina wiki (frontmatter + body). Usare prima di aggiornare una pagina."

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        try:
            vault = _get_vault()
            page = vault.read_page(path)
            fm = page.frontmatter.model_dump(exclude_none=True)
            return f"**{path}**\n\nFrontmatter: {fm}\n\n---\n\n{page.body}"
        except Exception as exc:
            logger.error("wiki_read_page error: {}", exc)
            return f"Errore lettura pagina '{path}': {exc}"


# ---------------------------------------------------------------------------
# WikiWritePageTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path relativo alla root del vault.",
        },
        "body": {
            "type": "string",
            "description": "Contenuto Markdown della pagina (senza frontmatter).",
        },
        "page_type": {
            "type": "string",
            "enum": ["belief", "pattern", "business", "content", "source"],
            "description": "Tipo di pagina.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista di tag.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Livello di confidence (solo per beliefs).",
        },
        "status": {
            "type": "string",
            "enum": ["active", "exploring", "archived"],
            "description": "Status (solo per business e content).",
        },
        "reason": {
            "type": "string",
            "description": "Motivo della scrittura/aggiornamento (va nel log).",
        },
        "increment_source_count": {
            "type": "boolean",
            "description": "Se true, incrementa source_count della pagina.",
        },
    },
    "required": ["path", "body", "page_type", "reason"],
})
class WikiWritePageTool(Tool):
    """Scrive o aggiorna una pagina nel vault wiki. USARE SOLO dopo conferma esplicita di Alessandro."""

    @property
    def name(self) -> str:
        return "wiki_write_page"

    @property
    def description(self) -> str:
        return (
            "Scrive o aggiorna una pagina nel vault wiki. "
            "Se la pagina esiste già, aggiorna il body e il frontmatter mantenendo created e source_count. "
            "Aggiorna automaticamente _log.md. "
            "IMPORTANTE: chiamare SOLO dopo conferma esplicita di Alessandro."
        )

    async def execute(self, **kwargs: Any) -> str:
        path: str = kwargs["path"]
        body: str = kwargs["body"]
        page_type: str = kwargs["page_type"]
        tags: list = kwargs.get("tags") or []
        confidence: str | None = kwargs.get("confidence")
        status: str | None = kwargs.get("status")
        reason: str = kwargs.get("reason", "aggiornamento")
        increment_source_count: bool = kwargs.get("increment_source_count", False)

        try:
            from datetime import date
            vault = _get_vault()

            try:
                existing = vault.read_page(path)
                fm = existing.frontmatter
                fm.tags = tags or fm.tags
                if confidence:
                    fm.confidence = confidence  # type: ignore[assignment]
                if status:
                    fm.status = status  # type: ignore[assignment]
                if increment_source_count:
                    fm.source_count += 1
                from nanobot.business.wiki.models import WikiPage
                page = WikiPage(path=path, frontmatter=fm, body=body)
                vault.write_page(path, page, reason=reason)
                return f"✅ Pagina aggiornata: **{path}**\nMotivo: {reason}"

            except Exception:
                # New page
                vault.create_page(
                    path=path,
                    page_type=page_type,
                    title=path,
                    body=body,
                    tags=tags,
                    confidence=confidence,
                    status=status,
                )
                return f"✅ Nuova pagina creata: **{path}**"

        except Exception as exc:
            logger.error("wiki_write_page error: {}", exc)
            return f"Errore scrittura pagina '{path}': {exc}"


# ---------------------------------------------------------------------------
# WikiListPagesTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["beliefs", "patterns", "business", "content", "sources"],
            "description": "Sezione da listare. Ometti per listare tutto.",
        },
    },
    "required": [],
})
class WikiListPagesTool(Tool):
    """Lista le pagine del vault wiki, opzionalmente filtrate per sezione."""

    @property
    def name(self) -> str:
        return "wiki_list_pages"

    @property
    def description(self) -> str:
        return "Lista i path di tutte le pagine nel vault wiki. Usare per trovare pagine esistenti prima di leggere o aggiornare."

    async def execute(self, **kwargs: Any) -> str:
        section: str | None = kwargs.get("section")
        try:
            vault = _get_vault()
            pages = vault.list_pages(section)
            if not pages:
                return "Nessuna pagina trovata."
            return "\n".join(f"• {p}" for p in pages)
        except Exception as exc:
            logger.error("wiki_list_pages error: {}", exc)
            return f"Errore lista pagine: {exc}"


# ---------------------------------------------------------------------------
# WikiStoreRawTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "Testo da archiviare nel raw store.",
        },
        "content_type": {
            "type": "string",
            "enum": ["text", "url", "pdf"],
            "description": "Tipo di contenuto.",
        },
        "source_url": {
            "type": "string",
            "description": "URL sorgente, se applicabile.",
        },
    },
    "required": ["content", "content_type"],
})
class WikiStoreRawTool(Tool):
    """Archivia input grezzo in wiki-raw/ prima del processing. Sempre primo step dell'ingest."""

    @property
    def name(self) -> str:
        return "wiki_store_raw"

    @property
    def description(self) -> str:
        return (
            "Archivia l'input grezzo di Alessandro in ~/wiki-raw/ con UUID e metadata. "
            "Va chiamato come primo step di ogni ingest, prima di classificare o aggiornare pagine."
        )

    async def execute(self, **kwargs: Any) -> str:
        content: str = kwargs["content"]
        content_type: str = kwargs["content_type"]
        source_url: str | None = kwargs.get("source_url")
        try:
            raw = _get_raw()
            entry = raw.store_raw(content, content_type, source_url=source_url)
            return f"📥 Raw archiviato: `{entry.id}` ({content_type})"
        except Exception as exc:
            logger.error("wiki_store_raw error: {}", exc)
            return f"Errore store raw: {exc}"


# ---------------------------------------------------------------------------
# WikiReadIndexTool
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiReadIndexTool(Tool):
    """Legge _index.md — punto di partenza per query e ingest."""

    @property
    def name(self) -> str:
        return "wiki_read_index"

    @property
    def description(self) -> str:
        return (
            "Legge il catalogo _index.md del vault. "
            "Usare come primo step per qualsiasi query o per orientarsi prima di un ingest."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            vault = _get_vault()
            content = vault.read_index()
            if not content:
                return "⚠️ _index.md non trovato o vuoto."
            return content
        except Exception as exc:
            logger.error("wiki_read_index error: {}", exc)
            return f"Errore lettura index: {exc}"


# ---------------------------------------------------------------------------
# WikiReadSchemaTool
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiReadSchemaTool(Tool):
    """Legge _schema.md — regole operative del vault. Da leggere all'inizio di ogni sessione wiki."""

    @property
    def name(self) -> str:
        return "wiki_read_schema"

    @property
    def description(self) -> str:
        return (
            "Legge _schema.md — il contratto operativo della wiki. "
            "Contiene definizioni dei tipi di pagine, workflow di ingest/query/synthesis, "
            "la voce di Alessandro. Da leggere all'inizio della sessione wiki."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            vault = _get_vault()
            content = vault.read_schema()
            if not content:
                return "⚠️ _schema.md non trovato."
            return content
        except Exception as exc:
            logger.error("wiki_read_schema error: {}", exc)
            return f"Errore lettura schema: {exc}"


# ---------------------------------------------------------------------------
# WikiAppendLogTool
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "entry_type": {
            "type": "string",
            "description": "Tipo entry: ingest | update | query | synthesis | lint.",
        },
        "title": {
            "type": "string",
            "description": "Titolo breve dell'entry.",
        },
        "detail": {
            "type": "string",
            "description": "Dettaglio opzionale.",
        },
    },
    "required": ["entry_type", "title"],
})
class WikiAppendLogTool(Tool):
    """Appende una entry a _log.md. Chiamare dopo ogni ingest o modifica significativa."""

    @property
    def name(self) -> str:
        return "wiki_append_log"

    @property
    def description(self) -> str:
        return "Appende una entry timestampata a _log.md. Va chiamato dopo ogni ingest o aggiornamento pagina."

    async def execute(self, **kwargs: Any) -> str:
        from datetime import datetime
        from nanobot.business.wiki.models import LogEntry
        entry_type: str = kwargs["entry_type"]
        title: str = kwargs["title"]
        detail: str = kwargs.get("detail", "")
        try:
            vault = _get_vault()
            vault.append_log(LogEntry(
                timestamp=datetime.now(),
                type=entry_type,
                title=title,
                detail=detail,
            ))
            return f"✅ Log aggiornato: {entry_type} | {title}"
        except Exception as exc:
            logger.error("wiki_append_log error: {}", exc)
            return f"Errore append log: {exc}"
