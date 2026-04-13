"""Abstract MemoryBackend interface and shared data classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MemoryHit:
    content: str
    score: float
    metadata: dict = field(default_factory=dict)
    memory_id: str | None = None


@dataclass
class UserProfile:
    static: list[str] = field(default_factory=list)   # long-term facts
    dynamic: list[str] = field(default_factory=list)  # recent context


class MemoryBackend(ABC):
    @abstractmethod
    async def add(
        self,
        content: str,
        container_tag: str,
        metadata: dict | None = None,
    ) -> None: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        container_tag: str,
        limit: int = 10,
    ) -> list[MemoryHit]: ...

    @abstractmethod
    async def get_profile(self, container_tag: str) -> UserProfile: ...

    @abstractmethod
    async def forget(self, memory_id: str, container_tag: str) -> None: ...
