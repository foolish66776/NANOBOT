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
        mem0_cfg = config.get("memory", {}).get("mem0", {})
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
