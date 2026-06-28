"""Tests for the ``propose_agent`` / ``list_skills`` natural-language agent
creation tools (``integrations/tools_agent_proposal.py``).

Uses the ``monkeypatch.setattr(db_mod, "AsyncSessionLocal", ...)`` pattern so
the handlers' internal ``async_unit_of_work()`` binds to an in-memory SQLite
seeded with the skill index + connector tables.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Side-effect import — surfaces ``src.*`` on sys.path before importing it.
import valuz_agent.boot.kernel  # noqa: F401

from src.core.tools import ExecContext

from valuz_agent.infra.database import Base
from valuz_agent.integrations import tools_agent_proposal as tap
from valuz_agent.integrations.tools_agent_proposal import (
    _list_agents_handler,
    _list_model_options_handler,
    _list_project_members_handler,
    _list_skills_handler,
    _propose_agent_handler,
    _update_agent_handler,
    _validate_runtime_model,
)
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.skills.models import SkillIndexRow

USER_ID = "local-test-owner"


@pytest_asyncio.fixture
async def seeded_db(monkeypatch):
    """In-memory async SQLite with the skill index + connector tables, bound
    into ``async_unit_of_work`` via ``AsyncSessionLocal``."""
    import valuz_agent.infra.db as db_mod

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    SkillIndexRow.__table__,
                    ConnectorRow.__table__,
                    ConnectorAttrRow.__table__,
                    ConnectorOAuthRow.__table__,
                    AgentRow.__table__,
                    ProjectMemberRow.__table__,
                ],
            )
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)

    # Seed one indexed skill + one connector owned by the test owner.
    async with factory() as db:
        db.add(
            SkillIndexRow(
                slug="market-research",
                name="Market Research",
                description="Research helper",
                scope="user",
                source="filesystem",
                source_path="/tmp/skills/market-research",
                user_id=USER_ID,
            )
        )
        db.add(
            ConnectorRow(
                slug="github",
                display_name="GitHub",
                connector_type="custom",
                transport="http",
                url="https://example.com/mcp",
                user_id=USER_ID,
            )
        )
        db.add(
            AgentRow(
                slug="research-helper",
                name="Research Helper",
                description="An existing library agent",
                source="custom",
                user_id=USER_ID,
            )
        )
        await db.commit()
    yield factory
    await engine.dispose()


def _ctx() -> ExecContext:
    return ExecContext(session_id="sess-1")


def _empty_catalog():
    from valuz_agent.modules.settings.model_options import (
        CurrentDefault,
        ModelOptionsResponse,
    )

    return ModelOptionsResponse(
        current=CurrentDefault(runtime=None, provider_id=None, model=None),
        groups=[],
    )


def _catalog(*models: tuple[str, list[str]]):
    """Build a one-provider catalog from ``(model_id, [runtimes])`` pairs."""
    from valuz_agent.modules.settings.model_options import (
        CurrentDefault,
        ModelOption,
        ModelOptionGroup,
        ModelOptionProvider,
        ModelOptionsResponse,
    )

    opts = [
        ModelOption(
            model_id=mid,
            provider_id="p1",
            label=mid,
            runtimes=rts,
            default_runtime=rts[0],
            is_current_default=False,
        )
        for mid, rts in models
    ]
    prov = ModelOptionProvider(
        provider_id="p1",
        label="Test Channel",
        kind="anthropic",
        source="user",
        cli_tool=None,
        status="available",
        unavailable_reason=None,
        models=opts,
    )
    return ModelOptionsResponse(
        current=CurrentDefault(runtime=None, provider_id=None, model=None),
        groups=[ModelOptionGroup(key="api_key", providers=[prov])],
    )


def _set_catalog(monkeypatch, catalog) -> None:
    async def _gather(_db, _user_id):
        return catalog

    monkeypatch.setattr(tap, "_gather_model_options", _gather)


@pytest.fixture(autouse=True)
def _stub_model_catalog(monkeypatch):
    """Default the handlers' model catalog to empty so existing tests don't
    depend on provider tables. Individual tests override via ``_set_catalog``."""
    _set_catalog(monkeypatch, _empty_catalog())


async def test_propose_minimal_ok(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "Helper", "instructions": "Be helpful."}, _ctx()
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["spec"]["name"] == "Helper"
    assert payload["spec"]["runtime"] == "claude_agent"
    assert payload["warnings"] == []


async def test_missing_required_fields(seeded_db) -> None:
    res = await _propose_agent_handler({"name": "", "instructions": "x"}, _ctx())
    assert res.is_error
    assert "name" in res.content


async def test_invalid_runtime(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "runtime": "bogus"}, _ctx()
    )
    assert res.is_error
    assert "runtime" in res.content


async def test_unindexed_skill_rejected(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "skills": ["does-not-exist"]}, _ctx()
    )
    assert res.is_error
    assert "does-not-exist" in res.content


async def test_indexed_skill_ok(seeded_db) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "skills": ["market-research"]}, _ctx()
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["spec"]["skills"] == ["market-research"]


async def test_missing_connector_warns_not_fails(seeded_db) -> None:
    res = await _propose_agent_handler(
        {
            "name": "H",
            "instructions": "i",
            "connectors": ["github", "ghost-connector"],
        },
        _ctx(),
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    # Existing connector binds; the missing one is surfaced as a warning.
    assert payload["spec"]["connectors"] == ["github", "ghost-connector"]
    assert any("ghost-connector" in w for w in payload["warnings"])


async def test_list_skills_returns_indexed(seeded_db) -> None:
    res = await _list_skills_handler({}, _ctx())
    assert not res.is_error
    payload = json.loads(res.content)
    slugs = {s["slug"] for s in payload["skills"]}
    assert "market-research" in slugs


@pytest.mark.parametrize("effort", ["low", "max"])
async def test_valid_effort_ok(seeded_db, effort) -> None:
    res = await _propose_agent_handler(
        {"name": "H", "instructions": "i", "effort": effort}, _ctx()
    )
    assert not res.is_error
    assert json.loads(res.content)["spec"]["effort"] == effort


async def test_list_agents_returns_library(seeded_db) -> None:
    res = await _list_agents_handler({}, _ctx())
    assert not res.is_error
    slugs = {a["slug"] for a in json.loads(res.content)["agents"]}
    assert "research-helper" in slugs


async def test_list_members_requires_project(seeded_db, monkeypatch) -> None:
    async def _no_project(_sid):
        return None

    monkeypatch.setattr(tap, "_resolve_project_id", _no_project)
    res = await _list_project_members_handler({}, _ctx())
    assert res.is_error
    assert "no project" in res.content


async def test_list_members_with_project(seeded_db, monkeypatch) -> None:
    async def _project(_sid):
        return "p1"

    monkeypatch.setattr(tap, "_resolve_project_id", _project)
    # Seed a member referencing the existing library agent.
    import valuz_agent.infra.db as db_mod

    async with db_mod.AsyncSessionLocal() as db:
        db.add(
            ProjectMemberRow(
                project_id="p1",
                agent_slug="research-helper-1",
                source_agent_slug="research-helper",
                user_id=USER_ID,
            )
        )
        await db.commit()

    res = await _list_project_members_handler({}, _ctx())
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["project_id"] == "p1"
    assert payload["members"][0]["source_agent_slug"] == "research-helper"
    assert payload["members"][0]["name"] == "Research Helper"


async def test_deploy_requires_project(seeded_db, monkeypatch) -> None:
    from valuz_agent.integrations.tools_agent_proposal import _deploy_agent_handler

    async def _no_project(_sid):
        return None

    monkeypatch.setattr(tap, "_resolve_project_id", _no_project)
    res = await _deploy_agent_handler({"agent_slug": "research-helper"}, _ctx())
    assert res.is_error
    assert "no project" in res.content


# ── runtime / model validation (the codex/claude mix-up bug) ─────────────


def test_validate_claude_model_on_codex_rejected() -> None:
    """A configured Claude model paired with the codex runtime is the bug."""
    catalog = _catalog(("claude-sonnet-4-6", ["claude_agent", "deepagents"]))
    error, _ = _validate_runtime_model("codex", "claude-sonnet-4-6", catalog)
    assert error is not None
    assert "cannot run on runtime 'codex'" in error


def test_validate_codex_model_on_claude_agent_rejected() -> None:
    catalog = _catalog(("gpt-5-codex", ["codex"]))
    error, _ = _validate_runtime_model("claude_agent", "gpt-5-codex", catalog)
    assert error is not None
    assert "claude_agent" in error


def test_validate_compatible_pair_ok() -> None:
    catalog = _catalog(
        ("claude-sonnet-4-6", ["claude_agent", "deepagents"]),
        ("gpt-5-codex", ["codex"]),
    )
    assert _validate_runtime_model("codex", "gpt-5-codex", catalog) == (None, [])
    assert _validate_runtime_model("claude_agent", "claude-sonnet-4-6", catalog) == (None, [])


def test_validate_omitted_model_on_codex_rejected() -> None:
    """Omitting the model on codex would default to claude-sonnet-4-6, which
    codex can't drive — caught even with an empty catalog."""
    error, _ = _validate_runtime_model("codex", "", _empty_catalog())
    assert error is not None
    assert tap.DEFAULT_MODEL in error


def test_validate_omitted_model_on_claude_agent_ok() -> None:
    assert _validate_runtime_model("claude_agent", "", _empty_catalog()) == (None, [])


def test_validate_unknown_explicit_model_warns_only() -> None:
    catalog = _catalog(("claude-sonnet-4-6", ["claude_agent"]))
    error, warnings = _validate_runtime_model("claude_agent", "gpt-custom-x", catalog)
    assert error is None
    assert warnings and "gpt-custom-x" in warnings[0]


async def test_propose_codex_with_claude_model_rejected(seeded_db, monkeypatch) -> None:
    _set_catalog(monkeypatch, _catalog(("claude-sonnet-4-6", ["claude_agent", "deepagents"])))
    res = await _propose_agent_handler(
        {
            "name": "Coder",
            "instructions": "Write code.",
            "runtime": "codex",
            "model": "claude-sonnet-4-6",
        },
        _ctx(),
    )
    assert res.is_error
    assert "codex" in res.content


async def test_propose_codex_with_compatible_model_ok(seeded_db, monkeypatch) -> None:
    _set_catalog(monkeypatch, _catalog(("gpt-5-codex", ["codex"])))
    res = await _propose_agent_handler(
        {
            "name": "Coder",
            "instructions": "Write code.",
            "runtime": "codex",
            "model": "gpt-5-codex",
        },
        _ctx(),
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["spec"]["model"] == "gpt-5-codex"


# ── update_agent (direct edit of an existing library agent) ──────────────


async def _get_agent_row(slug: str):
    import valuz_agent.infra.db as db_mod
    from valuz_agent.modules.agents.datastore import AgentDatastore

    async with db_mod.AsyncSessionLocal() as db:
        return await AgentDatastore(db).get_agent(USER_ID, slug)


async def test_update_agent_changes_instructions(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "instructions": "New method.", "description": "d2"},
        _ctx(),
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["slug"] == "research-helper"
    assert set(payload["changed"]) == {"instructions", "description"}
    row = await _get_agent_row("research-helper")
    assert row.instructions == "New method."
    assert row.description == "d2"


async def test_update_agent_unknown_slug(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "ghost", "instructions": "x"}, _ctx()
    )
    assert res.is_error
    assert "ghost" in res.content


async def test_update_agent_no_fields(seeded_db) -> None:
    res = await _update_agent_handler({"agent_slug": "research-helper"}, _ctx())
    assert res.is_error
    assert "no editable fields" in res.content


async def test_update_agent_blank_name_rejected(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "name": "  "}, _ctx()
    )
    assert res.is_error
    assert "name" in res.content
    # The agent's name is untouched.
    row = await _get_agent_row("research-helper")
    assert row.name == "Research Helper"


async def test_update_agent_unindexed_skill_rejected(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "skills": ["does-not-exist"]}, _ctx()
    )
    assert res.is_error
    assert "does-not-exist" in res.content


async def test_update_agent_replaces_skills(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "skills": ["market-research"]}, _ctx()
    )
    assert not res.is_error
    row = await _get_agent_row("research-helper")
    assert row.skills == ["market-research"]


async def test_update_agent_omitted_skills_and_connectors_preserved(seeded_db) -> None:
    """Not passing skills/connectors leaves them exactly as they were — only the
    fields actually submitted are touched."""
    # Give the agent equipment first.
    await _update_agent_handler(
        {
            "agent_slug": "research-helper",
            "skills": ["market-research"],
            "connectors": ["github"],
        },
        _ctx(),
    )
    # Now edit only the description — equipment must survive untouched.
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "description": "edited"}, _ctx()
    )
    assert not res.is_error
    assert json.loads(res.content)["changed"] == ["description"]
    row = await _get_agent_row("research-helper")
    assert row.description == "edited"
    assert row.skills == ["market-research"]
    assert row.connector_types == ["github"]


async def test_update_agent_empty_list_clears(seeded_db) -> None:
    """Submitting an explicit empty list overwrites (clears) the set."""
    await _update_agent_handler(
        {"agent_slug": "research-helper", "skills": ["market-research"]}, _ctx()
    )
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "skills": []}, _ctx()
    )
    assert not res.is_error
    assert (await _get_agent_row("research-helper")).skills == []


async def test_update_agent_clears_effort_with_empty_string(seeded_db) -> None:
    # Set an effort, then clear it.
    await _update_agent_handler(
        {"agent_slug": "research-helper", "effort": "high"}, _ctx()
    )
    assert (await _get_agent_row("research-helper")).effort == "high"
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "effort": ""}, _ctx()
    )
    assert not res.is_error
    assert (await _get_agent_row("research-helper")).effort is None


async def test_update_agent_runtime_only_validated_against_existing_model(
    seeded_db, monkeypatch
) -> None:
    """Switching ONLY the runtime to codex while the agent keeps its Claude
    model is rejected — the effective pair is checked, not just the field
    passed."""
    _set_catalog(monkeypatch, _catalog(("claude-sonnet-4-6", ["claude_agent", "deepagents"])))
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "runtime": "codex"}, _ctx()
    )
    assert res.is_error
    assert "update_agent:" in res.content
    assert "codex" in res.content
    # Rejected before any write — runtime stays claude_agent.
    assert (await _get_agent_row("research-helper")).runtime == "claude_agent"


async def test_update_agent_runtime_and_model_together_ok(seeded_db, monkeypatch) -> None:
    _set_catalog(monkeypatch, _catalog(("gpt-5-codex", ["codex"])))
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "runtime": "codex", "model": "gpt-5-codex"},
        _ctx(),
    )
    assert not res.is_error
    row = await _get_agent_row("research-helper")
    assert row.runtime == "codex"
    assert row.model == "gpt-5-codex"


async def test_update_agent_missing_connector_warns_not_fails(seeded_db) -> None:
    res = await _update_agent_handler(
        {"agent_slug": "research-helper", "connectors": ["github", "ghost-connector"]},
        _ctx(),
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert any("ghost-connector" in w for w in payload["warnings"])
    # The write still lands with the full set.
    assert (await _get_agent_row("research-helper")).connector_types == [
        "github",
        "ghost-connector",
    ]


async def test_list_model_options_returns_runtimes_and_models(seeded_db, monkeypatch) -> None:
    _set_catalog(monkeypatch, _catalog(("claude-sonnet-4-6", ["claude_agent", "deepagents"])))
    res = await _list_model_options_handler({}, _ctx())
    assert not res.is_error
    payload = json.loads(res.content)
    runtime_ids = {r["id"] for r in payload["runtimes"]}
    assert {"claude_agent", "codex", "deepagents"} <= runtime_ids
    model_ids = {
        m["model_id"] for p in payload["providers"] for m in p["models"]
    }
    assert "claude-sonnet-4-6" in model_ids
    assert "current_default" in payload
