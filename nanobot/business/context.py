"""BusinessContext — first-class representation of a business line."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BusinessContext:
    """A named business-line context that scopes memories and skills.

    Attributes:
        id:             Short identifier, e.g. "concr3tica".
        name:           Display name, e.g. "Concr3tica".
        container_tag:  Namespace for the memory backend, e.g. "personal".
        static_profile: Multi-line system-prompt fragment injected before
                        the dynamic memory block.
        skills:         Enabled skill names for this context; ["*"] = all.
        description:    Optional human-readable description (not injected).
    """

    id: str
    name: str
    container_tag: str
    static_profile: str
    skills: list[str] = field(default_factory=lambda: ["*"])
    description: str = ""
    model: str | None = None  # optional LLM override for this business line
