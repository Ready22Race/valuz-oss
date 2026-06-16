"""``valuz_agent.facade`` — the host application's stable, overlay-facing API.

Importable from overlays (part of the OSS↔overlay contract). Today it exposes
the resource library; more host-provided application services can be added here.
"""

from valuz_agent.facade.resources import (
    ResourceKind,
    ResourceLibrary,
    ResourceRef,
    ResourceSnapshot,
    get_resource_library,
)

__all__ = [
    "ResourceKind",
    "ResourceLibrary",
    "ResourceRef",
    "ResourceSnapshot",
    "get_resource_library",
]
