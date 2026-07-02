"""Host-persistent secret for the kernel DataService JWT seam.

The host owns ONE HS256 secret used to (a) **verify** bearer tokens on the
host-mounted DataService and (b) **mint** short-lived tokens for a sandbox
kernel — so the sandbox carries only a JWT, never a DB credential. The secret is
persisted through the OS keychain via the secret store (never plaintext on disk)
and generated on first use.

Single-tenant (OSS): keyed under the local owner. A SaaS overlay swaps this for
an asymmetric (JWKS) scheme behind the same ``TokenVerifier`` port — nothing else
changes.
"""

from __future__ import annotations

import secrets

from valuz_agent.infra import secret_store

DS_SECRET_REF = "data_service_jwt_secret"


def get_or_create_ds_secret(owner: str) -> str:
    """Return the host's DataService JWT secret, generating + persisting one on
    first use. Idempotent: the same secret is returned across restarts so tokens
    minted earlier keep verifying."""
    existing = secret_store.get(owner, DS_SECRET_REF)
    if existing:
        return existing
    value = secrets.token_urlsafe(32)
    secret_store.put(owner, DS_SECRET_REF, value)
    return value
