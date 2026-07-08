"""Curated single-agent marketplace templates.

Loaded from ``resources/marketplace/agent_templates.json`` — Valuz-authored
content. SkillHub expert packs may inform internal curation, but every
published Agent template is owned and presented as Valuz official content.
Bilingual fields
use the same ``Text`` shape as agent packs and resolve against the request
locale.

The ``skills`` list is display metadata (what the agent is designed around),
not an auto-install manifest — installing the template creates the library
agent only; equipping skills stays an explicit user step.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from valuz_agent.modules.packs_common.manifest import Text, resolve_text

logger = logging.getLogger(__name__)

__all__ = ["AgentTemplateDef", "TemplateConnector", "load_agent_templates", "resolve_text"]


class TemplateConnector(BaseModel):
    name: Text
    requirement: Literal["required", "optional", "api_key", "cost"] = "optional"


class AgentTemplateDef(BaseModel):
    """One curated single-agent template as authored in the resource file."""

    id: str
    slug: str
    name: Text
    role: Text = ""
    instructions: Text = ""
    icon: str = "users"
    category: str = "research"
    category_label: Text = ""
    runtime: str = "claude_agent"
    effort: str | None = None
    source: Literal["valuz_official"] = "valuz_official"
    skills: list[Text] = Field(default_factory=list)
    connectors: list[TemplateConnector] = Field(default_factory=list)


class _TemplateFile(BaseModel):
    schema_version: int = 1
    templates: list[AgentTemplateDef] = Field(default_factory=list)


def _templates_path() -> Path:
    """``backend/valuz_agent/resources/marketplace/agent_templates.json``."""
    return (
        Path(__file__).resolve().parent.parent.parent
        / "resources"
        / "marketplace"
        / "agent_templates.json"
    )


@lru_cache(maxsize=1)
def load_agent_templates() -> tuple[AgentTemplateDef, ...]:
    """All curated templates in display order; empty on a bad resource file."""
    path = _templates_path()
    if not path.is_file():
        logger.warning("marketplace agent templates resource missing: %s", path)
        return ()
    try:
        data = _TemplateFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.exception("failed to load marketplace agent templates: %s", path)
        return ()
    return tuple(data.templates)
