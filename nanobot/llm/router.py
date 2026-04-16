"""LLM Router — single source of truth for model/API selection by role.

Usage:
    router = LLMRouter()
    text = await router.complete(role="council_judge", system="...", user="...")
    text = await router.complete(role="council_persona", persona="vc_unicorni", system="...", user="...")

Roles:
    conversation        → MiniMax M2 (API ufficiale)
    council_persona     → dipende dalla persona (vedi PERSONA_ROUTES)
    council_judge       → Claude Opus (Anthropic primario, OR fallback)
    validate_spec       → Claude Opus (Anthropic primario, OR fallback)
    review_workflow     → Claude Opus (Anthropic primario, OR fallback)
    weekly_audit        → Claude Opus (Anthropic primario, OR fallback)

Fallback per Claude (da CLAUDE.md §1.1):
    401  → fallback immediato a OpenRouter
    5xx  → fallback immediato a OpenRouter
    429  → 3 retry con backoff (5s, 15s, 45s) poi OpenRouter
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Literal

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Costanti modelli
# ---------------------------------------------------------------------------

_ANTHROPIC_SONNET = "claude-sonnet-4-5"
_ANTHROPIC_OPUS = "claude-opus-4-5"
_OR_SONNET = "anthropic/claude-sonnet-4-5"
_OR_OPUS = "anthropic/claude-opus-4-5"
_OR_GPT4O = "openai/gpt-4o"
_OR_GEMINI = "google/gemini-2.5-pro"
_OR_GROK = "x-ai/grok-4"
_OR_DEEPSEEK = "deepseek/deepseek-chat-v3-0324"  # fallback se Grok non disponibile

# Mapping persona → (primary_api, primary_model, fallback_api, fallback_model)
# primary_api: "anthropic" | "openrouter"
PERSONA_ROUTES: dict[str, dict] = {
    "voce-cliente": {
        "api": "anthropic",
        "model": _ANTHROPIC_SONNET,
        "or_model": _OR_SONNET,
    },
    "vc-unicorni": {
        "api": "openrouter",
        "model": _OR_GPT4O,
        "or_model": _OR_GPT4O,
    },
    "bartlett": {
        "api": "openrouter",
        "model": _OR_GEMINI,
        "or_model": _OR_GEMINI,
    },
    "visionario": {
        "api": "openrouter",
        "model": _OR_GROK,
        "or_model": _OR_DEEPSEEK,  # fallback automatico se Grok non disponibile
    },
    "jobs": {
        "api": "anthropic",
        "model": _ANTHROPIC_OPUS,
        "or_model": _OR_OPUS,
    },
    "munger": {
        "api": "anthropic",
        "model": _ANTHROPIC_SONNET,
        "or_model": _OR_SONNET,
    },
    "giudice": {
        "api": "anthropic",
        "model": _ANTHROPIC_OPUS,
        "or_model": _OR_OPUS,
    },
}

SUPERVISOR_ROUTES: dict[str, dict] = {
    "validate_spec": {"api": "anthropic", "model": _ANTHROPIC_OPUS, "or_model": _OR_OPUS},
    "review_workflow": {"api": "anthropic", "model": _ANTHROPIC_OPUS, "or_model": _OR_OPUS},
    "weekly_audit": {"api": "anthropic", "model": _ANTHROPIC_OPUS, "or_model": _OR_OPUS},
    "council_judge": {"api": "anthropic", "model": _ANTHROPIC_OPUS, "or_model": _OR_OPUS},
}

_CLAUDE_RETRY_DELAYS = [5.0, 15.0, 45.0]


@dataclass
class LLMRouter:
    """Route completions to the right model/API by role."""

    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    openrouter_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", "")
    )
    llm_log_path: str = field(
        default_factory=lambda: os.path.expanduser(
            os.environ.get("NANOBOT_LLM_LOG", "~/.nanobot/logs/llm-routing.log")
        )
    )
    timeout: float = 60.0

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        persona: str | None = None,
        max_tokens: int = 1500,
    ) -> str:
        """Return a completion string for the given role.

        Args:
            role: One of 'conversation', 'council_persona', 'council_judge',
                  'validate_spec', 'review_workflow', 'weekly_audit'.
            system: System prompt.
            user: User message.
            persona: Required when role == 'council_persona'.
            max_tokens: Max tokens in the response.

        Returns:
            The assistant text response.

        Raises:
            RuntimeError: If all API attempts fail.
        """
        if role == "council_persona":
            if persona is None:
                raise ValueError("persona is required when role='council_persona'")
            route = PERSONA_ROUTES.get(persona)
            if route is None:
                raise ValueError(f"Unknown persona: {persona!r}. Known: {list(PERSONA_ROUTES)}")
        elif role in SUPERVISOR_ROUTES:
            route = SUPERVISOR_ROUTES[role]
        else:
            raise ValueError(f"Unknown role: {role!r}")

        return await self._call_with_fallback(route, system=system, user=user, max_tokens=max_tokens)

    # -----------------------------------------------------------------------
    # Internal routing
    # -----------------------------------------------------------------------

    async def _call_with_fallback(
        self,
        route: dict,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        primary_api = route["api"]
        primary_model = route["model"]
        or_model = route["or_model"]

        if primary_api == "anthropic":
            try:
                return await self._call_anthropic_with_retry(
                    model=primary_model,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                )
            except _FallbackToOR as exc:
                self._log_fallback(primary_model, or_model, str(exc))
                return await self._call_openrouter(
                    model=or_model, system=system, user=user, max_tokens=max_tokens
                )
        else:
            # OpenRouter-native persona (GPT-4o, Gemini, Grok)
            try:
                return await self._call_openrouter(
                    model=primary_model, system=system, user=user, max_tokens=max_tokens
                )
            except Exception as exc:
                if primary_model != or_model:
                    self._log_fallback(primary_model, or_model, str(exc))
                    return await self._call_openrouter(
                        model=or_model, system=system, user=user, max_tokens=max_tokens
                    )
                raise

    async def _call_anthropic_with_retry(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        """Call Anthropic API with retry on 429 and immediate fallback on 401/5xx."""
        for attempt, delay in enumerate([0.0] + _CLAUDE_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._call_anthropic(
                    model=model, system=system, user=user, max_tokens=max_tokens
                )
            except _RateLimitError:
                if attempt < len(_CLAUDE_RETRY_DELAYS):
                    logger.warning(
                        "Anthropic 429 on {}, retry {}/{} in {}s",
                        model, attempt + 1, len(_CLAUDE_RETRY_DELAYS), _CLAUDE_RETRY_DELAYS[attempt] if attempt < len(_CLAUDE_RETRY_DELAYS) else "N/A",
                    )
                    continue
                raise _FallbackToOR(f"Anthropic 429 exhausted retries for {model}")
            except _AuthError as exc:
                raise _FallbackToOR(f"Anthropic 401 for {model}: {exc}") from exc
            except _ServerError as exc:
                raise _FallbackToOR(f"Anthropic 5xx for {model}: {exc}") from exc
        raise _FallbackToOR(f"Anthropic retries exhausted for {model}")

    async def _call_anthropic(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        if not self.anthropic_api_key:
            raise _FallbackToOR("ANTHROPIC_API_KEY not set")

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )

        if resp.status_code == 401:
            raise _AuthError(resp.text)
        if resp.status_code == 429:
            raise _RateLimitError(resp.text)
        if resp.status_code >= 500:
            raise _ServerError(f"{resp.status_code}: {resp.text}")
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic unexpected {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        return data["content"][0]["text"]

    async def _call_openrouter(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openrouter_api_key}",
                    "content-type": "application/json",
                    "HTTP-Referer": "https://github.com/nanobot",
                    "X-Title": "nanobot council",
                },
                json=payload,
            )

        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]

        # Grok fallback: se il modello non è disponibile su OR, tenta DeepSeek
        if resp.status_code in (400, 404) and model == _OR_GROK:
            logger.warning("Grok-4 non disponibile su OpenRouter, fallback a DeepSeek V3")
            return await self._call_openrouter(
                model=_OR_DEEPSEEK, system=system, user=user, max_tokens=max_tokens
            )

        raise RuntimeError(f"OpenRouter {resp.status_code} for {model}: {resp.text[:300]}")

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------

    def _log_fallback(self, from_model: str, to_model: str, reason: str) -> None:
        try:
            log_dir = os.path.dirname(self.llm_log_path)
            os.makedirs(log_dir, exist_ok=True)
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            line = f"[{ts}] FALLBACK {from_model} → {to_model} | reason: {reason}\n"
            with open(self.llm_log_path, "a", encoding="utf-8") as f:
                f.write(line)
            logger.warning("LLM fallback: {} → {} ({})", from_model, to_model, reason)
        except Exception:
            pass  # logging never breaks the flow


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------

class _FallbackToOR(Exception):
    """Trigger OpenRouter fallback."""


class _RateLimitError(Exception):
    """Anthropic 429."""


class _AuthError(Exception):
    """Anthropic 401."""


class _ServerError(Exception):
    """Anthropic 5xx."""
