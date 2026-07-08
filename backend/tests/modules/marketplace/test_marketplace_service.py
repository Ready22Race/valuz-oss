"""MarketplaceService — normalization + install orchestration over fakes.

No network, no DB: the SkillHub client, skill datastore/service, agent and
pack services are all replaced with in-memory fakes exposing exactly the
methods the service consumes. Covers the normalized item shape, the curated
category allowlist, graceful SkillHub degradation, and every install path's
idempotency contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.agents.service import MemberAlreadyExistsError
from valuz_agent.modules.marketplace.errors import (
    MarketplaceItemNotFound,
    MarketplaceUpstreamError,
)
from valuz_agent.modules.marketplace.service import (
    CURATED_SKILL_CATEGORIES,
    MarketplaceInstallResult,
    MarketplaceService,
)
from valuz_agent.modules.marketplace.skillhub import SkillHubUnavailableError
from valuz_agent.modules.skills.errors import SkillImportFailed

USER = "user-1"


def _hub_skill(slug: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slug": slug,
        "name": slug.title(),
        "description": f"{slug} description",
        "description_zh": f"{slug} 描述",
        "iconUrl": f"https://cdn.example/{slug}.png",
        "category": "data-analysis",
        "subCategories": [{"key": "data-insight", "name": "数据洞察"}],
        "downloads": 100,
        "stars": 5,
        "installs": 10,
        "version": "1.0.0",
        "source": "clawhub",
        "verified": False,
        "labels": {"requires_api_key": "false"},
    }
    base.update(overrides)
    return base


class FakeSkillHub:
    def __init__(self) -> None:
        self.skills: list[dict[str, Any]] = []  # search results (list_skills)
        self.total = 0
        self.showcase: list[dict[str, Any]] = []  # curated shelf (browse)
        self.unavailable = False
        self.detail_payload: dict[str, Any] | None = None
        self.evaluation_payload: dict[str, Any] | None = None
        self.files_payload: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def _check(self) -> None:
        if self.unavailable:
            raise SkillHubUnavailableError("down")

    async def categories(self) -> list[dict[str, Any]]:
        self._check()
        return [
            {"key": key, "name": f"{key}-zh", "nameEn": f"{key}-en"}
            for key in (*CURATED_SKILL_CATEGORIES, "life-service", "design-media")
        ]

    async def recommended_skills(self) -> list[dict[str, Any]]:
        self._check()
        return self.showcase

    async def list_skills(self, **params: Any) -> tuple[list[dict[str, Any]], int]:
        self._check()
        self.list_calls.append(params)
        return self.skills, self.total

    async def skill_detail(self, slug: str) -> dict[str, Any]:
        self._check()
        if self.detail_payload is None:
            raise SkillHubUnavailableError("no detail")
        return self.detail_payload

    async def skill_files(self, slug: str) -> list[dict[str, Any]]:
        self._check()
        return self.files_payload

    async def skill_evaluation(self, slug: str) -> dict[str, Any]:
        self._check()
        if self.evaluation_payload is None:
            raise SkillHubUnavailableError("no evaluation")
        return self.evaluation_payload

    def download_url(self, slug: str) -> str:
        return f"https://hub.example/api/v1/download?slug={slug}"


def _index_row(slug: str, **overrides: Any) -> SimpleNamespace:
    base = dict(
        id=f"id-{slug}",
        slug=slug,
        name=slug,
        description=f"{slug} desc",
        scope="user",
        status="available",
        is_locked=False,
        library_enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeSkillService:
    """Covers both faces the marketplace consumes: the index reads
    (list_indexed_skills / get_indexed_skill) and the URL-import pipeline."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.preview = SimpleNamespace(
            preview_id="pv-1", name="imported-skill", name_conflict=False, suggested_name=None
        )
        self.preview_error: Exception | None = None
        self.confirmed: list[Any] = []
        self.enabled: list[tuple[str, bool]] = []

    async def list_indexed_skills(self, user_id: str) -> list[SimpleNamespace]:
        return self.rows

    async def get_indexed_skill(self, user_id: str, slug: str) -> SimpleNamespace | None:
        return next((r for r in self.rows if r.slug == slug), None)

    async def import_url_preview(self, user_id: str, url: str) -> SimpleNamespace:
        if self.preview_error is not None:
            raise self.preview_error
        return self.preview

    async def confirm_url_import(self, user_id: str, payload: Any) -> SimpleNamespace:
        self.confirmed.append(payload)
        return SimpleNamespace(slug=payload.name or "imported-skill")

    async def set_library_enabled(self, user_id: str, skill_id: str, enabled: bool) -> None:
        self.enabled.append((skill_id, enabled))


class FakeAgentService:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self.slugs = slugs or set()
        self.created: list[dict[str, Any]] = []

    async def list_agents(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=s) for s in self.slugs]

    async def create_agent(self, user_id: str, payload: dict[str, Any]) -> SimpleNamespace:
        if payload["slug"] in self.slugs:
            raise MemberAlreadyExistsError(payload["slug"])
        self.created.append(payload)
        return SimpleNamespace(slug=payload["slug"])


class FakePackService:
    def __init__(self) -> None:
        self.packs = [
            {
                "id": "investment",
                "name": "投研 Team",
                "description": "端到端投研",
                "scenario": "金融投资",
                "icon": "gem",
                "added": False,
                "roles": [
                    {
                        "slug": "inv-analyst",
                        "name": "行业分析师",
                        "description": "行业研究",
                        "skills": ["comps", "sector-overview"],
                        "connector_types": ["valuz-stock"],
                    },
                    {
                        "slug": "inv-modeler",
                        "name": "建模师",
                        "description": "财务建模",
                        "skills": ["comps"],
                        "connector_types": [],
                    },
                ],
            }
        ]
        self.import_result: dict[str, Any] = {"created": 2, "skipped": 0, "roles": []}
        self.import_calls: list[dict[str, Any]] = []

    async def list_packs(self, user_id: str) -> list[dict[str, Any]]:
        return self.packs

    async def get_pack(self, user_id: str, pack_id: str) -> dict[str, Any]:
        from valuz_agent.modules.agent_packs.errors import PackNotFound

        for p in self.packs:
            if p["id"] == pack_id:
                return p
        raise PackNotFound()

    async def import_pack(self, user_id: str, pack_id: str, **kwargs: Any) -> dict[str, Any]:
        self.import_calls.append({"pack_id": pack_id, **kwargs})
        return self.import_result


@pytest.fixture()
def env():  # type: ignore[no-untyped-def]
    hub = FakeSkillHub()
    skill_svc = FakeSkillService()
    agent_svc = FakeAgentService()
    pack_svc = FakePackService()
    svc = MarketplaceService(
        skillhub=hub,  # type: ignore[arg-type]
        skill_service=skill_svc,  # type: ignore[arg-type]
        agent_service=agent_svc,  # type: ignore[arg-type]
        pack_service=pack_svc,  # type: ignore[arg-type]
    )
    return SimpleNamespace(
        svc=svc, hub=hub, skill_ds=skill_svc, skill_svc=skill_svc,
        agent_svc=agent_svc, pack_svc=pack_svc,
    )


# ---------------------------------------------------------------------------
# Skill listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_items_normalized_with_badges(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [
        _hub_skill("plain"),
        _hub_skill("keyed", labels={"requires_api_key": "true"}),
        _hub_skill("communal", source="community", verified=True),
    ]
    out = await env.svc.list_items(USER, type_="skill")
    assert not out.degraded
    by_ref = {i.source_ref: i for i in out.items}
    assert by_ref["plain"].id == "skillhub:skill:plain"
    assert by_ref["plain"].badges == []
    assert "requires_api_key" in by_ref["keyed"].badges
    assert {"verified", "community"} <= set(by_ref["communal"].badges)
    assert by_ref["plain"].install_target == "skill_library"
    assert by_ref["plain"].subcategories == ["数据洞察"]


@pytest.mark.asyncio
async def test_skillhub_unavailable_index_row_is_not_installed(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [_hub_skill("ima-skills")]
    env.skill_svc.rows = [SimpleNamespace(slug="ima-skills", status="unavailable")]
    out = await env.svc.list_items(USER, type_="skill")
    assert out.items[0].installed is False


@pytest.mark.asyncio
async def test_skillhub_missing_index_path_is_not_installed(env, tmp_path):  # type: ignore[no-untyped-def]
    env.hub.showcase = [_hub_skill("ima-skills")]
    env.skill_svc.rows = [
        SimpleNamespace(
            slug="ima-skills",
            status="available",
            source_path=str(tmp_path / "deleted-skill"),
        )
    ]
    out = await env.svc.list_items(USER, type_="skill")
    assert out.items[0].installed is False


@pytest.mark.asyncio
async def test_browse_serves_official_showcase_paged(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [_hub_skill(f"s{i}") for i in range(45)]
    page1 = await env.svc.list_items(USER, type_="skill", page_size=30)
    assert page1.total == 45 and len(page1.items) == 30
    assert env.hub.list_calls == []  # browse never crawls the catalog
    page2 = await env.svc.list_items(USER, type_="skill", page=2, page_size=30)
    assert [i.source_ref for i in page2.items] == [f"s{i}" for i in range(30, 45)]


@pytest.mark.asyncio
async def test_browse_category_filters_within_showcase(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [
        _hub_skill("d1"),
        _hub_skill("design", category="design-media"),
    ]
    out = await env.svc.list_items(USER, type_="skill", category="design-media")
    assert [i.source_ref for i in out.items] == ["design"] and out.total == 1


@pytest.mark.asyncio
async def test_search_hits_full_catalog_scoped_to_allowlist(env):  # type: ignore[no-untyped-def]
    env.hub.skills = [
        _hub_skill("ok"),
        _hub_skill("mystic", category="mysticism"),
    ]
    env.hub.total = 1671
    out = await env.svc.list_items(USER, type_="skill", q="pdf")
    assert [i.source_ref for i in out.items] == ["ok"]  # junk verticals dropped
    assert out.total == 1671  # search keeps the full catalog depth
    (call,) = env.hub.list_calls
    assert call["keyword"] == "pdf"


@pytest.mark.asyncio
async def test_skill_items_marks_installed_from_index(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [_hub_skill("have"), _hub_skill("lack")]
    env.skill_ds.rows = [_index_row("have")]
    out = await env.svc.list_items(USER, type_="skill")
    flags = {i.source_ref: i.installed for i in out.items if i.source == "skillhub"}
    assert flags == {"have": True, "lack": False}


@pytest.mark.asyncio
async def test_skill_items_never_include_official_skills(env):  # type: ignore[no-untyped-def]
    # Product decision: official skills ship with the client and never
    # appear as market items — the Skills tab is SkillHub-only.
    env.hub.showcase = [_hub_skill("remote")]
    env.skill_ds.rows = [
        _index_row("official-a", scope="official"),
        _index_row("user-skill", scope="user"),
    ]
    out = await env.svc.list_items(USER, type_="skill")
    assert all(i.source == "skillhub" for i in out.items)


@pytest.mark.asyncio
async def test_skill_items_degrade_empty_when_hub_down(env):  # type: ignore[no-untyped-def]
    env.hub.unavailable = True
    out = await env.svc.list_items(USER, type_="skill")
    assert out.degraded and out.items == [] and out.total == 0


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_categories_derive_from_showcase(env):  # type: ignore[no-untyped-def]
    env.hub.showcase = [
        _hub_skill("a"),  # data-analysis
        _hub_skill("b"),
        _hub_skill("c", category="ai-agent"),
        _hub_skill("d", category="design-media"),  # curator extra, not in allowlist
    ]
    out = await env.svc.list_categories(USER, "skill")
    # Allowlist order first, curator extras after; counts are the shelf's own.
    assert [(c.key, c.count) for c in out.categories] == [
        ("data-analysis", 2),
        ("ai-agent", 1),
        ("design-media", 1),
    ]
    # Label resolves through the upstream category names (locale-dependent).
    assert out.categories[0].label.startswith("data-analysis-")
    assert not out.degraded


@pytest.mark.asyncio
async def test_skill_categories_degrade_when_hub_down(env):  # type: ignore[no-untyped-def]
    env.hub.unavailable = True
    out = await env.svc.list_categories(USER, "skill")
    assert out.degraded and out.categories == []


@pytest.mark.asyncio
async def test_agent_categories_derived_from_team_templates_only(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_categories(USER, "agent")
    assert [(c.key, c.label, c.count) for c in out.categories] == [
        ("金融投资", "金融投资", 1)
    ]


# ---------------------------------------------------------------------------
# Agent / team templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_templates_listed_with_filters(env):  # type: ignore[no-untyped-def]
    everything = await env.svc.list_items(USER, type_="agent_template")
    assert everything.total == 8
    finance = await env.svc.list_items(USER, type_="agent_template", category="finance")
    assert [i.source_ref for i in finance.items] == ["equity-research"]
    official = await env.svc.list_items(USER, type_="agent_template", source="valuz_official")
    assert all(i.source == "valuz_official" for i in official.items)
    assert official.total == 8


@pytest.mark.asyncio
async def test_agent_template_installed_flag(env):  # type: ignore[no-untyped-def]
    env.agent_svc.slugs = {"mkt-equity-research"}
    out = await env.svc.list_items(USER, type_="agent_template")
    flags = {i.source_ref: i.installed for i in out.items}
    assert flags["equity-research"] is True
    assert flags["longform-writer"] is False


@pytest.mark.asyncio
async def test_team_templates_expose_members_and_lead(env):  # type: ignore[no-untyped-def]
    out = await env.svc.list_items(USER, type_="agent_team_template")
    (team,) = out.items
    assert team.id == "valuz:team:investment"
    assert team.members is not None and team.members[0].lead
    assert team.skill_count == 2  # union of role skills
    assert team.install_target == "agent_library"
    by_category = await env.svc.list_items(
        USER, type_="agent_team_template", category="金融投资"
    )
    assert [i.source_ref for i in by_category.items] == ["investment"]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skillhub_detail_maps_files_and_security(env):  # type: ignore[no-untyped-def]
    env.hub.detail_payload = {
        "skill": {
            **_hub_skill("agent-memory"),
            "displayName": "Agent Memory",
            "description": None,
            "description_zh": None,
            "summary_zh": "这是来自详情接口的技能简介。",
            "stats": {"downloads": 5, "stars": 1, "installs": 2},
            "sourceUrl": "https://clawhub.ai/x/agent-memory",
            "updated_at": 1783469566392,
        },
        "owner": {"displayName": "dennis"},
        "latestVersion": {"version": "1.0.0"},
        "securityReports": {
            "keen": {"status": "benign", "statusText": "安全", "reportUrl": "https://r/1"},
            "sanbu": {"status": "benign", "statusText": "无风险", "reportUrl": "https://r/2"},
        },
    }
    env.hub.files_payload = [{"path": "SKILL.md", "size": 1385, "sha256": "x"}]
    env.hub.evaluation_payload = {
        "userSummary": "这个 Skill 质量不错，值得一试。",
        "dimensions": {
            "trust": {"items": {"scan": {"score": 5}}, "userReason": "无 P0/P1 风险"},
            "reliability": {"items": {"func": {"score": 4}, "stability": {"score": 4}}},
            "adaptability": {"items": {"boundary": {"score": 4.5}}},
            "convention": {"items": {"docQuality": {"score": 5}}},
            "effectiveness": {"items": {"usability": {"score": 4.5}}},
        },
    }
    detail = await env.svc.get_item(USER, "skillhub:skill:agent-memory")
    assert detail.title == "Agent Memory"
    assert detail.description == "这是来自详情接口的技能简介。"
    assert detail.owner == "dennis"
    assert detail.security is not None and detail.security.status == "benign"
    assert "reviewed_skillhub" in detail.badges
    assert detail.files is not None and detail.files[0].path == "SKILL.md"
    assert detail.origin_url == "https://clawhub.ai/x/agent-memory"
    assert detail.updated_at is not None
    assert detail.evaluation is not None
    assert detail.evaluation.system == "TRACE"
    assert detail.evaluation.score == 4.6
    assert detail.evaluation.rating == "优秀"
    assert [d.code for d in detail.evaluation.dimensions] == ["T", "R", "A", "C", "E"]


@pytest.mark.asyncio
async def test_agent_template_detail_has_instructions(env):  # type: ignore[no-untyped-def]
    detail = await env.svc.get_item(USER, "valuz:agent:equity-research")
    assert detail.instructions
    assert detail.bound_skills and len(detail.bound_skills) == 6
    assert detail.connectors is not None and any(
        c.requirement == "required" for c in detail.connectors
    )


@pytest.mark.asyncio
async def test_team_detail_lists_bound_skills(env):  # type: ignore[no-untyped-def]
    detail = await env.svc.get_item(USER, "valuz:team:investment")
    assert detail.bound_skills == ["comps", "sector-overview"]
    assert detail.members is not None and len(detail.members) == 2
    assert detail.instructions and "行业分析师" in detail.instructions
    assert detail.workflow == [
        "行业分析师：行业研究",
        "建模师：财务建模",
        "汇总交付：整合各成员结果，形成可复用的最终成果包",
    ]
    assert detail.deliverables is not None and "行业研究纪要" in detail.deliverables


@pytest.mark.asyncio
async def test_unknown_item_ids_raise_not_found(env):  # type: ignore[no-untyped-def]
    for bad in ("nope", "valuz:agent:missing", "valuz:team:missing", "valuz:skill:x", "x:y:z"):
        with pytest.raises(MarketplaceItemNotFound):
            await env.svc.get_item(USER, bad)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_skillhub_skill_runs_url_pipeline(env):  # type: ignore[no-untyped-def]
    result = await env.svc.install(USER, "skillhub:skill:fresh")
    assert result.status == "installed"
    (payload,) = env.skill_svc.confirmed
    assert payload.preview_id == "pv-1"
    assert payload.name == "fresh"


@pytest.mark.asyncio
async def test_install_skillhub_skill_falls_back_to_slug_name(env):  # type: ignore[no-untyped-def]
    # SkillHub catalog slugs must remain stable even if the archive manifest
    # previews under a friendlier or staging-derived name.
    env.skill_svc.preview = SimpleNamespace(
        preview_id="pv-3", name="skill-url-6acdc6aa", name_conflict=False, suggested_name=None
    )
    result = await env.svc.install(USER, "skillhub:skill:fresh")
    assert result.installed_ref == "fresh"


@pytest.mark.asyncio
async def test_install_skillhub_skill_uses_suggested_name_on_conflict(env):  # type: ignore[no-untyped-def]
    env.skill_svc.preview = SimpleNamespace(
        preview_id="pv-2", name="taken", name_conflict=True, suggested_name="taken-2"
    )
    result = await env.svc.install(USER, "skillhub:skill:fresh")
    assert result.installed_ref == "taken-2"


@pytest.mark.asyncio
async def test_install_skillhub_skill_idempotent(env):  # type: ignore[no-untyped-def]
    env.skill_ds.rows = [_index_row("fresh")]
    result = await env.svc.install(USER, "skillhub:skill:fresh")
    assert result.status == "already_installed"
    assert env.skill_svc.confirmed == []


@pytest.mark.asyncio
async def test_install_skillhub_fetch_failure_maps_to_upstream_error(env):  # type: ignore[no-untyped-def]
    env.skill_svc.preview_error = SkillImportFailed("Failed to fetch URL: boom")
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.install(USER, "skillhub:skill:fresh")


@pytest.mark.asyncio
async def test_install_skillhub_validation_failure_propagates(env):  # type: ignore[no-untyped-def]
    env.skill_svc.preview_error = SkillImportFailed("No SKILL.md found in the fetched content")
    with pytest.raises(SkillImportFailed):
        await env.svc.install(USER, "skillhub:skill:fresh")


@pytest.mark.asyncio
async def test_install_official_skill_id_rejected(env):  # type: ignore[no-untyped-def]
    env.skill_ds.rows = [_index_row("off", scope="official", library_enabled=False)]
    with pytest.raises(MarketplaceItemNotFound):
        await env.svc.install(USER, "valuz:skill:off")


@pytest.mark.asyncio
async def test_install_agent_template_creates_agent_with_defaults(env):  # type: ignore[no-untyped-def]
    result = await env.svc.install(
        USER,
        "valuz:agent:meeting-notes",
        runtime="deepagents",
        provider_id="prov-1",
        model="m-1",
        effort="low",
    )
    assert result.status == "installed"
    (payload,) = env.agent_svc.created
    assert payload["slug"] == "mkt-meeting-notes"
    assert payload["runtime"] == "deepagents"
    assert payload["model"] == "m-1"
    assert payload["instructions"]


@pytest.mark.asyncio
async def test_install_agent_template_idempotent(env):  # type: ignore[no-untyped-def]
    env.agent_svc.slugs = {"mkt-meeting-notes"}
    result = await env.svc.install(
        USER, "valuz:agent:meeting-notes", runtime="deepagents",
        provider_id="p", model="m", effort=None,
    )
    assert result.status == "already_installed"


@pytest.mark.asyncio
async def test_install_team_delegates_to_pack_service(env):  # type: ignore[no-untyped-def]
    result = await env.svc.install(
        USER, "valuz:team:investment", runtime="claude_agent",
        provider_id="p", model="m", effort="high",
    )
    assert result.status == "installed" and result.created == 2
    (call,) = env.pack_svc.import_calls
    assert call["pack_id"] == "investment" and call["runtime"] == "claude_agent"


@pytest.mark.asyncio
async def test_install_team_installs_skillhub_dependencies_before_pack_import(
    env, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    env.pack_svc.packs[0]["skills"] = [
        {"slug": "superpowers-tdd", "source": "skillhub"},
        {"slug": "sector-overview", "source": "bundled"},
    ]
    calls: list[tuple[str, str, bool, int]] = []

    async def _fake_install_skillhub_skill(
        user_id: str,
        item_id: str,
        slug: str,
        *,
        allow_rename: bool = True,
    ) -> MarketplaceInstallResult:
        calls.append((item_id, slug, allow_rename, len(env.pack_svc.import_calls)))
        return MarketplaceInstallResult(
            item_id=item_id, status="installed", installed_ref=slug
        )

    monkeypatch.setattr(env.svc, "_install_skillhub_skill", _fake_install_skillhub_skill)

    result = await env.svc.install(
        USER, "valuz:team:investment", runtime="claude_agent",
        provider_id="p", model="m", effort="high",
    )

    assert result.status == "installed"
    assert calls == [("skillhub:skill:superpowers-tdd", "superpowers-tdd", False, 0)]
    assert len(env.pack_svc.import_calls) == 1


@pytest.mark.asyncio
async def test_install_team_already_added(env):  # type: ignore[no-untyped-def]
    env.pack_svc.import_result = {"created": 0, "skipped": 3, "roles": []}
    result = await env.svc.install(
        USER, "valuz:team:investment", runtime="claude_agent",
        provider_id="p", model="m", effort=None,
    )
    assert result.status == "already_installed" and result.skipped == 3
