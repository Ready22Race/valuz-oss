"""generate_ui → ui-artifact sink offering: receipt trailer + fail-open."""

from __future__ import annotations

import json

import pytest

from valuz_agent.modules.genui.tools import (
    UI_ARTIFACT_RECEIPT_CLOSE,
    UI_ARTIFACT_RECEIPT_OPEN,
    _offer_to_artifact_sinks,
    _parse_target_host,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.ui_artifact import UiArtifactReceipt


@pytest.fixture
def sinks():
    before = list(ext.ui_artifact_sinks)
    ext.ui_artifact_sinks[:] = []
    yield ext.ui_artifact_sinks
    ext.ui_artifact_sinks[:] = before


class _Recorder:
    def __init__(self, receipt: UiArtifactReceipt | None):
        self.receipt = receipt
        self.calls: list[dict] = []

    async def store_generated_ui(self, **kwargs):
        self.calls.append(kwargs)
        return self.receipt


class _Boom:
    async def store_generated_ui(self, **kwargs):
        raise RuntimeError("sink down")


def _receipt() -> UiArtifactReceipt:
    return UiArtifactReceipt(
        artifact_id="art-1",
        revision_id="rev-1",
        revision=3,
        host_type="finance.research-desk",
        host_id="desk",
        expected_revision_id="rev-0",
    )


async def _offer() -> str:
    return await _offer_to_artifact_sinks(
        user_id="u1",
        session_id="s1",
        tool_use_id="toolu_1",
        target_host=_parse_target_host(
            {"target_host": {"host_type": "finance.research-desk", "host_id": "desk"}}
        ),
        request="生成一个总工作台",
        protocol="openui",
        content="<Card/>",
    )


async def test_receipt_trailer_appended(sinks) -> None:
    recorder = _Recorder(_receipt())
    sinks.append(recorder)
    trailer = await _offer()
    assert trailer.startswith("\n" + UI_ARTIFACT_RECEIPT_OPEN)
    assert trailer.endswith(UI_ARTIFACT_RECEIPT_CLOSE)
    payload = json.loads(
        trailer[len("\n" + UI_ARTIFACT_RECEIPT_OPEN) : -len(UI_ARTIFACT_RECEIPT_CLOSE)]
    )
    assert payload["artifact_id"] == "art-1"
    assert payload["revision"] == 3
    assert payload["expected_revision_id"] == "rev-0"
    call = recorder.calls[0]
    assert call["user_id"] == "u1"
    assert call["target_host"].host_id == "desk"
    assert call["target_host"].slot == "main"


async def test_no_sink_no_trailer(sinks) -> None:
    assert await _offer() == ""


async def test_declining_sink_no_trailer(sinks) -> None:
    sinks.append(_Recorder(None))
    assert await _offer() == ""


async def test_failing_sink_is_skipped(sinks) -> None:
    sinks.append(_Boom())
    recorder = _Recorder(_receipt())
    sinks.append(recorder)
    trailer = await _offer()
    assert UI_ARTIFACT_RECEIPT_OPEN in trailer
    assert recorder.calls


def test_parse_target_host_variants() -> None:
    assert _parse_target_host({}) is None
    assert _parse_target_host({"target_host": "nope"}) is None
    assert _parse_target_host({"target_host": {"host_type": "x"}}) is None
    host = _parse_target_host(
        {"target_host": {"host_type": "t", "host_id": "i", "slot": "side"}}
    )
    assert host is not None and host.slot == "side"


class _Session:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata


def test_target_host_falls_back_to_the_turns_host_ref() -> None:
    """The model forgetting the argument must not silently detach the
    generation from the workbench the user is looking at."""
    session = _Session(
        {"valuz": {"host_ref": {"host_type": "finance.research-desk", "host_id": "desk"}}}
    )
    host = _parse_target_host({}, session)
    assert host is not None
    assert (host.host_type, host.host_id, host.slot) == (
        "finance.research-desk",
        "desk",
        "main",
    )


def test_explicit_target_host_overrides_the_turn_host() -> None:
    session = _Session(
        {"valuz": {"host_ref": {"host_type": "finance.research-desk", "host_id": "desk"}}}
    )
    host = _parse_target_host(
        {"target_host": {"host_type": "finance.company-research", "host_id": "US:NVDA"}},
        session,
    )
    assert host is not None and host.host_id == "US:NVDA"


def test_no_host_anywhere_is_still_none() -> None:
    assert _parse_target_host({}, _Session({})) is None
    assert _parse_target_host({}, _Session({"valuz": {"host_ref": {"host_type": "x"}}})) is None
