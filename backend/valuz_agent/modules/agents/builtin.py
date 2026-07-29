"""Canonical OSS system-Agent identity.

Valurion is a real Agent row with owner scope. Product instructions and
effective resources are resolved dynamically when a session is created; they
are deliberately not copied into this row.
"""

from __future__ import annotations

VALURION_SLUG = "valurion"
LEGACY_VALUZ_HELPER_SLUG = "valuz-helper"
VALURION_NAME = "Valurion"
VALURION_DESCRIPTION = (
    "Your built-in assistant with access to all resources currently available to you."
)
VALURION_AVATAR = "bot"

SYSTEM_MANAGED_FIELDS: dict[str, object] = {
    "slug": VALURION_SLUG,
    "name": VALURION_NAME,
    "description": VALURION_DESCRIPTION,
    "instructions": "",
    "skills": [],
    "connector_types": [],
    "knowledge_scope": [],
    "kind": "system",
    "source": "builtin",
    "resource_policy": "all_available",
    "inherit_global_instructions": True,
    "permission_mode": "full_access",
    "readonly": True,
    "deletable": False,
    "avatar": VALURION_AVATAR,
}


def canonical_agent_slug(slug: str) -> str:
    """Resolve the compatibility read alias without permitting legacy writes."""
    return VALURION_SLUG if slug == LEGACY_VALUZ_HELPER_SLUG else slug


__all__ = [
    "LEGACY_VALUZ_HELPER_SLUG",
    "SYSTEM_MANAGED_FIELDS",
    "VALURION_AVATAR",
    "VALURION_DESCRIPTION",
    "VALURION_NAME",
    "VALURION_SLUG",
    "canonical_agent_slug",
]
