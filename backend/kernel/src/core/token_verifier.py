"""TokenVerifier — derive the request owner from a *verified* credential.

The remote/SaaS seam. Today the kernel trusts the ``X-Valuz-Owner-Id`` header
(set by the trusted host) for the request owner. That model breaks the moment
the caller is an UNTRUSTED, per-task sandbox: a sandbox could set any owner
header and read/write another user's rows. So in the remote deployment the
owner must come from a credential the sandbox cannot forge — a signed JWT.

``TokenVerifier`` is that seam. OSS binds :class:`NullTokenVerifier` (no
token identity → the header path stays authoritative for the in-process /
trusted-host mount, zero behaviour change). A SaaS overlay binds a real
signing-key/JWKS-backed verifier whose claims become the owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OwnerClaims:
    """The verified identity extracted from a request credential."""

    user_id: str
    session_id: str | None = None


class TokenVerifier(Protocol):
    """Verify a bearer credential and return its owner claims, or ``None``.

    - Returns ``None`` when no usable token-based identity is present — the
      caller then falls back to the ``X-Valuz-Owner-Id`` header path.
    - Raises (mapped to 401 by the caller) on a present-but-invalid
      credential (bad signature / expired), so a forged token is a hard
      failure, never a silent fallback to a caller-supplied header.
    """

    def verify(self, token: str | None) -> OwnerClaims | None: ...


class NullTokenVerifier:
    """Default OSS verifier: no token-based identity.

    Always returns ``None`` so ``get_owner_id`` keeps using the
    ``X-Valuz-Owner-Id`` header (the trusted in-process / host mount). A SaaS
    overlay swaps this for a signing-key-backed implementation.
    """

    def verify(self, token: str | None) -> OwnerClaims | None:
        return None
