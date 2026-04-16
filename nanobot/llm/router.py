"""LLM Router — single source of truth for model/API selection by role.

Usage:
    router = LLMRouter()
    text = await router.complete(role="council_judge", system="...", user="...")
    text = await router.complete(role="council_persona", persona="vc_unicorni", system="...", user="...")
    text = await router.complete(role="build", system="...", user="...")

Roles:
    conversation        → MiniMax M2 (API ufficiale)
    build               → MiniMax M2 (generazione workflow JSON)
    council_persona     → dipende dalla persona (vedi PERSONA_ROUTES)
    council_judge       → DeepSeek V3.2 (OpenRouter)
    validate_spec       → GLM-5.1 (OpenRouter z-ai)
    review_workflow     → GLM-5.1 (OpenRouter z-ai)
    weekly_audit        → GLM-5.1 (OpenRouter z-ai)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

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
_OR_DEEPSEEK_V32 = "deepseek/deepseek-v3.2"       # council personas + judge
_OR_GLM = "z-ai/glm-5.1"                          # supervisor checkpoints (validate/review/audit)

# Mapping persona → (primary_api, primary_model, fallback_api, fallback_model)
# primary_api: "anthropic" | "openrouter"
PERSONA_ROUTES: dict[str, dict] = {
    "voce-cliente": {
        "api": "openrouter",
        "model": _OR_DEEPSEEK_V32,
        "or_model": _OR_DEEPSEEK_V32,
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
        "api": "openrouter",
        "model": _OR_DEEPSEEK_V32,
        "or_model": _OR_DEEPSEEK_V32,
    },
    "munger": {
        "api": "openrouter",
        "model": _OR_DEEPSEEK_V32,
        "or_model": _OR_DEEPSEEK_V32,
    },
    "giudice": {
        "api": "openrouter",
        "model": _OR_DEEPSEEK_V32,
        "or_model": _OR_DEEPSEEK_V32,
    },
}

SUPERVISOR_ROUTES: dict[str, dict] = {
    "validate_spec": {"api": "openrouter", "model": _OR_GLM, "or_model": _OR_GLM},
    "review_workflow": {"api": "openrouter", "model": _OR_GLM, "or_model": _OR_GLM},
    "weekly_audit": {"api": "openrouter", "model": _OR_GLM, "or_model": _OR_GLM},
    "council_judge": {"api": "openrouter", "model": _OR_DEEPSEEK_V32, "or_model": _OR_DEEPSEEK_V32},
    # build: MiniMax M2 per generazione workflow JSON (CLAUDE.md §7.2 Step 2)
    "build": {"api": "minimax"},
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
    minimax_api_key: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_API_KEY", "")
    )
    minimax_base_url: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io")
    )
    minimax_group_id: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_GROUP_ID", "")
    )
    minimax_model: str = field(
        default_factory=lambda: os.environ.get("MINIMAX_DEFAULT_MODEL", "minimax/minimax-m2.7")
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

        # MiniMax routes (build, conversation) handled separately
        if route.get("api") == "minimax":
            return await self._call_minimax(system=system, user=user, max_tokens=max_tokens)

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
        if resp.status_code in (400, 402, 403):
            # credit balance too low, payment required, forbidden → fallback to OR
            raise _AuthError(f"{resp.status_code}: {resp.text[:200]}")
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

    async def _call_minimax(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        """Chiama MiniMax M2 via API ufficiale (OpenAI-compat endpoint)."""
        if not self.minimax_api_key:
            raise RuntimeError("MINIMAX_API_KEY non configurata")

        # MiniMax espone un endpoint OpenAI-compatibile
        base = self.minimax_base_url.rstrip("/")
        url = f"{base}/v1/text/chatcompletion_v2"

        # L'API ufficiale MiniMax si aspetta il nome senza prefisso "minimax/"
        # (es. "minimax-m2.7"), mentre la env var usa la forma "minimax/minimax-m2.7"
        model = self.minimax_model
        if model.startswith("minimax/"):
            model = model[len("minimax/"):]

        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.minimax_api_key}",
            "Content-Type": "application/json",
        }
        if self.minimax_group_id:
            headers["GroupId"] = self.minimax_group_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code == 200:
            data = resp.json()
            # MiniMax usa lo stesso formato di OpenAI
            choices = data.get("choices") or []
            if choices:
                return choices[0]["message"]["content"]
            raise RuntimeError(f"MiniMax risposta senza choices: {data}")

        raise RuntimeError(f"MiniMax {resp.status_code}: {resp.text[:300]}")

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
