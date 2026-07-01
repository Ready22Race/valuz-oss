"""Phase B — data-service / RemoteStoreHttp contract drift guard.

The data service must expose exactly one ``POST /rpc/{op}`` per StorePort
method the client calls — no missing route (client breaks) and no extra
(dead). Adding/removing a StorePort op forces a conscious update here.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from app.data_service import create_data_service_app
from src.core.token_verifier import NullTokenVerifier

# The StorePort surface the remote transport carries (1:1 with RemoteStoreHttp
# _*_once methods and the data-service /rpc routes).
EXPECTED_OPS = {
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "save_message",
    "load_message",
    "list_messages_for_session",
    "append_event",
    "get_events",
    "get_events_for_message",
    "get_events_after",
    "get_events_window",
    "usage_rollup",
}


def test_data_service_exposes_exactly_the_storeport_ops():
    app = create_data_service_app(store=object(), verifier=NullTokenVerifier())
    rpc_ops = {
        route.path.removeprefix("/rpc/")
        for route in app.routes
        if getattr(route, "path", "").startswith("/rpc/")
    }
    assert rpc_ops == EXPECTED_OPS


def test_client_implements_every_op():
    from src.adapters.remote_store_http import RemoteStoreHttp

    for op in EXPECTED_OPS:
        assert callable(getattr(RemoteStoreHttp, f"_{op}_once", None)), f"client missing _{op}_once"
