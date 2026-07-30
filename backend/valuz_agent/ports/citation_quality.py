"""Edition hook for trusted, declarative citation quality policies.

The host resolves a policy snapshot for an explicit owner and stamps the
JSON-safe snapshot into the kernel session before each turn.  The kernel
executes the generic policy evaluator; edition Python code never crosses the
host/kernel boundary and user-writable files are never consulted.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class CitationQualityPolicySnapshot:
    policy_id: str
    revision: str
    mode: Literal["required-on-evidence", "strict-domain"]
    config: dict[str, Any]

    def session_metadata(self) -> dict[str, Any]:
        payload = {
            "policy_id": self.policy_id,
            "revision": self.revision,
            "mode": self.mode,
            "config": copy.deepcopy(self.config),
        }
        # Fail at the trusted host boundary instead of sending an unserializable
        # or unbounded object into a remote kernel.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 128_000:
            raise ValueError("citation quality policy exceeds 128 KiB")
        return payload


class CitationQualityPolicyPort(Protocol):
    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot | None: ...


class NoopCitationQualityPolicy:
    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> None:
        del user_id, session_metadata
        return None


__all__ = [
    "CitationQualityPolicyPort",
    "CitationQualityPolicySnapshot",
    "NoopCitationQualityPolicy",
]
