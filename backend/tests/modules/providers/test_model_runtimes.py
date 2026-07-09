"""``LLMModel.runtimes`` is stamped on provider list/detail rows.

Regression for the single-source migration: providers list/detail now carry the
same runtimes the picker would have derived (via ``runtimes_for``), so the
frontend consumes the field instead of re-deriving. See
``docs/design/runtime-model-compat-single-source.md``.
"""

from __future__ import annotations

from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.providers.service import _row_to_detail, _row_to_list_item


def _row(**kw: object) -> ProviderRow:
    base: dict[str, object] = dict(
        id="p1",
        name="Custom",
        provider_kind="compatible",
        source="user",
        auth_type="api_key",
        enabled=True,
        is_default=False,
        deletable=True,
        default_model=None,
        test_status="success",
        credential_source="secret_ref",
        protocol=None,
        model_ids=None,
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    )
    base.update(kw)
    return ProviderRow(**base)  # type: ignore[arg-type]


def test_custom_openai_response_row_drives_codex() -> None:
    row = _row(protocol="openai-response", model_ids='["doubao-seed-2-1-pro"]')
    item = _row_to_list_item(row)
    assert item.compatible_protocols == ["openai-response"]
    by_id = {m.id: m for m in item.models}
    assert "codex" in (by_id["doubao-seed-2-1-pro"].runtimes or ())


def test_list_and_detail_agree() -> None:
    row = _row(protocol="openai-response", model_ids='["doubao-seed-2-1-pro"]')
    item = _row_to_list_item(row)
    detail = _row_to_detail(row)
    assert [(m.id, tuple(m.runtimes or ())) for m in detail.models] == [
        (m.id, tuple(m.runtimes or ())) for m in item.models
    ]


def test_anthropic_row_runtimes() -> None:
    row = _row(provider_kind="anthropic", protocol="anthropic", model_ids='["claude-x"]')
    rts = _row_to_list_item(row).models[0].runtimes or ()
    assert "claude_agent" in rts and "deepagents" in rts and "codex" not in rts


def test_codex_subscription_pins_codex_only() -> None:
    row = _row(
        provider_kind="codex-subscription",
        auth_type="oauth",
        protocol="openai-response",
        credential_source="none",
        model_ids='["gpt-5-codex"]',
    )
    assert tuple(_row_to_list_item(row).models[0].runtimes or ()) == ("codex",)


def test_default_model_only_channel_synthesizes_a_model_row() -> None:
    # No configured models, but a seeded default_model → one synthetic model row
    # so the picker still surfaces it, carrying a truthful runtimes.
    row = _row(protocol="openai-response", model_ids="[]", default_model="doubao-x")
    item = _row_to_list_item(row)
    assert [m.id for m in item.models] == ["doubao-x"]
    assert "codex" in (item.models[0].runtimes or ())
