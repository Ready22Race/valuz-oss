"""Tests for the model-options read model (GET /v1/settings/model-options).

The builder is pure (no DB / catalog) so these are plain unit tests. ADR-011:
each ``ProviderOptionInput`` carries its per-model rows (``models``), a
``serves_responses`` capability flag, and an opaque ``group`` / ``group_rank``;
``runtimes_for`` derives a model's runtimes from ``(protocols, provider_kind,
serves_responses)``.
"""

from __future__ import annotations

from valuz_agent.modules.providers.schemas import ProviderModel
from valuz_agent.modules.settings.model_options import (
    CurrentDefault,
    ProviderOptionInput,
    build_model_options,
    runtimes_for,
)

_GROUP_RANK = {"subscription": 10, "system": 20, "org": 30, "api_key": 40}


def _group_for(source: str, auth_type: str) -> str:
    if source == "org":
        return "org"
    if source == "system":
        return "system"
    if auth_type == "oauth":
        return "subscription"
    return "api_key"


def _m(mid: str, label: str | None = None, protocols: tuple[str, ...] = ()) -> ProviderModel:
    return ProviderModel(id=mid, label=label, protocols=protocols)


def _pin(
    *,
    models: list[ProviderModel] | None = None,
    serves_responses: bool = False,
    **overrides,
) -> ProviderOptionInput:
    base = dict(
        id="p",
        name="P",
        provider_kind="openai",
        source="user",
        auth_type="api_key",
        enabled=True,
        unavailable_reason=None,
        compatible_protocols=["openai-completion"],
        models=models if models is not None else [_m("m")],
        serves_responses=serves_responses,
    )
    base.update(overrides)
    # group / group_rank follow source + auth_type, mirroring what the route
    # copies off each ProviderListItem.
    group = _group_for(base["source"], base["auth_type"])  # type: ignore[arg-type]
    base["group"] = group
    base["group_rank"] = _GROUP_RANK[group]
    return ProviderOptionInput(**base)  # type: ignore[arg-type]


_NO_DEFAULT = CurrentDefault(runtime=None, provider_id=None, model=None)


# ── runtimes_for ─────────────────────────────────────────────────────


class TestRuntimesFor:
    def test_anthropic_plus_completion_runs_claude_and_deepagents(self) -> None:
        assert runtimes_for(
            ["anthropic", "openai-completion"], provider_kind="system", serves_responses=False
        ) == ["claude_agent", "deepagents"]

    def test_serves_responses_channel_runs_codex_only(self) -> None:
        assert runtimes_for(["openai-response"], provider_kind="system", serves_responses=True) == [
            "codex"
        ]

    def test_claude_subscription_runs_claude_only(self) -> None:
        assert runtimes_for(
            ["anthropic"], provider_kind="claude-subscription", serves_responses=False
        ) == ["claude_agent"]

    def test_codex_subscription_runs_codex_regardless_of_serves_responses(self) -> None:
        assert runtimes_for(
            ["openai-response"], provider_kind="codex-subscription", serves_responses=False
        ) == ["codex"]

    def test_user_openai_key_cannot_drive_codex(self) -> None:
        # A bare user OpenAI key speaking response shape still can't run codex
        # (codex walks its own keychain) — serves_responses is False for user
        # rows, so only deepagents.
        assert runtimes_for(
            ["openai-completion", "openai-response"], provider_kind="openai", serves_responses=False
        ) == ["deepagents"]

    def test_deepseek_dual_shape_runs_claude_and_deepagents(self) -> None:
        assert runtimes_for(
            ["anthropic", "openai-completion"], provider_kind="deepseek", serves_responses=False
        ) == ["claude_agent", "deepagents"]

    def test_empty_protocols_is_treated_as_no_restriction(self) -> None:
        # serves_responses channel, empty protocols → every runtime, priority-ordered.
        assert runtimes_for([], provider_kind="system", serves_responses=True) == [
            "claude_agent",
            "codex",
            "deepagents",
        ]

    def test_priority_order_is_claude_codex_deepagents(self) -> None:
        rts = runtimes_for([], provider_kind="system", serves_responses=True)
        assert rts == sorted(rts, key=["claude_agent", "codex", "deepagents"].index)


# ── build_model_options ──────────────────────────────────────────────


class TestBuildModelOptions:
    def test_channel_resolves_per_model_runtimes(self) -> None:
        """A contributed anthropic channel: per-model runtimes from each model's
        own protocols, preferred default_runtime, labels surfaced."""
        sys_provider = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[
                _m("sys-reportify-pro", "Valuz Pro", ("openai-completion", "anthropic")),
                _m("valuz-lite", "Valuz Lite", ("openai-completion", "anthropic")),
            ],
        )
        resp = build_model_options([sys_provider], _NO_DEFAULT)

        assert [g.key for g in resp.groups] == ["system"]
        provider = resp.groups[0].providers[0]
        assert provider.provider_id == "valuz-channel"
        assert provider.status == "available"
        by_id = {m.model_id: m for m in provider.models}
        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["sys-reportify-pro"].default_runtime == "claude_agent"
        assert by_id["sys-reportify-pro"].label == "Valuz Pro"
        assert by_id["valuz-lite"].label == "Valuz Lite"

    def test_serves_responses_channel_models_run_codex(self) -> None:
        codex_card = _pin(
            id="valuz-channel-codex",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            serves_responses=True,
            compatible_protocols=["openai-response"],
            models=[_m("gpt-5.4-nano", "Valuz Pro", ("openai-response",))],
        )
        provider = build_model_options([codex_card], _NO_DEFAULT).groups[0].providers[0]
        by_id = {m.model_id: m for m in provider.models}
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]
        assert by_id["gpt-5.4-nano"].default_runtime == "codex"

    def test_same_named_channels_merge_into_one_card(self) -> None:
        """An anthropic card + a serves_responses codex card both named
        "Valuz 系统模型" collapse to ONE card. Each model keeps its owning
        provider_id so a pick still routes to the right channel."""
        anthropic_card = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[
                _m("sys-reportify-pro", "Valuz Pro", ("openai-completion", "anthropic")),
                _m("valuz-lite", "Valuz Lite"),
            ],
        )
        codex_card = _pin(
            id="valuz-channel-codex",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            serves_responses=True,
            compatible_protocols=["openai-response"],
            models=[
                _m("valuz-lite-codex", "Valuz Lite Codex", ("openai-response",)),
                _m("gpt-5.4-nano", "Valuz Pro", ("openai-response",)),
            ],
        )
        resp = build_model_options([anthropic_card, codex_card], _NO_DEFAULT)

        system = next(g for g in resp.groups if g.key == "system")
        assert len(system.providers) == 1  # merged into one card
        card = system.providers[0]
        assert len(card.models) == 4
        owner = {m.model_id: m.provider_id for m in card.models}
        assert owner["sys-reportify-pro"] == "valuz-channel"
        assert owner["gpt-5.4-nano"] == "valuz-channel-codex"
        by_id = {m.model_id: m for m in card.models}
        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]
        # Both "Valuz Pro" variants keep the raw label (no disambiguation).
        assert by_id["sys-reportify-pro"].label == "Valuz Pro"
        assert by_id["gpt-5.4-nano"].label == "Valuz Pro"

    def test_subscription_status_is_client_resolved_with_cli_tool(self) -> None:
        sub = _pin(
            id="claude-subscription",
            name="Claude Pro / Max",
            provider_kind="claude-subscription",
            source="user",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("claude-opus-4-8", protocols=("anthropic",))],
        )
        resp = build_model_options([sub], _NO_DEFAULT)
        provider = resp.groups[0].providers[0]
        assert resp.groups[0].key == "subscription"
        assert provider.status == "client_resolved"
        assert provider.cli_tool == "claude"
        assert provider.models[0].runtimes == ["claude_agent"]

    def test_api_key_provider_is_available(self) -> None:
        resp = build_model_options([_pin()], _NO_DEFAULT)
        assert resp.groups[0].key == "api_key"
        provider = resp.groups[0].providers[0]
        assert provider.status == "available"
        assert provider.cli_tool is None

    def test_disabled_channel_reports_unavailable_reason(self) -> None:
        prov = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            enabled=False,
            unavailable_reason="未登录 Valuz 账户",
            compatible_protocols=["anthropic"],
            models=[_m("m1", protocols=("anthropic",))],
        )
        provider = build_model_options([prov], _NO_DEFAULT).groups[0].providers[0]
        assert provider.status == "unavailable"
        assert provider.unavailable_reason == "未登录 Valuz 账户"

    def test_models_without_runnable_runtime_are_dropped(self) -> None:
        # A user api_key provider speaking only openai-response → no runtime
        # (codex needs codex-subscription / serves_responses; deepagents won't
        # take response).
        prov = _pin(compatible_protocols=["openai-response"], models=[_m("m")])
        resp = build_model_options([prov], _NO_DEFAULT)
        assert resp.groups == []  # provider left with no models → dropped

    def test_groups_ordered_by_group_rank(self) -> None:
        sub = _pin(
            id="s",
            provider_kind="claude-subscription",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("a", protocols=("anthropic",))],
        )
        sysp = _pin(
            id="y",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("b", protocols=("anthropic",))],
        )
        apik = _pin(id="k", models=[_m("c")])
        resp = build_model_options([apik, sysp, sub], _NO_DEFAULT)  # input order shuffled
        assert [g.key for g in resp.groups] == ["subscription", "system", "api_key"]

    def test_is_current_default_flagged(self) -> None:
        prov = _pin(id="k", models=[_m("m1"), _m("m2")])
        current = CurrentDefault(runtime="deepagents", provider_id="k", model="m2")
        models = {
            m.model_id: m
            for m in build_model_options([prov], current).groups[0].providers[0].models
        }
        assert models["m2"].is_current_default is True
        assert models["m1"].is_current_default is False
