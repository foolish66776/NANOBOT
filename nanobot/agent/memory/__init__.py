"""Memory package.

Public API — backward-compatible re-exports and factory.

Existing code that does::

    from nanobot.agent.memory import MemoryStore, Consolidator, Dream

continues to work unchanged.

New code should use the abstract interface::

    from nanobot.agent.memory import MemoryBackend, build_memory_backend
"""

from __future__ import annotations

# Re-export legacy classes so all existing imports keep working.
from nanobot.agent.memory.local import Consolidator, Dream, LocalMemory, MemoryStore

# Re-export abstract interface and data classes.
from nanobot.agent.memory.base import MemoryBackend, MemoryHit, UserProfile

__all__ = [
    # Legacy
    "MemoryStore",
    "Consolidator",
    "Dream",
    # Abstract interface
    "MemoryBackend",
    "MemoryHit",
    "UserProfile",
    "LocalMemory",
    "build_memory_backend",
]


def build_memory_backend(config: dict, workspace=None) -> MemoryBackend:
    """Factory: instantiate the configured MemoryBackend.

    Falls back to ``local`` if ``config["memory"]["backend"]`` is absent.

    Args:
        config:    The full nanobot config dict (from config.json).
        workspace: Path to the nanobot workspace directory.  Required for the
                   ``local`` backend; ignored by remote backends.
    """
    from pathlib import Path

    backend_name = config.get("memory", {}).get("backend", "local")

    if backend_name == "local":
        if workspace is None:
            raise ValueError("build_memory_backend: workspace is required for backend='local'")
        return LocalMemory(Path(workspace))

    if backend_name == "mem0":
        from nanobot.agent.memory.mem0_backend import Mem0Backend  # added in Phase 2
        mem0_cfg = dict(config.get("memory", {}).get("mem0", {}))
        # Auto-inject LLM credentials if not explicitly configured in memory.mem0.
        # Priority: explicit llmApiKey > explicit openrouterApiKey > main nanobot provider.
        # We do NOT auto-inject from providers.openrouter — that key may be unrelated to
        # the active LLM and is often invalid. Use the main-agent provider instead.
        if not mem0_cfg.get("llmApiKey") and not mem0_cfg.get("openrouterApiKey"):
            defaults = config.get("agents", {}).get("defaults", {})
            main_model = defaults.get("model") or defaults.get("model_override", "")
            provider_name = _guess_provider_name(main_model, config.get("providers", {}))
            if provider_name:
                prov = config.get("providers", {}).get(provider_name, {})
                api_key = prov.get("api_key") or prov.get("apiKey", "")
                api_base = prov.get("api_base") or prov.get("apiBase")
                if api_key:
                    mem0_cfg["llmApiKey"] = api_key
                    if api_base:
                        mem0_cfg["llmBaseUrl"] = api_base
                    else:
                        base = _get_default_base_url(provider_name)
                        if base:
                            mem0_cfg["llmBaseUrl"] = base
                    # When auto-injecting from the main provider, use its model name.
                    # This overrides any llmModel set in mem0 config because that model
                    # name was likely intended for a different provider (e.g. openrouter).
                    if main_model:
                        mem0_cfg["llmModel"] = main_model
        return Mem0Backend(mem0_cfg, workspace=workspace)

    if backend_name == "supermemory":
        from nanobot.agent.memory.supermemory import SupermemoryBackend  # added if needed
        sm_cfg = config.get("memory", {}).get("supermemory", {})
        return SupermemoryBackend(
            base_url=sm_cfg["baseUrl"],
            api_key=sm_cfg["apiKey"],
            timeout=sm_cfg.get("timeout", 15),
        )

    raise ValueError(f"Unknown memory backend: {backend_name!r}")


# ---------------------------------------------------------------------------
# Internal helpers for provider auto-detection
# ---------------------------------------------------------------------------

def _guess_provider_name(model: str, providers: dict) -> str | None:
    """Return the provider name whose keywords best match the model string."""
    try:
        from nanobot.providers.registry import PROVIDERS
    except ImportError:
        return None
    model_lower = model.lower()
    for spec in PROVIDERS:
        if any(kw.lower() in model_lower for kw in spec.keywords):
            prov = providers.get(spec.name, {})
            if prov.get("api_key") or prov.get("apiKey"):
                return spec.name
    return None


def _get_default_base_url(provider_name: str) -> str | None:
    """Return the default api_base for a provider, if it has one."""
    try:
        from nanobot.providers.registry import find_by_name
        spec = find_by_name(provider_name)
        return spec.default_api_base if spec else None
    except ImportError:
        return None
