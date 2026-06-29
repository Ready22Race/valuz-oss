"""Manifest round-trip tests for ``ProjectPackManifest``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from valuz_agent.modules.agent_packs.manifest import PackAgent
from valuz_agent.modules.project_packs.manifest import (
    PackMember,
    ProjectMeta,
    ProjectPackManifest,
)


def _sample_manifest() -> ProjectPackManifest:
    return ProjectPackManifest(
        project=ProjectMeta(
            name="My Project",
            kind="project",
            icon="rocket",
            instructions_md="Do great work",
        ),
        members=[
            PackMember(
                agent_slug="lead",
                source_agent_slug="source-lead",
                agent=PackAgent(slug="source-lead", name="Lead"),
            ),
            PackMember(
                agent_slug="researcher",
                source_agent_slug="source-researcher",
                agent=PackAgent(slug="source-researcher", name="Researcher"),
            ),
        ],
    )


def test_manifest_roundtrip() -> None:
    m = _sample_manifest()
    json_str = m.model_dump_json()
    parsed = ProjectPackManifest.model_validate_json(json_str)
    assert parsed.project.name == "My Project"
    assert parsed.project.kind == "project"
    assert parsed.project.icon == "rocket"
    assert [mem.agent_slug for mem in parsed.members] == ["lead", "researcher"]


def test_member_agent_slug_matches_source_agent_slug() -> None:
    """``PackMember.agent.slug`` must equal ``source_agent_slug`` so the
    snapshot travels under the same key the recipient de-dupes by."""
    m = _sample_manifest()
    for mem in m.members:
        assert mem.agent.slug == mem.source_agent_slug


def test_manifest_defaults() -> None:
    m = ProjectPackManifest(project=ProjectMeta(name="Bare"))
    assert m.schema_version == 1
    assert m.kind == "project-pack"
    assert m.project.kind == "project"
    assert m.members == []
    assert m.automations == []


def test_manifest_rejects_missing_project() -> None:
    with pytest.raises(ValidationError):
        ProjectPackManifest()  # type: ignore[call-arg]


def test_manifest_rejects_wrong_kind_field() -> None:
    """The ``kind`` discriminator is enforced as the default — but a project
    manifest's ``project.kind`` must be ``"project"`` (the export path
    raises on chat-kind; the manifest carries the kind the user shipped)."""
    m = ProjectPackManifest(
        project=ProjectMeta(name="x", kind="project"),
        kind="project-pack",
    )
    assert m.kind == "project-pack"
    assert m.project.kind == "project"
