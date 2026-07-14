"""MarketplaceService ↔ direct_fallback integration.

Covers the ``Settings.marketplace_direct_fallback`` gated behavior: when the
market index is unreachable, ``skill``/``connector`` reads fall through to
SkillHub/ModelScope (mocked here — no network) and ``agent`` reads fall
through to the bundled data (``agent_templates.json`` + built-in agent
packs), all marked ``degraded: true``; the ``skillhub:skill:*`` /
``valuz:agent:*`` / ``valuz:team:*`` install dispatches run the pre-index
pipelines and write provenance tagged ``source_channel="direct-fallback"``;
and the direct-source item ids 404 when the flag is off but resolve through
the fallback when it's on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.modules.marketplace import service as service_module
from valuz_agent.modules.marketplace.errors import MarketplaceItemNotFound
from valuz_agent.modules.marketplace.market_index import MarketIndexUnavailableError
from valuz_agent.modules.marketplace.modelscope import ModelScopeUnavailableError
from valuz_agent.modules.marketplace.service import MarketplaceService
from valuz_agent.modules.marketplace.skillhub import SkillHubUnavailableError

USER = "user-1"


# ---------------------------------------------------------------------------
# Fakes — mirror the real SkillHubClient/ModelScopeClient method surfaces
# ---------------------------------------------------------------------------


class FakeHub:
    def __init__(self) -> None:
        self.category_rows: list[dict[str, Any]] = [
            {"key": "dev-programming", "name": "开发编程", "nameEn": "Dev & programming"},
        ]
        self.recommended: list[dict[str, Any]] = [
            {
                "slug": "pdf-toolkit",
                "name": "PDF Toolkit",
                "description": "Extract text from PDFs",
                "category": "dev-programming",
                "labels": {},
                "downloads": 10,
                "stars": 2,
                "installs": 1,
                "version": "1.0.0",
            }
        ]
        self.search_result: tuple[list[dict[str, Any]], int] = ([], 0)
        self.detail_payload: dict[str, Any] | None = {
            "skill": {
                "slug": "pdf-toolkit",
                "displayName": "PDF Toolkit",
                "category": "dev-programming",
                "ownerName": "Acme",
            },
            "owner": {"displayName": "Acme"},
            "latestVersion": {"version": "1.2.3"},
        }
        self.unavailable = False

    async def categories(self) -> list[dict[str, Any]]:
        if self.unavailable:
            raise SkillHubUnavailableError("down")
        return self.category_rows

    async def recommended_skills(self) -> list[dict[str, Any]]:
        if self.unavailable:
            raise SkillHubUnavailableError("down")
        return self.recommended

    async def list_skills(
        self, *, page: int, page_size: int, category: str | None, keyword: str | None
    ) -> tuple[list[dict[str, Any]], int]:
        if self.unavailable:
            raise SkillHubUnavailableError("down")
        return self.search_result

    async def skill_detail(self, slug: str) -> dict[str, Any]:
        if self.unavailable or self.detail_payload is None:
            raise SkillHubUnavailableError("down")
        return self.detail_payload

    async def skill_files(self, slug: str) -> list[dict[str, Any]]:
        return [{"path": "SKILL.md", "size": 100, "sha256": "abc"}]

    async def skill_evaluation(self, slug: str) -> dict[str, Any]:
        raise SkillHubUnavailableError("no evaluation")

    def download_url(self, slug: str) -> str:
        return f"https://api.skillhub.cn/api/v1/download?slug={slug}"


class FakeModelScope:
    def __init__(self) -> None:
        self.servers: list[dict[str, Any]] = [
            {
                "id": "acme/search-tool",
                "name": "Search Tool",
                "categories": ["search"],
                "tags": ["search"],
                "description": "Web search MCP",
                "is_verified": True,
            }
        ]
        self.total = 1
        self.unavailable = False

    async def list_servers(
        self,
        *,
        category: str | None,
        search: str | None,
        is_hosted: bool | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if self.unavailable:
            raise ModelScopeUnavailableError("down")
        return self.servers, self.total

    async def server_detail(self, server_id: str) -> dict[str, Any]:
        if self.unavailable:
            raise ModelScopeUnavailableError("down")
        return {
            "id": server_id,
            "name": "Search Tool",
            "author": "acme",
            "categories": ["search"],
            "readme": "A search MCP.",
            "server_config": [
                {
                    "mcpServers": {
                        "search": {
                            "command": "npx",
                            "args": ["-y", "search-mcp"],
                            "env": {},
                        }
                    }
                }
            ],
        }

    async def server_detail_cached(self, server_id: str) -> dict[str, Any]:
        return await self.server_detail(server_id)


class FakeMarketIndexClient:
    """Always unavailable — every test here exercises the fallback path."""

    channel = "oss"

    async def categories(self, kind: str, locale: str) -> dict[str, Any]:
        raise MarketIndexUnavailableError("index down")

    async def list_items(self, **params: Any) -> dict[str, Any]:
        raise MarketIndexUnavailableError("index down")

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]:
        raise MarketIndexUnavailableError("index down")


class FakeSkillService:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.preview = SimpleNamespace(
            preview_id="pv-1", name="pdf-toolkit", name_conflict=False, suggested_name=None
        )
        self.confirmed: list[Any] = []

    async def list_indexed_skills(self, user_id: str) -> list[SimpleNamespace]:
        return self.rows

    async def get_indexed_skill(self, user_id: str, slug: str) -> SimpleNamespace | None:
        return next((r for r in self.rows if r.slug == slug), None)

    async def import_url_preview(self, user_id: str, url: str) -> SimpleNamespace:
        return self.preview

    async def confirm_url_import(self, user_id: str, payload: Any) -> SimpleNamespace:
        self.confirmed.append(payload)
        slug = payload.name or "imported-skill"
        return SimpleNamespace(slug=slug, content_hash=f"hash-of-{slug}")


class FakeAgentService:
    def __init__(self) -> None:
        self.slugs: set[str] = set()
        self.created: list[dict[str, Any]] = []

    async def list_agents(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=slug) for slug in self.slugs]

    async def create_agent(self, user_id: str, payload: dict[str, Any]) -> SimpleNamespace:
        self.created.append(payload)
        self.slugs.add(payload["slug"])
        return SimpleNamespace(slug=payload["slug"])


BUILTIN_PACK = {
    "id": "research-squad",
    "name": "Research Squad",
    "description": "A built-in research team",
    "icon": "users",
    "scenario": "research",
    "added": False,
    "roles": [
        {"slug": "lead", "name": "Lead", "description": "leads", "skills": ["pdf-toolkit"]},
        {"slug": "analyst", "name": "Analyst", "description": "digs", "skills": []},
    ],
    "skills": [],
}


class FakePackService:
    def __init__(self) -> None:
        self.packs: list[dict[str, Any]] = [dict(BUILTIN_PACK)]
        self.imported: list[str] = []

    async def list_packs(self, user_id: str) -> list[dict[str, Any]]:
        return self.packs

    async def get_pack(self, user_id: str, pack_id: str) -> dict[str, Any]:
        from valuz_agent.modules.agent_packs.errors import PackNotFound

        for pack in self.packs:
            if pack["id"] == pack_id:
                return pack
        raise PackNotFound(pack_id)

    async def import_pack(self, user_id: str, pack_id: str, **kwargs: Any) -> dict[str, Any]:
        self.imported.append(pack_id)
        return {"created": 2, "skipped": 0}


class FakeConnectorService:
    def __init__(self) -> None:
        self.slugs: set[str] = set()

    async def list_connectors(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=slug) for slug in self.slugs]


class FakeInstallStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, user_id: str, **kwargs: Any) -> None:
        self.records.append({"user_id": user_id, **kwargs})


@pytest.fixture()
def env(monkeypatch):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    ms = FakeModelScope()
    monkeypatch.setattr(service_module, "_skillhub_client", lambda: hub)
    monkeypatch.setattr(service_module, "_modelscope_client", lambda: ms)
    monkeypatch.setattr(settings, "marketplace_direct_fallback", True)

    index = FakeMarketIndexClient()
    skill_svc = FakeSkillService()
    agent_svc = FakeAgentService()
    pack_svc = FakePackService()
    connector_svc = FakeConnectorService()
    installs = FakeInstallStore()
    svc = MarketplaceService(
        index=index,  # type: ignore[arg-type]
        skill_service=skill_svc,  # type: ignore[arg-type]
        agent_service=agent_svc,  # type: ignore[arg-type]
        pack_service=pack_svc,  # type: ignore[arg-type]
        installs=installs,  # type: ignore[arg-type]
        connector_service=connector_svc,  # type: ignore[arg-type]
    )
    return SimpleNamespace(
        svc=svc,
        hub=hub,
        ms=ms,
        skill_svc=skill_svc,
        agent_svc=agent_svc,
        pack_svc=pack_svc,
        connector_svc=connector_svc,
        installs=installs,
    )


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_categories_fall_back_when_index_down(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_categories(USER, "skill")
    assert out.degraded is True
    assert [c.key for c in out.categories] == ["dev-programming"]


@pytest.mark.asyncio
async def test_connector_categories_fall_back_when_index_down(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_categories(USER, "connector")
    assert out.degraded is True
    assert any(c.key == "search" for c in out.categories)


@pytest.mark.asyncio
async def test_agent_categories_derive_from_builtin_pack_scenarios(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_categories(USER, "agent")
    assert out.degraded is True
    assert [c.key for c in out.categories] == ["research"]


@pytest.mark.asyncio
async def test_categories_fallback_disabled_returns_empty_degraded(env, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)
    out = await env.svc.list_categories(USER, "skill")
    assert out.degraded is True
    assert out.categories == []


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_skills_falls_back_to_skillhub(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="skill")
    assert out.degraded is True
    assert [i.source_ref for i in out.items] == ["pdf-toolkit"]
    assert out.items[0].id == "skillhub:skill:pdf-toolkit"
    assert out.items[0].installed is False


@pytest.mark.asyncio
async def test_list_skills_recomputes_installed_from_library(env):  # type: ignore[no-untyped-def]
    env.skill_svc.rows = [SimpleNamespace(slug="pdf-toolkit", status="available", source_path=None)]
    out = await env.svc.list_items(USER, type_="skill")
    assert out.items[0].installed is True


@pytest.mark.asyncio
async def test_list_connectors_falls_back_to_modelscope(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="connector")
    assert out.degraded is True
    assert len(out.items) == 1
    assert out.items[0].source == "modelscope"
    assert out.items[0].id.startswith("modelscope:connector:")


@pytest.mark.asyncio
async def test_list_connectors_recomputes_installed(env):  # type: ignore[no-untyped-def]
    # The connector library slug convention: modelscope-{sanitized server id}.
    env.connector_svc.slugs = {"modelscope-acme-search-tool"}
    out = await env.svc.list_items(USER, type_="connector")
    assert out.items[0].installed is True


@pytest.mark.asyncio
async def test_list_connectors_source_filter_excludes_non_modelscope(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="connector", source="skillhub")
    assert out.degraded is True
    assert out.items == []


@pytest.mark.asyncio
async def test_list_agent_templates_falls_back_to_builtin(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="agent_template")
    assert out.degraded is True
    assert out.items, "bundled agent_templates.json must yield fallback items"
    assert all(i.id.startswith("valuz:agent:") for i in out.items)
    assert all(i.type == "agent_template" for i in out.items)


@pytest.mark.asyncio
async def test_list_agent_templates_recomputes_installed_from_library(env):  # type: ignore[no-untyped-def]
    from valuz_agent.modules.marketplace.templates import load_agent_templates

    # ``installed`` keys on the template's library slug (``tpl.slug``), not
    # its catalog id (``source_ref``) — pre-index semantics.
    tpl = load_agent_templates()[0]
    env.agent_svc.slugs.add(tpl.slug)
    out = await env.svc.list_items(USER, type_="agent_template")
    assert next(i for i in out.items if i.id == f"valuz:agent:{tpl.id}").installed is True


@pytest.mark.asyncio
async def test_list_team_templates_falls_back_to_builtin_packs(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="agent_team_template")
    assert out.degraded is True
    assert [i.id for i in out.items] == ["valuz:team:research-squad"]
    assert out.items[0].members and out.items[0].members[0].lead is True


@pytest.mark.asyncio
async def test_list_items_fallback_disabled_returns_empty_degraded(env, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)
    out = await env.svc.list_items(USER, type_="skill")
    assert out.degraded is True
    assert out.items == []
    assert out.total == 0


# ---------------------------------------------------------------------------
# Detail (get_item)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_item_skillhub_id_resolves_through_fallback(env):  # type: ignore[no-untyped-def]
    detail = await env.svc.get_item(USER, "skillhub:skill:pdf-toolkit")
    assert detail.id == "skillhub:skill:pdf-toolkit"
    assert detail.owner == "Acme"


@pytest.mark.asyncio
async def test_get_item_modelscope_id_resolves_through_fallback(env):  # type: ignore[no-untyped-def]
    from valuz_agent.modules.marketplace.direct_fallback import encode_connector_ref

    encoded = encode_connector_ref("acme/search-tool")
    detail = await env.svc.get_item(USER, f"modelscope:connector:{encoded}")
    assert detail.source_ref == "acme/search-tool"
    assert detail.connector_config is not None


@pytest.mark.asyncio
async def test_get_item_legacy_ids_404_when_fallback_disabled(env, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)
    for bad in ("skillhub:skill:pdf-toolkit", "modelscope:connector:x", "valuz:agent:x"):
        with pytest.raises(MarketplaceItemNotFound):
            await env.svc.get_item(USER, bad)


# ---------------------------------------------------------------------------
# Install — skillhub:skill:* fallback dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_skillhub_skill_runs_url_pipeline_and_records_provenance(env):  # type: ignore[no-untyped-def]
    result = await env.svc.install(USER, "skillhub:skill:pdf-toolkit")
    assert result.status == "installed"
    assert result.installed_ref == "pdf-toolkit"
    (payload,) = env.skill_svc.confirmed
    assert payload.name == "pdf-toolkit"
    (record,) = env.installs.records
    assert record["item_id"] == "skillhub:skill:pdf-toolkit"
    assert record["item_type"] == "skill"
    assert record["installed_ref"] == "pdf-toolkit"
    assert record["version"] == "1.2.3"
    assert record["source_channel"] == "direct-fallback"
    assert record["content_hash"] == "hash-of-pdf-toolkit"


@pytest.mark.asyncio
async def test_install_skillhub_skill_falls_back_to_zero_version_without_detail(env):  # type: ignore[no-untyped-def]
    env.hub.detail_payload = None
    result = await env.svc.install(USER, "skillhub:skill:pdf-toolkit")
    assert result.status == "installed"
    (record,) = env.installs.records
    assert record["version"] == "0.0.0"


@pytest.mark.asyncio
async def test_install_skillhub_skill_idempotent(env):  # type: ignore[no-untyped-def]
    env.skill_svc.rows = [
        SimpleNamespace(
            slug="pdf-toolkit", status="available", source_path=None, content_hash="existing"
        )
    ]
    result = await env.svc.install(USER, "skillhub:skill:pdf-toolkit")
    assert result.status == "already_installed"
    assert env.skill_svc.confirmed == []


@pytest.mark.asyncio
async def test_install_skillhub_skill_raises_not_found_when_fallback_disabled(env, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)
    with pytest.raises(MarketplaceItemNotFound):
        await env.svc.install(USER, "skillhub:skill:pdf-toolkit")


# ---------------------------------------------------------------------------
# Agent templates / team packs — built-in data fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_item_builtin_agent_template_resolves_through_fallback(env):  # type: ignore[no-untyped-def]
    listing = await env.svc.list_items(USER, type_="agent_template")
    target = listing.items[0]
    detail = await env.svc.get_item(USER, target.id)
    assert detail.id == target.id
    assert detail.type == "agent_template"
    assert detail.owner == "Valuz"
    assert detail.instructions


@pytest.mark.asyncio
async def test_get_item_builtin_team_resolves_through_fallback(env):  # type: ignore[no-untyped-def]
    detail = await env.svc.get_item(USER, "valuz:team:research-squad")
    assert detail.type == "agent_team_template"
    assert [m.slug for m in detail.members] == ["lead", "analyst"]
    assert "pdf-toolkit" in detail.bound_skills


@pytest.mark.asyncio
async def test_install_builtin_agent_template_creates_agent_and_records_provenance(env):  # type: ignore[no-untyped-def]
    from valuz_agent.modules.marketplace.templates import load_agent_templates

    tpl = load_agent_templates()[0]
    item_id = f"valuz:agent:{tpl.id}"
    result = await env.svc.install(USER, item_id, runtime="claude_agent")
    assert result.status == "installed"
    assert result.installed_ref == tpl.slug
    (created,) = env.agent_svc.created
    assert created["slug"] == tpl.slug
    (record,) = env.installs.records
    assert record["item_id"] == item_id
    assert record["item_type"] == "agent_template"
    assert record["installed_ref"] == tpl.slug
    assert record["version"] == "0.0.0"
    assert record["source_channel"] == "direct-fallback"


@pytest.mark.asyncio
async def test_install_builtin_team_imports_pack_and_records_provenance(env):  # type: ignore[no-untyped-def]
    result = await env.svc.install(USER, "valuz:team:research-squad", runtime="claude_agent")
    assert result.status == "installed"
    assert result.installed_ref == "research-squad"
    assert result.created == 2
    assert env.pack_svc.imported == ["research-squad"]
    (record,) = env.installs.records
    assert record["item_type"] == "agent_team_template"
    assert record["installed_ref"] == "research-squad"
    assert record["source_channel"] == "direct-fallback"


@pytest.mark.asyncio
async def test_install_builtin_team_installs_skillhub_dependencies_first(env):  # type: ignore[no-untyped-def]
    env.pack_svc.packs[0]["skills"] = [{"source": "skillhub", "slug": "pdf-toolkit"}]
    result = await env.svc.install(USER, "valuz:team:research-squad", runtime="claude_agent")
    assert result.status == "installed"
    (payload,) = env.skill_svc.confirmed
    assert payload.name == "pdf-toolkit"


@pytest.mark.asyncio
async def test_install_builtin_ids_404_when_fallback_disabled(env, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)
    for bad in ("valuz:agent:whatever", "valuz:team:research-squad"):
        with pytest.raises(MarketplaceItemNotFound):
            await env.svc.install(USER, bad)


@pytest.mark.asyncio
async def test_install_unknown_builtin_template_raises_not_found(env):  # type: ignore[no-untyped-def]
    with pytest.raises(MarketplaceItemNotFound):
        await env.svc.install(USER, "valuz:agent:not-a-real-template")
