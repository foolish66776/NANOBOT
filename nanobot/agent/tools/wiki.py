"""Wiki agent tools — vault read/write, ingest, query, stats."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters


def _get_vault() -> "VaultManager":
    from nanobot.business.wiki.config import get_config
    from nanobot.business.wiki.vault import VaultManager
    cfg = get_config()
    git_mgr = None
    if cfg.git_auto_push and cfg.git_repo and cfg.git_ssh_key:
        from nanobot.business.wiki.git_manager import GitManager
        git_mgr = GitManager(
            repo_path=cfg.vault_path,
            remote_url=cfg.git_repo,
            ssh_key_content=cfg.git_ssh_key,
            author_name=cfg.git_author_name,
            author_email=cfg.git_author_email,
        )
    return VaultManager(cfg.vault_path, git_manager=git_mgr)


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


# ---------------------------------------------------------------------------
# WikiQueryTool  (Fase 2 — Query graph)
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Domanda da rispondere attingendo al vault wiki.",
        },
        "sections": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Sezioni da consultare (beliefs, patterns, business, content, sources). Ometti per tutte.",
        },
    },
    "required": ["question"],
})
class WikiQueryTool(Tool):
    """Risponde a una domanda attingendo al vault wiki, in prima persona come Alessandro."""

    @property
    def name(self) -> str:
        return "wiki_query"

    @property
    def description(self) -> str:
        return (
            "Risponde a una domanda attingendo al vault wiki di Alessandro. "
            "Legge _index.md, identifica le pagine pertinenti, le legge, e compone "
            "una risposta in prima persona come Alessandro. "
            "Cita sempre le pagine usate. Se il vault non ha material sufficiente, lo dice esplicitamente."
        )

    async def execute(self, **kwargs: Any) -> str:
        question: str = kwargs["question"]
        sections: list[str] | None = kwargs.get("sections")
        try:
            vault = _get_vault()
            index = vault.read_index()
            pages_to_read: list[str] = []
            for section in (sections or ["beliefs", "patterns", "business"]):
                pages_to_read.extend(vault.list_pages(section))

            if not pages_to_read:
                return (
                    "Il vault non contiene ancora pagine sufficienti per rispondere. "
                    "Aggiungi contenuto con l'ingest prima di fare query."
                )

            # Read all relevant pages
            page_contents: list[str] = []
            for path in pages_to_read[:12]:  # cap to avoid context overflow
                try:
                    page = vault.read_page(path)
                    page_contents.append(f"### {path}\n\n{page.body}")
                except Exception:
                    pass

            if not page_contents:
                return "Nessuna pagina leggibile trovata per questa query."

            # Build context block for the agent to use
            context = "\n\n---\n\n".join(page_contents)
            result = (
                f"**Contesto vault per la query:** \"{question}\"\n\n"
                f"Pagine consultate: {', '.join(pages_to_read[:12])}\n\n"
                f"---\n\n{context}\n\n---\n\n"
                "Rispondi ora in prima persona come Alessandro, citando le pagine usate."
            )
            vault.append_log(__import__("nanobot.business.wiki.models", fromlist=["LogEntry"]).LogEntry(
                timestamp=__import__("datetime").datetime.now(),
                type="query",
                title=question[:80],
            ))
            return result
        except Exception as exc:
            logger.error("wiki_query error: {}", exc)
            return f"Errore query vault: {exc}"


# ---------------------------------------------------------------------------
# WikiLintTool  (Fase 3 — Lint)
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiLintTool(Tool):
    """Esegue lint del vault: pagine orfane, beliefs stale, possibili contraddizioni."""

    @property
    def name(self) -> str:
        return "wiki_lint"

    @property
    def description(self) -> str:
        return (
            "Analizza il vault per problemi di manutenzione: pagine orfane senza link in entrata, "
            "beliefs con confidence 'low' non aggiornate da più di WIKI_LINT_STALE_DAYS giorni, "
            "pagine con stesso tag ma potenziali contraddizioni. "
            "Restituisce un report formattato da presentare ad Alessandro."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from datetime import date, timedelta
            vault = _get_vault()
            cfg = __import__("nanobot.business.wiki.config", fromlist=["get_config"]).get_config()
            issues: list[str] = []
            index_text = vault.read_index()

            # Check stale low-confidence beliefs
            for path in vault.list_pages("beliefs"):
                try:
                    page = vault.read_page(path)
                    fm = page.frontmatter
                    if fm.confidence == "low":
                        stale_threshold = date.today() - timedelta(days=cfg.lint_stale_days)
                        if fm.updated < stale_threshold:
                            issues.append(
                                f"2. Belief stale (confidence: low, aggiornata {fm.updated}): "
                                f"`{path}` — ancora valida?"
                            )
                except Exception:
                    pass

            # Check orphan pages (not mentioned in index)
            for path in vault.list_pages():
                if path not in index_text:
                    issues.append(f"1. Pagina orfana: `{path}` — nessun riferimento in _index.md. Archiviare?")

            if not issues:
                vault.append_log(__import__("nanobot.business.wiki.models", fromlist=["LogEntry"]).LogEntry(
                    timestamp=__import__("datetime").datetime.now(),
                    type="lint",
                    title="Lint completato — nessun problema trovato",
                ))
                return "✅ Lint completato — nessun problema trovato nel vault."

            numbered = "\n".join(issues)
            vault.append_log(__import__("nanobot.business.wiki.models", fromlist=["LogEntry"]).LogEntry(
                timestamp=__import__("datetime").datetime.now(),
                type="lint",
                title=f"Lint: {len(issues)} punti",
            ))
            return (
                f"🔍 **Lint wiki** — {len(issues)} punti da verificare:\n\n"
                f"{numbered}\n\n"
                "Rispondi punto per punto o ignora."
            )
        except Exception as exc:
            logger.error("wiki_lint error: {}", exc)
            return f"Errore lint: {exc}"


# ---------------------------------------------------------------------------
# WikiSynthesisCheckTool  (Fase 3 — Synthesis trigger)
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiSynthesisCheckTool(Tool):
    """Verifica se è il momento di proporre una synthesis (ogni N ingest)."""

    @property
    def name(self) -> str:
        return "wiki_synthesis_check"

    @property
    def description(self) -> str:
        return (
            "Controlla il contatore ingest nel _log.md. "
            "Se ha raggiunto il trigger interval (WIKI_SYNTHESIS_TRIGGER_INTERVAL), "
            "restituisce le ultime N riflessioni da analizzare per proporre nuovi pattern/beliefs. "
            "Se non è il momento, restituisce un messaggio vuoto."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            from nanobot.business.wiki.config import get_config
            vault = _get_vault()
            cfg = get_config()
            stats = vault.get_stats()
            ingest_count = stats["ingest_count"]

            if ingest_count == 0 or ingest_count % cfg.synthesis_trigger_interval != 0:
                return ""  # Not time yet

            # Read recent sources and log for analysis
            recent_sources: list[str] = []
            for path in vault.list_pages("sources")[-cfg.synthesis_trigger_interval:]:
                try:
                    page = vault.read_page(path)
                    recent_sources.append(f"**{path}**\n{page.body[:400]}")
                except Exception:
                    pass

            if not recent_sources:
                return ""

            context = "\n\n---\n\n".join(recent_sources)
            return (
                f"🔄 **Synthesis trigger** — {ingest_count} ingest completati.\n\n"
                f"Analizza le ultime {len(recent_sources)} fonti e proponi se emergono nuovi pattern:\n\n"
                f"{context}\n\n"
                "Se noti un pattern ricorrente, proponi: "
                "'Ho notato un pattern in N riflessioni su [tema]. "
                "Vuoi che crei patterns/[nome].md? Includerebbe: [3 bullet]'"
            )
        except Exception as exc:
            logger.error("wiki_synthesis_check error: {}", exc)
            return f"Errore synthesis check: {exc}"


# ---------------------------------------------------------------------------
# WikiMem0SyncTool  (Fase 4 — Mem0 sync)
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "full_resync": {
            "type": "boolean",
            "description": "Se true, risincronizza l'intero vault. Se false (default), sync solo pagina specificata.",
        },
        "path": {
            "type": "string",
            "description": "Path relativo della pagina da sincronizzare (ignorato se full_resync=true).",
        },
    },
    "required": [],
})
class WikiMem0SyncTool(Tool):
    """Sincronizza pagine wiki nei namespace Mem0 (wiki:beliefs, wiki:patterns, wiki:sources)."""

    @property
    def name(self) -> str:
        return "wiki_mem0_sync"

    @property
    def description(self) -> str:
        return (
            "Sincronizza una pagina (o l'intero vault) nei namespace Mem0 wiki:*. "
            "Chiamare dopo ogni wiki_write_page per mantenere Mem0 aggiornato. "
            "full_resync=true per recovery/migration completa."
        )

    async def execute(self, **kwargs: Any) -> str:
        full_resync: bool = kwargs.get("full_resync", False)
        path: str | None = kwargs.get("path")
        try:
            from nanobot.business.wiki.mem import sync_page_to_mem0, sync_vault_to_mem0
            vault = _get_vault()

            # Get memory backend from nanobot context
            try:
                from nanobot.agent.memory.mem0_backend import Mem0Backend
                import os
                db_url = os.environ.get("NANOBOT_MEMORY_DATABASE_URL", "")
                backend = Mem0Backend(db_url) if db_url else None
            except Exception:
                backend = None

            if backend is None:
                return "⚠️ Mem0 backend non disponibile (NANOBOT_MEMORY_DATABASE_URL mancante)."

            if full_resync:
                summary = await sync_vault_to_mem0(backend, vault)
                return f"✅ Mem0 full resync: {summary['synced']} pagine sincronizzate, {summary['errors']} errori."

            if not path:
                return "Specifica un path o usa full_resync=true."

            section = path.split("/")[0] if "/" in path else "other"
            page = vault.read_page(path)
            await sync_page_to_mem0(backend, path, page.body, section)
            return f"✅ Mem0 sync: {path} → wiki:{section}"

        except Exception as exc:
            logger.error("wiki_mem0_sync error: {}", exc)
            return f"Errore Mem0 sync: {exc}"


# ---------------------------------------------------------------------------
# WikiIngestSourceTool  (Fase 5 — source ingest)
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Titolo della fonte (articolo, newsletter, video, PDF).",
        },
        "source_url": {
            "type": "string",
            "description": "URL della fonte, se disponibile.",
        },
        "source_type": {
            "type": "string",
            "enum": ["articolo", "newsletter", "PDF", "video", "altro"],
            "description": "Tipo di fonte.",
        },
        "summary": {
            "type": "string",
            "description": "Sintesi oggettiva della fonte in 3-5 righe.",
        },
        "alessandros_take": {
            "type": "string",
            "description": "Punto di vista di Alessandro sulla fonte. Se non fornito, il tool chiederà.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tag pertinenti.",
        },
    },
    "required": ["title", "source_type", "summary"],
})
class WikiIngestSourceTool(Tool):
    """Crea una pagina sources/ per una fonte esterna ingested."""

    @property
    def name(self) -> str:
        return "wiki_ingest_source"

    @property
    def description(self) -> str:
        return (
            "Crea una pagina in sources/ per una fonte esterna (articolo, newsletter, PDF, video). "
            "Se alessandros_take non è fornito, risponde chiedendo il suo punto di vista prima di scrivere. "
            "Aggiorna _log.md e sincronizza su Mem0."
        )

    async def execute(self, **kwargs: Any) -> str:
        title: str = kwargs["title"]
        source_url: str | None = kwargs.get("source_url")
        source_type: str = kwargs["source_type"]
        summary: str = kwargs["summary"]
        alessandros_take: str | None = kwargs.get("alessandros_take")
        tags: list = kwargs.get("tags") or []

        if not alessandros_take:
            return (
                f"📎 Fonte ricevuta: **{title}**\n\n"
                f"Sintesi: {summary}\n\n"
                "Qual è il tuo take su questo? "
                "Cosa è in linea con le tue beliefs, cosa le sfida, cosa aggiunge?"
            )

        import re
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
        path = f"sources/{slug}.md"

        body = f"# {title}\n"
        if source_url:
            body += f"**Fonte:** {source_url}\n"
        body += f"**Tipo:** {source_type}\n\n"
        body += f"## Sintesi oggettiva\n\n{summary}\n\n"
        body += f"## Alessandro's take\n\n{alessandros_take}\n\n"
        body += "## Connessioni wiki\n\n*(da completare)*\n"

        try:
            vault = _get_vault()
            raw = _get_raw()
            raw.store_raw(f"{title}\n{source_url or ''}\n{summary}\n{alessandros_take}", "text", source_url=source_url)
            vault.create_page(path=path, page_type="source", title=title, body=body, tags=tags)
            return (
                f"✅ Fonte ingested: **{path}**\n"
                f"Ora aggiorna le pagine beliefs/ o business/ pertinenti se necessario, "
                f"poi chiama wiki_mem0_sync per sincronizzare su Mem0."
            )
        except Exception as exc:
            logger.error("wiki_ingest_source error: {}", exc)
            return f"Errore ingest source: {exc}"


# ---------------------------------------------------------------------------
# WikiIngestBusinessIdeaTool  (Fase 5 — business_idea ingest)
# ---------------------------------------------------------------------------

@tool_parameters({
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Titolo dell'idea di business (4-6 parole).",
        },
        "description": {
            "type": "string",
            "description": "Descrizione dell'idea: problema, soluzione, leva.",
        },
        "pain_point": {
            "type": "string",
            "description": "Pain point concreto che risolve.",
        },
        "who_pays": {
            "type": "string",
            "description": "Chi paga e perché.",
        },
        "why_now": {
            "type": "string",
            "description": "Perché questo momento è quello giusto.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tag pertinenti.",
        },
    },
    "required": ["title", "description"],
})
class WikiIngestBusinessIdeaTool(Tool):
    """Crea una pagina business/ per una nuova idea di business con status 'exploring'."""

    @property
    def name(self) -> str:
        return "wiki_ingest_business_idea"

    @property
    def description(self) -> str:
        return (
            "Crea una pagina in business/ per una nuova idea di business con status 'exploring'. "
            "Collega alle beliefs e patterns pertinenti. "
            "Se mancano pain_point, who_pays o why_now, li chiede prima di creare la pagina."
        )

    async def execute(self, **kwargs: Any) -> str:
        title: str = kwargs["title"]
        description: str = kwargs["description"]
        pain_point: str | None = kwargs.get("pain_point")
        who_pays: str | None = kwargs.get("who_pays")
        why_now: str | None = kwargs.get("why_now")
        tags: list = kwargs.get("tags") or []

        missing = []
        if not pain_point:
            missing.append("pain point concreto")
        if not who_pays:
            missing.append("chi paga")
        if not why_now:
            missing.append("perché ora")

        if missing:
            return (
                f"💡 Idea ricevuta: **{title}**\n\n"
                f"{description}\n\n"
                f"Prima di creare la pagina, dimmi: {', '.join(missing)}?"
            )

        import re
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
        path = f"business/{slug}.md"

        body = f"# {title}\n\n"
        body += f"## Descrizione\n\n{description}\n\n"
        body += f"## Pain point\n\n{pain_point}\n\n"
        body += f"## Chi paga\n\n{who_pays}\n\n"
        body += f"## Perché ora\n\n{why_now}\n\n"
        body += "## Connessioni\n\n*(da completare — collega a beliefs/ e patterns/ pertinenti)*\n"

        try:
            vault = _get_vault()
            raw = _get_raw()
            raw.store_raw(f"{title}\n{description}\n{pain_point}\n{who_pays}\n{why_now}", "text")
            vault.create_page(path=path, page_type="business", title=title, body=body,
                              tags=tags, status="exploring")
            return (
                f"✅ Idea di business creata: **{path}** (status: exploring)\n"
                f"Collega a beliefs/ e patterns/ pertinenti con wiki_read_index + wiki_write_page."
            )
        except Exception as exc:
            logger.error("wiki_ingest_business_idea error: {}", exc)
            return f"Errore ingest business idea: {exc}"


# ---------------------------------------------------------------------------
# WikiGitPullTool
# ---------------------------------------------------------------------------

@tool_parameters({"type": "object", "properties": {}, "required": []})
class WikiGitPullTool(Tool):
    """Forza git pull dal remote GitHub (usare dopo edit manuale da Obsidian)."""

    @property
    def name(self) -> str:
        return "wiki_git_pull"

    @property
    def description(self) -> str:
        return (
            "Esegue git pull origin main sul vault. "
            "Usare quando Alessandro ha editato da Obsidian e vuole sincronizzare. "
            "Richiede WIKI_GIT_AUTO_PUSH=true e WIKI_GIT_REPO configurati."
        )

    async def execute(self, **kwargs: Any) -> str:
        try:
            vault = _get_vault()
            await vault.pull()
            return "✅ git pull completato — vault aggiornato."
        except Exception as exc:
            logger.error("wiki_git_pull error: {}", exc)
            return f"Errore git pull: {exc}"
