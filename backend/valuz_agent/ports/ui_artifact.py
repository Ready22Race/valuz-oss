"""Generated-UI artifact sink port.

``generate_ui`` is edition-neutral: it turns a request into renderable UI and
returns it as the tool result. Editions that keep a durable UI artifact store
(e.g. a workbench with versioned pages) register a sink here; on every
successful generation the tool offers the generated document to the sink and,
when the sink persists a revision, appends the returned receipt to the tool
result so the conversation can render an adopt/bind affordance.

The sink NEVER binds anything — persistence of the revision is the sink's
choice, adoption stays a separate user-confirmed action in the edition's own
API (proposal/confirm, mirroring the automation contract). Sink failures are
swallowed: a broken sink must never break UI generation itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UiArtifactTargetHost:
    """Where the generated UI is meant to live, as claimed by the CALLER.

    The agent copies this from its host context (e.g. a workbench page's
    ``host_type``/``host_id``); the sink revalidates ownership server-side.
    """

    host_type: str
    host_id: str
    slot: str = "main"


@dataclass(frozen=True)
class UiArtifactReceipt:
    """What the sink persisted, surfaced to the conversation UI.

    ``expected_revision_id`` is the revision currently ADOPTED by the target
    host at generation time (None when the host has no binding yet) — the
    optimistic-concurrency token the confirm call must present.
    """

    artifact_id: str
    revision_id: str
    revision: int
    host_type: str | None = None
    host_id: str | None = None
    slot: str = "main"
    expected_revision_id: str | None = None


class UiArtifactSinkPort(Protocol):
    """Persist a successfully generated UI document as an artifact revision."""

    async def store_generated_ui(
        self,
        *,
        user_id: str,
        session_id: str | None,
        tool_use_id: str | None,
        target_host: UiArtifactTargetHost | None,
        request: str,
        protocol: str,
        content: str,
    ) -> UiArtifactReceipt | None: ...


def receipt_to_payload(receipt: UiArtifactReceipt) -> dict[str, Any]:
    return {
        "artifact_id": receipt.artifact_id,
        "revision_id": receipt.revision_id,
        "revision": receipt.revision,
        "host_type": receipt.host_type,
        "host_id": receipt.host_id,
        "slot": receipt.slot,
        "expected_revision_id": receipt.expected_revision_id,
    }
