"""Owner-parametrized data-service env for a sandboxed kernel.

A kernel running in a sandbox on a durable store (``KERNEL_STORE=pg|remote``) must
reach the host DataService over HTTP+JWT and **never hold the DSN**. Assembling
that env (``KERNEL_STORE=remote`` + ``VALUZ_DATA_API_*``) used to be inlined in the
Seatbelt driver with an ambient ``resolve_local_user_id()``.

Extracting it here lets any driver reuse it and, crucially, pass the owner
**explicitly**: the local Seatbelt driver passes the device user, while a
multi-tenant cloud driver (commercial ``valuz-pool``) passes the request
principal so each user's sandbox gets a token scoped to *that* owner — the
mint half of the per-owner isolation the DataService verifies. See the
commercial ADR-012 PR ⑥.
"""

from __future__ import annotations

import os


def data_service_env(*, owner_user_id: str, host_callback_url: str) -> dict[str, str]:
    """Env pointing a sandboxed kernel at the host DataService for ``owner_user_id``.

    Returns an **empty dict** when the host is not on a durable store
    (``KERNEL_STORE=local``) or has no reachable ``host_callback_url`` — the
    sandbox then keeps its own local ``kernel.db``. Otherwise returns
    ``KERNEL_STORE=remote`` + the ``VALUZ_DATA_API_*`` triple; the token is HS256,
    signed with ``owner_user_id``'s per-owner secret, and carries only that owner
    — never the DB credential.
    """
    if os.environ.get("KERNEL_STORE", "local") not in ("pg", "remote") or not host_callback_url:
        return {}
    from valuz_agent.boot.kernel import mint_data_service_token
    from valuz_agent.infra.data_service_secret import get_or_create_ds_secret

    secret = get_or_create_ds_secret(owner_user_id)
    return {
        "KERNEL_STORE": "remote",
        "VALUZ_DATA_API_KIND": "http",
        # ADR-013: new sandboxes get the ``/_internal/...`` path; ``/internal/...``
        # stays mounted (see ``api/app.py::_mount_internal``) for sessions whose
        # persisted config predates the rename.
        "VALUZ_DATA_API_URL": host_callback_url.rstrip("/") + "/_internal/data",
        "VALUZ_DATA_API_TOKEN": mint_data_service_token(secret, user_id=owner_user_id),
    }


__all__ = ["data_service_env"]
