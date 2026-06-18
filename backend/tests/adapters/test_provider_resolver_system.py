"""Tests for the contributed-channel resolution path (ADR-011).

When a provider id isn't in the user table, the resolver consults
``ext.provider_catalog``: ``resolve`` synthesises a kernel ``ModelProvider``
from the returned credential, and ``resolve_runtime_provider`` derives the
runtime from the catalog row's protocols + ``serves_responses``.
"""

from __future__ import annotations

import pytest

# Side-effect import — surfaces ``src.core...`` on sys.path before
# provider_resolver imports ``ModelProvider`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.provider_resolver import (
    ProviderNotResolvable,
    resolve_model_provider,
    resolve_runtime_provider,
)
from valuz_agent.modules.providers.schemas import ProviderListItem, ProviderModel
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.provider_catalog import NoopProviderCatalog, ResolvedCredential


class _NoProviders:
    async def get_by_id(self, _user_id: str, _: str):  # type: ignore[no-untyped-def]
        return None


class _UnusedSecrets:
    def get(self, _: str):  # type: ignore[no-untyped-def]
        return None


class _FakeCatalog:
    def __init__(
        self,
        rows: list[ProviderListItem] | None = None,
        creds: dict[str, ResolvedCredential] | None = None,
    ) -> None:
        self._rows = rows or []
        self._creds = creds or {}

    async def list(self) -> list[ProviderListItem]:
        return list(self._rows)

    async def resolve(self, provider_id: str) -> ResolvedCredential | None:
        return self._creds.get(provider_id)


@pytest.fixture(autouse=True)
def fresh_catalog():
    ext.provider_catalog = NoopProviderCatalog()
    yield
    ext.provider_catalog = NoopProviderCatalog()


def _set(
    rows: list[ProviderListItem] | None = None,
    creds: dict[str, ResolvedCredential] | None = None,
) -> None:
    ext.provider_catalog = _FakeCatalog(rows, creds)


def _row(
    *, provider_id: str = "valuz-channel", compatible: list[str], serves_responses: bool = False
) -> ProviderListItem:
    return ProviderListItem(
        id=provider_id,
        name="Test System Channel",
        provider_kind="system",
        source="system",
        deletable=False,
        is_default=False,
        credential_source="system_managed",
        auth_type="oauth",
        compatible_protocols=compatible,
        serves_responses=serves_responses,
        group="system",
        group_rank=20,
        models=[ProviderModel(id="m")],
    )


class TestResolveModelProviderCatalog:
    async def test_cred_resolves(self) -> None:
        _set(
            creds={"valuz-channel": ResolvedCredential("https://cloud.test/v1", "abc", "anthropic")}
        )
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="claude-sonnet-4-6",
            providers=_NoProviders(),  # type: ignore[arg-type]
            secrets=_UnusedSecrets(),  # type: ignore[arg-type]
        )
        assert mp is not None
        assert mp.base_url == "https://cloud.test/v1"
        assert mp.api_key == "abc"
        assert mp.api_protocol == "anthropic"

    async def test_invalid_api_protocol_raises(self) -> None:
        _set(
            creds={
                "valuz-channel": ResolvedCredential(
                    "https://cloud.test/v1", "abc", "not-a-protocol"
                )
            }
        )
        with pytest.raises(ProviderNotResolvable, match="unknown api_protocol"):
            await resolve_model_provider(
                provider_id="valuz-channel",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )

    async def test_empty_api_base_becomes_none(self) -> None:
        _set(creds={"valuz-channel": ResolvedCredential("", "abc", "anthropic")})
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            secrets=_UnusedSecrets(),  # type: ignore[arg-type]
        )
        assert mp is not None
        assert mp.base_url is None

    async def test_unknown_id_raises_not_found(self) -> None:
        # NoopProviderCatalog resolves nothing + user table empty → not found.
        with pytest.raises(ProviderNotResolvable, match="not found"):
            await resolve_model_provider(
                provider_id="unknown",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )


class TestResolveRuntimeProviderCatalog:
    async def test_runtime_derived_from_catalog_row(self) -> None:
        # openai-completion, not serves_responses → deepagents only.
        _set(rows=[_row(compatible=["openai-completion"])])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
        )
        assert rt == "deepagents"

    async def test_serves_responses_row_derives_codex(self) -> None:
        _set(rows=[_row(compatible=["openai-response"], serves_responses=True)])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
        )
        assert rt == "codex"

    async def test_request_runtime_still_overrides(self) -> None:
        _set(rows=[_row(compatible=["anthropic"])])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            request_runtime_id="codex",
        )
        assert rt == "codex"

    async def test_unknown_id_defaults_to_deepagents(self) -> None:
        rt = await resolve_runtime_provider(
            provider_id="unknown",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
        )
        assert rt == "deepagents"
