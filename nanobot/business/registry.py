"""BusinessRegistry — loads and resolves BusinessContext from config."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from nanobot.business.context import BusinessContext

if TYPE_CHECKING:
    pass

# Marker patterns for in-message business-line selection.
# Supported:
#   /bl:<id>   at the start or anywhere in the message
#   #<id>      at the very end of the message
_BL_PREFIX_RE = re.compile(r"(?:^|\s)/bl:(\w[\w-]*)", re.IGNORECASE)
_BL_HASHTAG_RE = re.compile(r"#(\w[\w-]*)$", re.IGNORECASE)


class BusinessRegistry:
    """Loads business lines from config and resolves them per-message.

    Usage::

        registry = BusinessRegistry.from_config(config_dict)
        ctx = registry.resolve("hello /bl:concr3tica")
        # ctx.id == "concr3tica", message cleaned of the marker
    """

    def __init__(
        self,
        contexts: dict[str, BusinessContext],
        default_id: str = "personal",
    ) -> None:
        self._contexts = contexts
        self._default_id = default_id

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict) -> BusinessRegistry:
        """Build a registry from the root nanobot config dict.

        Reads ``businessLines`` and ``defaultBusinessLine`` keys.
        Falls back to a single "personal" line if neither is present.
        """
        lines_cfg: dict = config.get("businessLines") or config.get("business_lines", {})
        default_id: str = (
            config.get("defaultBusinessLine")
            or config.get("default_business_line", "personal")
        )

        contexts: dict[str, BusinessContext] = {}
        for bl_id, bl_cfg in lines_cfg.items():
            if not isinstance(bl_cfg, dict):
                continue
            contexts[bl_id] = BusinessContext(
                id=bl_id,
                name=bl_cfg.get("name", bl_id),
                container_tag=bl_cfg.get("containerTag") or bl_cfg.get("container_tag", bl_id),
                static_profile=bl_cfg.get("staticProfile") or bl_cfg.get("static_profile", ""),
                skills=bl_cfg.get("skills", ["*"]),
                description=bl_cfg.get("description", ""),
                model=bl_cfg.get("model") or None,
            )

        # If no business lines configured, create a minimal default.
        if not contexts:
            memory_cfg = config.get("memory", {})
            container_tag = memory_cfg.get("containerTag") or memory_cfg.get("container_tag", "personal")
            contexts[default_id] = BusinessContext(
                id=default_id,
                name=default_id.title(),
                container_tag=container_tag,
                static_profile="",
            )

        # Ensure the default_id exists.
        if default_id not in contexts:
            first = next(iter(contexts))
            default_id = first

        return cls(contexts, default_id)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, bl_id: str) -> BusinessContext | None:
        """Return the BusinessContext for the given id, or None."""
        return self._contexts.get(bl_id)

    def get_or_default(self, bl_id: str | None = None) -> BusinessContext:
        """Return context for bl_id, or the default if not found."""
        if bl_id and bl_id in self._contexts:
            return self._contexts[bl_id]
        return self._contexts[self._default_id]

    def list(self) -> list[BusinessContext]:
        """Return all registered business contexts."""
        return list(self._contexts.values())

    @property
    def default_id(self) -> str:
        return self._default_id

    # ------------------------------------------------------------------
    # Message-level resolution
    # ------------------------------------------------------------------

    def resolve(
        self, message: str, override_id: str | None = None
    ) -> tuple[BusinessContext, str]:
        """Extract a business-line marker from the message and return
        the resolved context plus the cleaned message.

        Resolution priority:
          1. override_id (explicit CLI flag or channel config)
          2. /bl:<id> marker anywhere in the message
          3. #<id> hashtag at the end of the message
          4. default business line

        The marker is stripped from the returned message.

        Returns:
            (context, cleaned_message)
        """
        if override_id and override_id in self._contexts:
            return self._contexts[override_id], message

        # /bl:<id> marker
        m = _BL_PREFIX_RE.search(message)
        if m:
            bl_id = m.group(1).lower()
            if bl_id in self._contexts:
                cleaned = _BL_PREFIX_RE.sub("", message).strip()
                return self._contexts[bl_id], cleaned

        # #<id> hashtag at the end
        m = _BL_HASHTAG_RE.search(message)
        if m:
            bl_id = m.group(1).lower()
            if bl_id in self._contexts:
                cleaned = message[: m.start()].strip()
                return self._contexts[bl_id], cleaned

        return self._contexts[self._default_id], message
