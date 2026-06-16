"""AgentPackService — list/import behavior over an in-memory library.

Covers the data-layer guarantees without the kernel or HTTP layer: built-in
packs load from ``resources/agent_packs/*/manifest.json``, ``import_pack``
creates agents with the passed-in deploy target, de-dup is by fixed slug
(idempotent), bundled skills materialize on import, and library state
(``in_library`` / ``added``) is reported back. Deploy-target resolution lives
in the route, so the tests pass a fixed (runtime, provider, model, effort).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.secret_store import FileSecretStore
from valuz_agent.modules.agent_packs.errors import PackNotFound
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import ConnectorRow
from valuz_agent.modules.connectors.service import ConnectorService

USER = "user-1"
# create_agent / list_agents never touch the connector service.
DEPLOY = {
    "runtime": "claude_agent",
    "provider_id": "prov-1",
    "model": "claude-sonnet-4-6",
    "effort": "medium",
}


async def _build_service(workdir):  # type: ignore[no-untyped-def]
    """Build an isolated AgentPackService over a fresh sqlite db + tmp secret
    store. Returns (service, session, engine) so the caller can dispose."""
    workdir.mkdir(parents=True, exist_ok=True)
    db_file = workdir / "packs.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRow.__table__,
                ProjectMemberRow.__table__,
                ConnectorRow.__table__,
            ],
        )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    # Isolated connector service so import-time connector registration writes to
    # the test db + a tmp secret store, never the real keychain.
    connector_svc = ConnectorService(
        ConnectorDatastore(session), FileSecretStore(workdir / "secrets")
    )
    agent_svc = AgentService(session, connector_service=connector_svc)
    return AgentPackService(agent_svc), session, engine


@pytest.fixture
async def svc(tmp_path, monkeypatch) -> AsyncIterator[AgentPackService]:
    # import_pack materializes the pack's bundled skills to the official-skills
    # dir — pin it under tmp so tests never touch the real ~/.valuz tree.
    monkeypatch.setenv("VALUZ_OFFICIAL_SKILLS_DIR", str(tmp_path / "official-skills"))
    service, session, engine = await _build_service(tmp_path)
    try:
        yield service
    finally:
        await session.close()
        await engine.dispose()


async def test_list_packs(svc: AgentPackService) -> None:
    packs = await svc.list_packs(USER)
    assert [p["id"] for p in packs] == ["content", "investment", "product"]
    assert sum(len(p["roles"]) for p in packs) == 12
    for pack in packs:
        assert pack["added"] is False
        assert all(r["in_library"] is False for r in pack["roles"])
        # text resolved to real strings, not raw locale maps or dotted keys.
        assert pack["name"] and not pack["name"].startswith("agentTemplates.")
        assert pack["scenario"]


async def test_import_pack_creates_agents(svc: AgentPackService) -> None:
    res = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res["created"] == 4
    assert res["skipped"] == 0
    assert sorted(r.slug for r in res["roles"]) == [
        "inv-earnings-tracker",
        "inv-industry-analyst",
        "inv-model-builder",
        "inv-report-writer",
    ]
    analyst = next(r for r in res["roles"] if r.slug == "inv-industry-analyst")
    assert analyst.avatar == "analyst"
    assert analyst.runtime == "claude_agent"
    # the pack's recommended effort wins over the deploy default
    assert analyst.effort == "high"
    assert analyst.source == "custom"
    assert "china-sector-overview" in analyst.skills
    assert "akshare-mcp" in analyst.connector_types


async def test_import_pack_registers_connectors(svc: AgentPackService) -> None:
    # the investment pack's financial MCPs activate on import — registered as the
    # user's ConnectorRows (idempotent) so they resolve at session time.
    await svc.import_pack(USER, "investment", **DEPLOY)
    connector_svc = svc._agents._connectors
    slugs = {v.slug for v in await connector_svc.list_connectors(USER)}
    assert {"wind-mcp", "ifind-mcp", "akshare-mcp", "china-news-mcp"} <= slugs
    # idempotent: a second import doesn't duplicate
    await svc.import_pack(USER, "investment", **DEPLOY)
    again = [v for v in await connector_svc.list_connectors(USER) if v.slug == "wind-mcp"]
    assert len(again) == 1


async def test_import_pack_materializes_skills(svc: AgentPackService, tmp_path) -> None:
    # bundled skills land in the official-skills dir on import (deferred from
    # boot), so they're in the library and resolvable at session time.
    await svc.import_pack(USER, "investment", **DEPLOY)
    skills_dir = tmp_path / "official-skills"
    assert (skills_dir / "china-dcf" / "SKILL.md").is_file()
    assert (skills_dir / "china-sector-overview").is_dir()


async def test_import_pack_idempotent(svc: AgentPackService) -> None:
    await svc.import_pack(USER, "investment", **DEPLOY)
    res2 = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res2["created"] == 0
    assert res2["skipped"] == 4
    assert len({r.slug for r in res2["roles"]}) == 4


async def test_partial_import_only_fills_missing(svc: AgentPackService) -> None:
    await svc._agents.create_agent(
        USER, {"slug": "inv-model-builder", "name": "manual", **DEPLOY}
    )
    res = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res["created"] == 3
    assert res["skipped"] == 1


async def test_list_marks_added_after_import(svc: AgentPackService) -> None:
    await svc.import_pack(USER, "investment", **DEPLOY)
    packs = await svc.list_packs(USER)
    inv = next(p for p in packs if p["id"] == "investment")
    assert inv["added"] is True
    assert all(r["in_library"] for r in inv["roles"])
    content = next(p for p in packs if p["id"] == "content")
    assert content["added"] is False
    assert all(not r["in_library"] for r in content["roles"])


async def test_product_pack_has_no_equipment(svc: AgentPackService) -> None:
    # 产研 is a skill-less / connector-less team for now.
    res = await svc.import_pack(USER, "product", **DEPLOY)
    assert res["created"] == 4
    assert sorted(r.slug for r in res["roles"]) == [
        "prod-designer",
        "prod-engineer",
        "prod-pm",
        "prod-qa",
    ]
    for role in res["roles"]:
        assert role.skills == []
        assert role.connector_types == []


async def test_unknown_pack_raises(svc: AgentPackService) -> None:
    with pytest.raises(PackNotFound):
        await svc.import_pack(USER, "does-not-exist", **DEPLOY)


# -- import / export ------------------------------------------------------


def test_packaging_roundtrip(tmp_path) -> None:
    from valuz_agent.modules.agent_packs.manifest import (
        AgentPackManifest,
        PackAgent,
        PackCollection,
        PackSkill,
    )
    from valuz_agent.modules.agent_packs.packaging import (
        build_archive,
        embedded_skill_dir,
        extract_archive,
    )

    # a real on-disk skill dir to embed as files
    skill_src = tmp_path / "my-skill"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")

    manifest = AgentPackManifest(
        collection=PackCollection(name="My Pack"),
        agents=[
            PackAgent(slug="a1", name="Agent One", instructions="do things", skills=["my-skill"])
        ],
        skills=[PackSkill(slug="my-skill", source="embedded")],
    )
    data = build_archive(manifest, {"my-skill": skill_src})
    assert isinstance(data, bytes) and len(data) > 0

    parsed, root = extract_archive(data)
    assert [a.slug for a in parsed.agents] == ["a1"]
    assert parsed.agents[0].name == "Agent One"
    # the skill's files travel inside the archive
    extracted = embedded_skill_dir(root, "my-skill")
    assert extracted is not None
    assert (extracted / "SKILL.md").read_text(encoding="utf-8") == "# My Skill\n"


def test_extract_rejects_non_zip() -> None:
    from valuz_agent.modules.agent_packs.packaging import PackArchiveError, extract_archive

    with pytest.raises(PackArchiveError):
        extract_archive(b"not a zip file")


async def test_export_import_roundtrip(svc: AgentPackService, tmp_path, monkeypatch) -> None:
    # seed the source library with the investment team (agents + connectors)
    await svc.import_pack(USER, "investment", **DEPLOY)
    slugs = [
        "inv-industry-analyst",
        "inv-model-builder",
        "inv-earnings-tracker",
        "inv-report-writer",
    ]
    data = await svc.export_agents(
        USER, slugs, collection={"id": "inv", "name": "My Investment Team"}
    )
    assert isinstance(data, bytes) and len(data) > 0

    # import into a *separate* install (fresh db + secret store), simulating a
    # different machine — connector slugs are globally unique per install.
    # Pin both skill roots under tmp so an embedded-skill install never touches
    # the real ~/.agents tree.
    monkeypatch.setenv("VALUZ_OFFICIAL_SKILLS_DIR", str(tmp_path / "dest-official"))
    monkeypatch.setenv("VALUZ_USER_SKILLS_DIR", str(tmp_path / "dest-user-skills"))
    dest, session2, engine2 = await _build_service(tmp_path / "dest")
    try:
        preview = await dest.preview_import(USER, data)
        assert preview["preview_id"]
        assert preview["collection"]["name"] == "My Investment Team"
        assert {a["slug"] for a in preview["agents"]} == set(slugs)
        assert all(not a["in_library"] for a in preview["agents"])
        assert {c["slug"] for c in preview["connectors"]} >= {"wind-mcp", "akshare-mcp"}

        result = await dest.confirm_import(USER, preview["preview_id"], **DEPLOY)
        assert result["created"] == 4
        dest_agents = {a.slug for a in await dest._agents.list_agents(USER)}
        assert set(slugs) <= dest_agents
        dest_conns = {v.slug for v in await dest._agents._connectors.list_connectors(USER)}
        assert {"wind-mcp", "akshare-mcp", "ifind-mcp", "china-news-mcp"} <= dest_conns
        # the staged preview is consumed — confirming again fails
        from valuz_agent.modules.agent_packs.errors import PackImportFailed

        with pytest.raises(PackImportFailed):
            await dest.confirm_import(USER, preview["preview_id"], **DEPLOY)
    finally:
        await session2.close()
        await engine2.dispose()


async def test_export_unknown_agent_raises(svc: AgentPackService) -> None:
    from valuz_agent.modules.agents.service import AgentNotFoundError

    with pytest.raises(AgentNotFoundError):
        await svc.export_agents(USER, ["nope"], collection=None)
