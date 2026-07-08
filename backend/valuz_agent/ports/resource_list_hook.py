"""Port: resource list hook for commercial overlay.

OSS mode uses ``NoopResourceListHook`` — list endpoints return data unchanged.
The commercial overlay binds a real hook via ``set_resource_list_hook()``
at app startup to inject cloud sync status and org-level resources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ResourceListHook(ABC):
    """Post-process resource list responses with external data (e.g. cloud sync status).

    An overlay implements this to inject cloud sync status and org-level resources
    into the host's resource list before it is returned to the client.
    """

    @abstractmethod
    async def apply(
        self, resource_type: str, items: list[dict[str, Any]], *, user_id: str
    ) -> list[dict[str, Any]]:
        """Receive local resource list, return post-processed list."""
        ...


class NoopResourceListHook(ResourceListHook):
    """Default hook — returns items unchanged."""

    async def apply(
        self, resource_type: str, items: list[dict[str, Any]], *, user_id: str
    ) -> list[dict[str, Any]]:
        return items


def get_resource_list_hook() -> ResourceListHook:
    from valuz_agent.ports.extensions import ext

    return ext.resource_list_hook


def set_resource_list_hook(hook: ResourceListHook) -> None:
    """Replace the hook (called by commercial app at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.resource_list_hook = hook


__all__ = [
    "NoopResourceListHook",
    "ResourceListHook",
    "get_resource_list_hook",
    "set_resource_list_hook",
]
