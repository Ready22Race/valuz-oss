"""Port: the LLM-provider extension point (ADR-011).

Supersedes the per-descriptor ``LLMProviderRegistry`` (ADR-007). An overlay
binds ONE ``LLMProvider`` via ``ext.llm_provider`` to contribute its channels
into the provider list and resolve their credentials on the call path. OSS
makes **zero judgement** about the contributed rows — it
appends them next to its own user rows (display) and falls through to
``resolve`` when a call targets an id it doesn't own (invoke).

Two methods, two lifecycles:

* ``list``    — display path. Called once per provider-list render. Returns
  self-judged, key-free :class:`~valuz_agent.modules.providers.schemas.LLMChannel`
  rows. The implementation does its own (cached) upstream catalog fetch so one
  enumeration hits the network once.
* ``resolve`` — invoke path. Called once per real LLM call to turn a row id into
  a live credential. May run with no request JWT (background automations), so
  the implementation must gate on a long-lived credential for the explicit
  ``user_id``, not the user session.

OSS binds :class:`NoopLLMProvider` by default (``list → []``,
``resolve → None``); the overlay replaces it at app-factory time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from valuz_agent.modules.providers.schemas import LLMChannel


@dataclass(frozen=True)
class ResolvedCredential:
    """A live credential for the invoke path — carries a key.

    Only ever flows through ``resolve``; never enters a display row.

    Attributes:
        api_base: Base URL handed to the kernel ``ModelProvider``.
        api_key: Bearer/key threaded into ``ModelProvider``.
        api_protocol: Kernel underscore form — ``anthropic`` /
            ``openai_completion`` / ``openai_response`` / ``gemini``.
    """

    api_base: str
    api_key: str
    api_protocol: str


@runtime_checkable
class LLMProvider(Protocol):
    """An extra source of provider rows. OSS makes zero judgement on content."""

    async def list(self) -> list[LLMChannel]: ...

    async def resolve(
        self, provider_id: str, *, user_id: str | None = None
    ) -> ResolvedCredential | None: ...


class NoopLLMProvider:
    """OSS default: contributes no rows and resolves nothing."""

    async def list(self) -> list[LLMChannel]:
        return []

    async def resolve(
        self, provider_id: str, *, user_id: str | None = None
    ) -> ResolvedCredential | None:
        return None


class SystemProviderImmutable(RuntimeError):  # noqa: N818 — domain error, not Error-suffixed
    """Raised when a write op targets a non-deletable contributed provider.

    A contributed (catalog) channel has ``deletable=False`` and no user-table
    row, so it can't be edited / deleted / tested via the user CRUD path.
    Carries the offending ``provider_id`` so the route layer can surface it;
    mapped to HTTP 409 by the providers router.
    """

    def __init__(self, provider_id: str) -> None:
        super().__init__(
            f"provider {provider_id!r} is system-managed and cannot be edited or deleted"
        )
        self.provider_id = provider_id


__all__ = [
    "NoopLLMProvider",
    "LLMProvider",
    "ResolvedCredential",
    "SystemProviderImmutable",
]
