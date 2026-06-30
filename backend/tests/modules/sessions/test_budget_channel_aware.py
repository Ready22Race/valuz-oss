"""The pre-turn wallet check must be channel-aware.

``_enforce_budget`` hands the session's **locked channel** to the billing port
so an overlay can skip enforcement for channels it does not meter (a user's own
direct API-key channel, org BYOK). Without the channel, an empty wallet wrongly
blocks a turn that would never spend a platform credit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.modules.sessions.errors import BudgetExceeded
from valuz_agent.modules.sessions.service import _enforce_budget
from valuz_agent.ports.billing import BudgetStatus, NoopBillingProvider
from valuz_agent.ports.extensions import ext


class _RecordingBilling:
    """Billing stub: records (user_id, provider_id) and returns a canned decision."""

    def __init__(self, decision: BudgetStatus) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str | None]] = []

    async def check_budget(self, user_id, estimated_cost=0.0, *, provider_id=None):  # type: ignore[no-untyped-def]
        self.calls.append((user_id, provider_id))
        return self.decision

    async def meter(self, event):  # type: ignore[no-untyped-def]  # pragma: no cover
        ...

    async def get_balance(self, user_id):  # type: ignore[no-untyped-def]  # pragma: no cover
        return None


def _session(
    *,
    owner: str | None = "u1",
    row_user_id: str | None = None,
    locked_provider_id: str | None = None,
) -> SimpleNamespace:
    valuz: dict[str, object] = {}
    if locked_provider_id is not None:
        valuz["locked_provider_id"] = locked_provider_id
    metadata: dict[str, object] = {"valuz": valuz}
    if owner is not None:
        metadata["owner_user_id"] = owner
    attrs: dict[str, object] = {"metadata": metadata}
    if row_user_id is not None:
        attrs["user_id"] = row_user_id
    return SimpleNamespace(**attrs)


async def test_passes_locked_channel_to_billing(monkeypatch):
    billing = _RecordingBilling(BudgetStatus(allowed=True))
    monkeypatch.setattr(ext, "billing", billing)

    await _enforce_budget(_session(locked_provider_id="user:my-openai"))

    assert billing.calls == [("u1", "user:my-openai")]


async def test_missing_locked_channel_passes_none(monkeypatch):
    billing = _RecordingBilling(BudgetStatus(allowed=True))
    monkeypatch.setattr(ext, "billing", billing)

    await _enforce_budget(_session(locked_provider_id=None))

    assert billing.calls == [("u1", None)]


async def test_uses_persisted_session_user_id_when_metadata_owner_missing(monkeypatch):
    billing = _RecordingBilling(BudgetStatus(allowed=True))
    monkeypatch.setattr(ext, "billing", billing)

    await _enforce_budget(
        _session(owner=None, row_user_id="u-from-session", locked_provider_id="valuz-channel")
    )

    assert billing.calls == [("u-from-session", "valuz-channel")]


async def test_uses_explicit_job_owner_when_session_owner_is_legacy_missing(monkeypatch):
    billing = _RecordingBilling(BudgetStatus(allowed=True))
    monkeypatch.setattr(ext, "billing", billing)

    await _enforce_budget(
        _session(owner=None, locked_provider_id="valuz-channel"),
        user_id="u-from-automation-row",
    )

    assert billing.calls == [("u-from-automation-row", "valuz-channel")]


async def test_raises_budget_exceeded_with_i18n_key(monkeypatch):
    billing = _RecordingBilling(
        BudgetStatus(
            allowed=False,
            reason="Balance exhausted.",
            message_key="commercial.billing.insufficientBalance",
        )
    )
    monkeypatch.setattr(ext, "billing", billing)

    with pytest.raises(BudgetExceeded) as ei:
        await _enforce_budget(_session(locked_provider_id="valuz-channel"))

    assert ei.value.message_key == "commercial.billing.insufficientBalance"
    assert ei.value.message == "Balance exhausted."


async def test_allowed_does_not_raise(monkeypatch):
    monkeypatch.setattr(ext, "billing", _RecordingBilling(BudgetStatus(allowed=True)))

    await _enforce_budget(_session(locked_provider_id="valuz-channel"))  # no raise == pass


async def test_noop_billing_tolerates_provider_id():
    # Contract: the default Noop provider accepts the new kwarg and stays
    # unlimited regardless of channel.
    status = await NoopBillingProvider().check_budget("u1", provider_id="anything")
    assert status.allowed is True
