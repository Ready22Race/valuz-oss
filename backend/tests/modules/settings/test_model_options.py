"""Tests for the model-options read model (GET /v1/settings/model-options).

The builder is pure (no DB / registry) so these are plain unit tests. The
per-model-protocol resolver is exercised separately against a SystemLLMProvider.
"""

from __future__ import annotations

from valuz_agent.modules.providers.service import (
    _descriptor_to_list_item,
    _resolve_descriptor_model_labels,
    _resolve_descriptor_model_options,
    _resolve_descriptor_model_protocols,
)
from valuz_agent.modules.settings.model_options import (
    CurrentDefault,
    ProviderOptionInput,
    _runtimes_for_model,
    build_model_options,
)
from valuz_agent.ports.llm_provider import SystemLLMProvider


def _pin(**overrides) -> ProviderOptionInput:
    base = dict(
        id="p",
        name="P",
        provider_kind="openai",
        source="user",
        auth_type="api_key",
        enabled=True,
        unavailable_reason=None,
        compatible_protocols=["openai-completion"],
        model_options=["m"],
        model_labels={},
        model_protocols={},
    )
    base.update(overrides)
    return ProviderOptionInput(**base)  # type: ignore[arg-type]


_NO_DEFAULT = CurrentDefault(runtime=None, provider_id=None, model=None)


# ── _runtimes_for_model ──────────────────────────────────────────────


class TestRuntimesForModel:
    def test_system_anthropic_plus_completion_runs_claude_and_deepagents(self) -> None:
        assert _runtimes_for_model(["anthropic", "openai-completion"], "system") == [
            "claude_agent",
            "deepagents",
        ]

    def test_system_response_runs_codex_only(self) -> None:
        assert _runtimes_for_model(["openai-response"], "system") == ["codex"]

    def test_claude_subscription_runs_claude_only(self) -> None:
        assert _runtimes_for_model(["anthropic"], "claude-subscription") == ["claude_agent"]

    def test_codex_subscription_runs_codex_regardless_of_protocol(self) -> None:
        assert _runtimes_for_model(["openai-response"], "codex-subscription") == ["codex"]

    def test_user_openai_key_cannot_drive_codex(self) -> None:
        # A bare user OpenAI key speaking response shape still can't run codex
        # (codex walks its own keychain) — only deepagents.
        assert _runtimes_for_model(["openai-completion", "openai-response"], "openai") == [
            "deepagents"
        ]

    def test_deepseek_dual_shape_runs_claude_and_deepagents(self) -> None:
        assert _runtimes_for_model(["anthropic", "openai-completion"], "deepseek") == [
            "claude_agent",
            "deepagents",
        ]

    def test_empty_protocols_is_treated_as_no_restriction(self) -> None:
        # System channel, empty protocols → every runtime, priority-ordered.
        assert _runtimes_for_model([], "system") == ["claude_agent", "codex", "deepagents"]

    def test_priority_order_is_claude_codex_deepagents(self) -> None:
        rts = _runtimes_for_model([], "system")
        assert rts == sorted(rts, key=["claude_agent", "codex", "deepagents"].index)


# ── build_model_options ──────────────────────────────────────────────


class TestBuildModelOptions:
    def test_system_channel_resolves_per_model_runtimes(self) -> None:
        """The real 4-model system channel: one provider, per-model runtimes,
        preferred default_runtime, two same-named models disambiguated."""
        sys_provider = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            model_options=["sys-reportify-pro", "valuz-lite", "valuz-lite-codex", "gpt-5.4-nano"],
            model_labels={
                "sys-reportify-pro": "Valuz Pro",
                "valuz-lite": "Valuz Lite",
                "valuz-lite-codex": "Valuz Lite Codex",
                "gpt-5.4-nano": "Valuz Pro",
            },
            model_protocols={
                "sys-reportify-pro": ["openai-completion", "anthropic"],
                "valuz-lite": ["openai-completion", "anthropic"],
                "valuz-lite-codex": ["openai-response"],
                "gpt-5.4-nano": ["openai-response"],
            },
        )
        resp = build_model_options([sys_provider], _NO_DEFAULT)

        assert [g.key for g in resp.groups] == ["system"]
        provider = resp.groups[0].providers[0]
        assert provider.provider_id == "valuz-channel"
        assert provider.status == "available"
        by_id = {m.model_id: m for m in provider.models}

        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["sys-reportify-pro"].default_runtime == "claude_agent"
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]
        assert by_id["gpt-5.4-nano"].default_runtime == "codex"

        # Raw labels — both "Valuz Pro" variants keep the name (the picker filters
        # by runtime, so a Claude + a Codex "Valuz Pro" never show together).
        assert by_id["sys-reportify-pro"].label == "Valuz Pro"
        assert by_id["gpt-5.4-nano"].label == "Valuz Pro"
        assert by_id["valuz-lite"].label == "Valuz Lite"

    def test_same_named_system_descriptors_merge_into_one_card(self) -> None:
        """The real two-descriptor overlay: an anthropic card + an openai-response
        card both named "Valuz 系统模型" collapse to ONE card. Each model keeps its
        owning provider_id so a pick still routes to the right descriptor."""
        anthropic_card = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            model_options=["sys-reportify-pro", "valuz-lite"],
            model_labels={"sys-reportify-pro": "Valuz Pro", "valuz-lite": "Valuz Lite"},
        )
        codex_card = _pin(
            id="valuz-channel-codex",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["openai-response"],
            model_options=["valuz-lite-codex", "gpt-5.4-nano"],
            model_labels={"valuz-lite-codex": "Valuz Lite Codex", "gpt-5.4-nano": "Valuz Pro"},
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
            model_options=["claude-opus-4-8"],
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

    def test_disabled_system_provider_reports_unavailable_reason(self) -> None:
        prov = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            enabled=False,
            unavailable_reason="未登录 Valuz 账户",
            compatible_protocols=["anthropic"],
            model_options=["m1"],
            model_protocols={"m1": ["anthropic"]},
        )
        provider = build_model_options([prov], _NO_DEFAULT).groups[0].providers[0]
        assert provider.status == "unavailable"
        assert provider.unavailable_reason == "未登录 Valuz 账户"

    def test_models_without_runnable_runtime_are_dropped(self) -> None:
        # A user api_key provider speaking only openai-response → no runtime
        # (codex needs codex-subscription/system; deepagents won't take response).
        prov = _pin(compatible_protocols=["openai-response"], model_options=["m"])
        resp = build_model_options([prov], _NO_DEFAULT)
        assert resp.groups == []  # provider left with no models → dropped

    def test_groups_follow_fixed_order(self) -> None:
        sub = _pin(
            id="s",
            provider_kind="claude-subscription",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            model_options=["a"],
        )
        sysp = _pin(
            id="y",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            model_options=["b"],
            model_protocols={"b": ["anthropic"]},
        )
        apik = _pin(id="k", model_options=["c"])
        resp = build_model_options([apik, sysp, sub], _NO_DEFAULT)  # input order shuffled
        assert [g.key for g in resp.groups] == ["subscription", "system", "api_key"]

    def test_is_current_default_flagged(self) -> None:
        prov = _pin(id="k", model_options=["m1", "m2"])
        current = CurrentDefault(runtime="deepagents", provider_id="k", model="m2")
        models = {
            m.model_id: m
            for m in build_model_options([prov], current).groups[0].providers[0].models
        }
        assert models["m2"].is_current_default is True
        assert models["m1"].is_current_default is False


# ── _resolve_descriptor_model_protocols ──────────────────────────────


def _descriptor(**overrides) -> SystemLLMProvider:
    base = dict(
        id="valuz-channel",
        name="Valuz 系统模型",
        provider_kind="system",
        runtime_provider="claude_agent",
        api_protocol="anthropic",
        api_base="https://gw.example",
    )
    base.update(overrides)
    return SystemLLMProvider(**base)  # type: ignore[arg-type]


class TestResolveDescriptorModelProtocols:
    async def test_none_resolver_returns_empty(self) -> None:
        assert await _resolve_descriptor_model_protocols(_descriptor()) == {}

    async def test_normalizes_kernel_underscore_to_ui_hyphen_sync(self) -> None:
        d = _descriptor(
            list_model_protocols=lambda: {
                "m1": ["openai_completion", "anthropic"],
                "m2": ["openai_response"],
            }
        )
        assert await _resolve_descriptor_model_protocols(d) == {
            "m1": ["openai-completion", "anthropic"],
            "m2": ["openai-response"],
        }

    async def test_async_resolver(self) -> None:
        async def _res() -> dict[str, list[str]]:
            return {"m": ["gemini"]}

        d = _descriptor(list_model_protocols=_res)
        assert await _resolve_descriptor_model_protocols(d) == {"m": ["gemini"]}

    async def test_resolver_error_degrades_to_empty(self) -> None:
        def _boom() -> dict[str, list[str]]:
            raise RuntimeError("gateway down")

        d = _descriptor(list_model_protocols=_boom)
        assert await _resolve_descriptor_model_protocols(d) == {}


# ── System-descriptor path end-to-end ────────────────────────────────


class TestSystemDescriptorEndToEnd:
    """The real path the route runs for a system provider: descriptor →
    ``_descriptor_to_list_item`` (what ``list_providers`` does for registry
    descriptors) + resolvers → ``ProviderOptionInput`` → ``build_model_options``.
    Validates one merged system channel whose models speak different protocols.
    """

    async def test_one_descriptor_yields_one_provider_with_per_model_runtimes(self) -> None:
        d = SystemLLMProvider(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            runtime_provider="claude_agent",
            api_protocol="anthropic",
            api_base="https://gw.example",
            list_models=lambda: ["sys-reportify-pro", "gpt-5.4-nano"],
            list_model_labels=lambda: {
                "sys-reportify-pro": "Valuz Pro",
                "gpt-5.4-nano": "Valuz Pro",
            },
            list_model_protocols=lambda: {
                "sys-reportify-pro": ["openai_completion", "anthropic"],
                "gpt-5.4-nano": ["openai_response"],
            },
            enabled=lambda: True,
        )
        opts = await _resolve_descriptor_model_options(d)
        labels = await _resolve_descriptor_model_labels(d)
        item = _descriptor_to_list_item(d, model_options=opts, model_labels=labels)
        protocols = await _resolve_descriptor_model_protocols(d)
        pin = ProviderOptionInput(
            id=item.id,
            name=item.name,
            provider_kind=item.provider_kind,
            source=item.source,
            auth_type=item.auth_type,
            enabled=item.enabled,
            unavailable_reason=item.unavailable_reason,
            compatible_protocols=item.compatible_protocols,
            model_options=item.model_options,
            model_labels=item.model_labels,
            model_protocols=protocols,
        )
        resp = build_model_options(
            [pin], CurrentDefault(runtime=None, provider_id=None, model=None)
        )

        assert [g.key for g in resp.groups] == ["system"]
        provider = resp.groups[0].providers[0]
        assert provider.provider_id == "valuz-channel"
        assert provider.status == "available"
        by_id = {m.model_id: m for m in provider.models}
        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]
        # Raw labels — both variants keep "Valuz Pro" (picker filters by runtime).
        assert [m.label for m in provider.models] == ["Valuz Pro", "Valuz Pro"]
