"""generate_ui → artifact revision: what gets recorded, and where it lands.

A generated page is a deliverable like any other, so it goes through
``deliver_artifact`` rather than a parallel store. Two things are specific to
it and pinned here: the document is EXTRACTED from the model's raw output
before anything is recorded, and the file name is derived from the host so
regenerating a page appends a version to the page the host already shows.
"""

from __future__ import annotations

import json

from valuz_agent.modules.genui.protocol import extract_a2ui_document
from valuz_agent.modules.genui.tools import _document_file_name, _parse_target_host
from valuz_agent.ports.ui_artifact import UiArtifactTargetHost

_CREATE = '{"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"openui"}}'
_COMPONENTS = (
    '{"version":"v0.9","updateComponents":{"surfaceId":"main","components":'
    '[{"id":"root","component":"Text","text":"hello"}]}}'
)
DOC = f"{_CREATE}\n{_COMPONENTS}"


# ── extraction ───────────────────────────────────────────────────────────


def test_should_keep_a_clean_document_verbatim() -> None:
    assert extract_a2ui_document(DOC) == DOC


def test_should_drop_the_closing_prose_models_add() -> None:
    # Real generations end with a summary. It is already in the conversation,
    # the renderer skips it, and storing it makes every downstream guard have
    # to know about it.
    raw = f"{DOC}\n研究工作台界面已在上方完整生成，包含以下三个模块：\n1. 市场概览"

    assert extract_a2ui_document(raw) == DOC


def test_should_refuse_a_document_cut_off_mid_json() -> None:
    # An aborted generation. Recording it would produce a bindable version
    # that renders as whatever stray component survived the truncation.
    assert extract_a2ui_document(f"{_CREATE}\n{_COMPONENTS[:80]}") is None


def test_should_refuse_output_with_no_components() -> None:
    assert extract_a2ui_document(_CREATE) is None


def test_should_refuse_empty_output() -> None:
    assert extract_a2ui_document("") is None


def test_prose_only_output_is_refused() -> None:
    # The model answered in words instead of generating. Nothing to bind.
    assert extract_a2ui_document("这个问题不需要图表，我直接回答：…") is None


# ── where it lands ───────────────────────────────────────────────────────


def test_host_targeted_generations_share_one_file_name() -> None:
    # Identity in the artifact layer is the scope-relative path, so a stable
    # name is what makes "generate this page again" append a version instead
    # of starting a second deliverable.
    host = UiArtifactTargetHost(
        host_type="finance.company-research", host_id="US:NVDA", slot="main"
    )

    assert _document_file_name(host) == _document_file_name(host)


def test_host_file_name_is_path_safe() -> None:
    # host_id carries things like ``US:NVDA``; it becomes a file name.
    name = _document_file_name(
        UiArtifactTargetHost(
            host_type="finance.company-research", host_id="US:NVDA", slot="main"
        )
    )

    assert ":" not in name and "/" not in name
    assert name.endswith(".a2ui.jsonl")


def test_different_hosts_do_not_collide() -> None:
    desk = _document_file_name(
        UiArtifactTargetHost(host_type="finance.research-desk", host_id="desk")
    )
    company = _document_file_name(
        UiArtifactTargetHost(host_type="finance.company-research", host_id="US:NVDA")
    )

    assert desk != company


def test_different_slots_on_one_host_do_not_collide() -> None:
    main = _document_file_name(
        UiArtifactTargetHost(host_type="finance.research-desk", host_id="desk", slot="main")
    )
    side = _document_file_name(
        UiArtifactTargetHost(host_type="finance.research-desk", host_id="desk", slot="side")
    )

    assert main != side


# ── which host a generation belongs to ───────────────────────────────────


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


class TestRepeatedDocument:
    """A turn's canonical text is the join of every model-end segment, so a run
    that emits the document twice hands back both copies byte for byte."""

    def test_stores_a_repeated_document_once(self) -> None:
        doc = "\n".join(
            [
                json.dumps({"version": "v0.9", "createSurface": {"surfaceId": "main"}}),
                json.dumps(
                    {
                        "version": "v0.9",
                        "updateComponents": {
                            "surfaceId": "main",
                            "components": [{"id": "root", "component": "Stack"}],
                        },
                    }
                ),
            ]
        )

        assert extract_a2ui_document(f"{doc}\n{doc}") == doc

    def test_keeps_a_second_declaration_that_differs(self) -> None:
        # Only an exact repeat is dropped; two DIFFERENT versions are a real
        # restart and picking a winner is not this function's call.
        first = json.dumps({"version": "v0.9", "createSurface": {"surfaceId": "main"}})
        components = json.dumps(
            {
                "version": "v0.9",
                "updateComponents": {
                    "surfaceId": "main",
                    "components": [{"id": "root", "component": "Stack"}],
                },
            }
        )
        other = json.dumps(
            {
                "version": "v0.9",
                "updateComponents": {
                    "surfaceId": "main",
                    "components": [{"id": "root", "component": "Card"}],
                },
            }
        )
        raw = "\n".join([first, components, first, other])

        assert extract_a2ui_document(raw) == raw
