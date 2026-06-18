"""An OAuth connector stuck at "connecting" should read as "pending_auth".

OAuth connectors connect through the login flow (pending_auth → connected), so a
row left at the create-time "connecting" state is really "not connected, needs
login". The view must surface that as ``pending_auth`` (→ 未连接, and the nav dot
fires) instead of a perpetual 连接中. Non-OAuth connectors keep "connecting" as a
real in-flight probe.
"""

from __future__ import annotations

import pytest

from valuz_agent.modules.connectors.models import ConnectorRow
from valuz_agent.modules.connectors.service import _effective_status


def _row(auth_type: str, status: str) -> ConnectorRow:
    return ConnectorRow(
        id="c1",
        user_id="u",
        slug="s",
        display_name="S",
        connector_type="custom",
        transport="http",
        auth_type=auth_type,
        url="https://example.com/mcp",
        enabled=True,
        status=status,
    )


@pytest.mark.parametrize(
    ("auth_type", "status", "expected"),
    [
        ("oauth", "connecting", "pending_auth"),  # the fix
        ("oauth", "connected", "connected"),  # real connection untouched
        ("oauth", "error", "error"),  # real failure untouched
        ("oauth", "pending_auth", "pending_auth"),  # already correct
        ("none", "connecting", "connecting"),  # non-oauth probe is real
        ("bearer", "connecting", "connecting"),  # non-oauth probe is real
    ],
)
def test_effective_status(auth_type: str, status: str, expected: str) -> None:
    assert _effective_status(_row(auth_type, status)) == expected
