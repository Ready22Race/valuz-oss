"""Unit tests for the unified :class:`PackManifest` and the v1→v2 lifters."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from valuz_agent.modules.packs_common.manifest import (
    KIND,
    SCHEMA_VERSION,
    PackAgent,
    PackCollection,
    PackConnector,
    PackManifest,
    PackProject,
    PackProjectConnector,
    PackProjectSkillConfig,
    PackSkill,
    from_legacy_agent_pack,
)


def test_collection_variant_defaults() -> None:
    m = PackManifest(collection=PackCollection(name="Pack"))
    assert m.schema_version == SCHEMA_VERSION == 2
    assert m.kind == KIND == "valuz-pack"
    assert m.project is None
    assert m.agents == []


def test_project_variant() -> None:
    m = PackManifest(project=PackProject(name="Proj"))
    assert m.collection is None
    assert m.project is not None
    assert m.project.kind == "project"


def test_exactly_one_target_rejects_neither() -> None:
    with pytest.raises(ValidationError):
        PackManifest()


def test_exactly_one_target_rejects_both() -> None:
    with pytest.raises(ValidationError):
        PackManifest(
            collection=PackCollection(name="x"),
            project=PackProject(name="y"),
        )


def test_collection_roundtrip_json() -> None:
    m = PackManifest(
        collection=PackCollection(name="Pack"),
        agents=[PackAgent(slug="a", name="A", skills=["s"])],
        skills=[PackSkill(slug="s", source="embedded")],
        connectors=[PackConnector(slug="c", source="custom")],
    )
    parsed = PackManifest.model_validate_json(m.model_dump_json(exclude_none=True))
    assert parsed.collection is not None
    assert parsed.project is None
    assert [a.slug for a in parsed.agents] == ["a"]


def test_project_roundtrip_json() -> None:
    m = PackManifest(
        project=PackProject(
            name="Proj",
            instructions_md="# guide",
            members=[{"agent_slug": "lead", "source_agent_slug": "lead-src"}],
            skills=[PackProjectSkillConfig(skill_path="/abs/skill")],
            connectors=[PackProjectConnector(slug="cc")],
        ),
        agents=[PackAgent(slug="lead-src", name="Lead")],
    )
    parsed = PackManifest.model_validate_json(m.model_dump_json(exclude_none=True))
    assert parsed.project is not None
    assert parsed.project.instructions_md == "# guide"
    assert parsed.project.members[0].agent_slug == "lead"
    assert parsed.project.skills[0].skill_path == "/abs/skill"
    assert parsed.project.connectors[0].slug == "cc"


def test_exclude_none_omits_the_unused_target() -> None:
    """An agent pack JSON carries no ``project`` key (and vice-versa)."""
    col = PackManifest(collection=PackCollection(name="Pack")).model_dump_json(exclude_none=True)
    assert '"project"' not in col
    proj = PackManifest(project=PackProject(name="P")).model_dump_json(exclude_none=True)
    assert '"collection"' not in proj


# -- legacy lifters ---------------------------------------------------------


def test_from_legacy_agent_pack() -> None:
    from valuz_agent.modules.agent_packs.manifest import AgentPackManifest

    legacy = AgentPackManifest(
        collection=PackCollection(name="Legacy"),
        agents=[PackAgent(slug="a", name="A")],
        skills=[PackSkill(slug="s", source="bundled")],
    )
    m = from_legacy_agent_pack(legacy)
    assert m.collection is not None
    assert m.collection.name == "Legacy"
    assert m.project is None
    assert [a.slug for a in m.agents] == ["a"]
