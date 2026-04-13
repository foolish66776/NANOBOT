"""Mem0Backend — MemoryBackend implementation backed by mem0ai + pgvector + Cohere."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from loguru import logger

from nanobot.agent.memory.base import MemoryBackend, MemoryHit, UserProfile

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# CohereEmbedder — thin wrapper injected into the mem0 Memory instance
# ---------------------------------------------------------------------------

class CohereEmbedder:
    """Minimal embedder compatible with mem0's EmbeddingBase interface.

    Uses Cohere's embed-multilingual-v3.0 (1024 dims).
    Uses input_type="search_document" for add/update, "search_query" for search.
    """

    EMBEDDING_DIMS = 1024

    def __init__(self, api_key: str, model: str = "embed-multilingual-v3.0") -> None:
        import cohere  # optional dep; imported here to keep startup fast

        self._client = cohere.Client(api_key=api_key)
        self.model = model
        # mem0 reads config.embedding_dims from this object
        self.config = _EmbedderConfigProxy(model=model, embedding_dims=self.EMBEDDING_DIMS)

    def embed(
        self,
        text: str,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> list[float]:
        input_type = (
            "search_document" if memory_action in ("add", "update") else "search_query"
        )
        response = self._client.embed(
            texts=[text],
            model=self.model,
            input_type=input_type,
            embedding_types=["float"],
        )
        return response.embeddings.float[0]


class _EmbedderConfigProxy:
    """Minimal config object that mem0 reads embedding_dims and model from."""

    def __init__(self, model: str, embedding_dims: int) -> None:
        self.model = model
        self.embedding_dims = embedding_dims


# ---------------------------------------------------------------------------
# Mem0Backend
# ---------------------------------------------------------------------------

_PROFILE_RECENT = 20   # how many recent entries form the dynamic profile
_PROFILE_STATIC = 10   # how many older entries form the static profile


class Mem0Backend(MemoryBackend):
    """MemoryBackend backed by mem0ai (AsyncMemory) + pgvector + Cohere embeddings.

    Namespace mapping (Option B — semantically correct):
        user_id  = config["userId"]   (default "alessandro", fixed per user)
        agent_id = container_tag      (business line: "personal", "concr3tica", …)

    Cross-business search: omit agent_id (search all of user_id's memories).

    Error policy: on any mem0 failure, log at WARNING level and return a safe
    empty result rather than crashing the agent loop.
    """

    def __init__(self, config: dict, workspace: Path | str | None = None) -> None:
        """
        Args:
            config:    The ``memory.mem0`` section of ~/.nanobot/config.json.
            workspace: Path to the nanobot workspace.  Used for the SQLite
                       history DB path if not explicitly configured.
        """
        self._user_id = config.get("userId", "alessandro")
        self._mem: Any | None = None  # lazy init — set in _ensure_mem0
        self._config = config
        self._workspace = Path(workspace) if workspace else None
        self._init_error: Exception | None = None

    # -- lazy initialisation -------------------------------------------------

    def _ensure_mem0(self) -> Any:
        """Return (or create) the AsyncMemory instance.  Thread-safe enough for asyncio."""
        if self._mem is not None:
            return self._mem
        if self._init_error is not None:
            raise self._init_error
        try:
            self._mem = self._build_mem0()
            logger.info("Mem0Backend initialised (collection={})", self._collection_name)
        except Exception as exc:
            self._init_error = exc
            raise
        return self._mem

    def _build_mem0(self) -> Any:
        """Build and return the AsyncMemory instance."""
        from mem0 import AsyncMemory
        from mem0.configs.base import MemoryConfig
        from mem0.embeddings.configs import EmbedderConfig

        cfg = self._config
        workspace = self._workspace

        # --- credentials (config dict → env var fallback) ---
        cohere_api_key = cfg.get("cohereApiKey") or _env("COHERE_API_KEY")
        database_url = cfg.get("databaseUrl") or _env("NANOBOT_MEMORY_DATABASE_URL")
        llm_model = cfg.get("llmModel", "openai/gpt-4o-mini")
        self._collection_name = cfg.get("collectionName", "mem0_memories")

        # LLM key resolution: explicit llmApiKey > openrouterApiKey > OPENROUTER_API_KEY env
        llm_api_key = (
            cfg.get("llmApiKey")
            or cfg.get("openrouterApiKey")
            or _env("OPENROUTER_API_KEY")
        )
        llm_base_url: str | None = cfg.get("llmBaseUrl")
        # If using OpenRouter key without explicit base URL, default to OpenRouter endpoint.
        if not llm_base_url and (cfg.get("openrouterApiKey") or _env("OPENROUTER_API_KEY")):
            llm_base_url = "https://openrouter.ai/api/v1"

        if not cohere_api_key:
            raise ValueError(
                "Mem0Backend: Cohere API key not found.  Set memory.mem0.cohereApiKey "
                "in config.json or COHERE_API_KEY in environment."
            )
        if not database_url:
            raise ValueError(
                "Mem0Backend: Postgres URL not found.  Set memory.mem0.databaseUrl "
                "in config.json or NANOBOT_MEMORY_DATABASE_URL in environment."
            )
        if not llm_api_key:
            raise ValueError(
                "Mem0Backend: LLM API key not found.  Set memory.mem0.llmApiKey "
                "(any OpenAI-compatible key), memory.mem0.openrouterApiKey, or "
                "OPENROUTER_API_KEY in environment / ~/.nanobot/.env.local."
            )

        # Build LLM config dict for mem0.
        llm_provider_cfg: dict = {
            "model": llm_model,
            "api_key": llm_api_key,
        }
        if llm_base_url:
            llm_provider_cfg["openai_base_url"] = llm_base_url

        # --- embedder: placeholder (will be swapped after init) ---
        # We still need to tell mem0 the correct dims for pgvector table creation.
        # The "openai" provider is used as a placeholder; we replace the instance
        # right after Memory.__init__ before any embed() call is made.
        embedder_config = EmbedderConfig(
            provider="openai",
            config={
                "model": "text-embedding-3-small",
                "embedding_dims": CohereEmbedder.EMBEDDING_DIMS,
                "api_key": "placeholder-will-not-be-called",
            },
        )

        # --- vector store: pgvector ---
        vector_store_config = {
            "provider": "pgvector",
            "config": {
                "connection_string": database_url,
                "collection_name": self._collection_name,
                "embedding_model_dims": CohereEmbedder.EMBEDDING_DIMS,
                "diskann": False,
                "hnsw": True,
            },
        }

        # --- history DB ---
        if workspace:
            history_db = str(workspace / "memory" / "mem0_history.db")
        else:
            history_db = cfg.get("historyDbPath", ":memory:")

        mem0_config = MemoryConfig(
            llm={"provider": "openai", "config": llm_provider_cfg},
            embedder=embedder_config,
            vector_store=vector_store_config,
            history_db_path=history_db,
        )

        mem = AsyncMemory(config=mem0_config)

        # Swap in real Cohere embedder — no network calls happen during init
        cohere_embedder = CohereEmbedder(api_key=cohere_api_key)
        mem.embedding_model = cohere_embedder

        return mem

    # -- MemoryBackend interface ----------------------------------------------

    async def add(
        self,
        content: str,
        container_tag: str,
        metadata: dict | None = None,
    ) -> None:
        try:
            mem = self._ensure_mem0()
            await mem.add(
                content,
                user_id=self._user_id,
                agent_id=container_tag,
                metadata=metadata,
            )
        except Exception:
            logger.warning(
                "Mem0Backend.add failed (container={}); memory not stored",
                container_tag,
            )

    async def search(
        self,
        query: str,
        container_tag: str,
        limit: int = 10,
    ) -> list[MemoryHit]:
        try:
            mem = self._ensure_mem0()
            result = await mem.search(
                query,
                user_id=self._user_id,
                agent_id=container_tag,
                limit=limit,
            )
            hits = result.get("results") if isinstance(result, dict) else (result or [])
            return [
                MemoryHit(
                    content=h.get("memory", ""),
                    score=h.get("score", 1.0),
                    memory_id=h.get("id"),
                    metadata=h.get("metadata") or {},
                )
                for h in hits
            ]
        except Exception:
            logger.warning(
                "Mem0Backend.search failed (query={!r}, container={}); returning []",
                query[:60],
                container_tag,
            )
            return []

    async def get_profile(self, container_tag: str) -> UserProfile:
        try:
            mem = self._ensure_mem0()
            result = await mem.get_all(
                user_id=self._user_id,
                agent_id=container_tag,
                limit=_PROFILE_RECENT + _PROFILE_STATIC,
            )
            entries = result.get("results") if isinstance(result, dict) else (result or [])
            contents = [e.get("memory", "") for e in entries if e.get("memory")]

            # Heuristic split: oldest entries → static (stable facts),
            # most recent N → dynamic (recent context)
            if len(contents) > _PROFILE_RECENT:
                static = contents[:-_PROFILE_RECENT][-_PROFILE_STATIC:]
                dynamic = contents[-_PROFILE_RECENT:]
            else:
                static = []
                dynamic = contents

            return UserProfile(static=static, dynamic=dynamic)
        except Exception:
            logger.warning(
                "Mem0Backend.get_profile failed (container={}); returning empty profile",
                container_tag,
            )
            return UserProfile()

    async def forget(self, memory_id: str, container_tag: str) -> None:
        try:
            mem = self._ensure_mem0()
            await mem.delete(memory_id)
        except Exception:
            logger.warning(
                "Mem0Backend.forget failed (memory_id={}, container={})",
                memory_id,
                container_tag,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str | None:
    """Read from env, also loading ~/.nanobot/.env.local if not already loaded."""
    _load_env_local()
    return os.environ.get(key)


_env_local_loaded = False


def _load_env_local() -> None:
    global _env_local_loaded
    if _env_local_loaded:
        return
    env_file = Path("~/.nanobot/.env.local").expanduser()
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError as exc:
            logger.warning("Could not load ~/.nanobot/.env.local: {}", exc)
    _env_local_loaded = True
