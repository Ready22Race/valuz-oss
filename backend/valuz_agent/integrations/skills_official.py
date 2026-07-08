from __future__ import annotations

from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.skills_filesystem import (
    _coerce_version,
    _compute_dir_hash,
    _detect_manifest,
    _read_manifest_cached,
)
from valuz_agent.integrations.skills_official_bootstrap import is_bundled_skill
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest


def _default_official_skill_root(user_id: str) -> Path:
    """Canonical home for officially-distributed skills.

    Always reads through ``fs_registry`` so the location stays
    consistent with the bootstrap sync target — both surfaces resolve
    to ``~/.valuz-oss/official-skills/`` by default.
    """
    return fs_registry.official_skill_root(user_id=user_id)


class OfficialSkillSource:
    name = "official"

    def __init__(self, official_dir: Path | None = None) -> None:
        self._dir = official_dir

    def list_skills(
        self, ctx: RuntimeContext, *, compute_content_hash: bool = True
    ) -> list[SkillManifest]:
        """List official skill manifests.

        ``compute_content_hash`` gates ``_compute_dir_hash`` (reads every file in
        each skill dir — slow on a network filesystem, needed only by the indexer).
        Display/catalog listing passes ``False`` and reads only each SKILL.md
        (cached). See ``FilesystemSkillSource.list_skills``.
        """
        if ctx.user_id is None:
            raise ValueError("user_id is required to list official skills")
        official_dir = self._dir or _default_official_skill_root(ctx.user_id)
        if not official_dir.exists():
            return []

        manifests: list[SkillManifest] = []
        for skill_dir in sorted(p for p in official_dir.iterdir() if p.is_dir()):
            manifest_path = _detect_manifest(skill_dir)
            if manifest_path is None:
                continue

            metadata, body, _raw, manifest_hash = _read_manifest_cached(manifest_path)
            name = str(metadata.get("name") or skill_dir.name)
            description = str(metadata.get("description") or self._summary_from_body(body))
            tags = metadata.get("tags")
            version = _coerce_version(metadata.get("version"))
            content_hash = _compute_dir_hash(skill_dir) if compute_content_hash else None

            bundled = is_bundled_skill(skill_dir)
            manifests.append(
                SkillManifest(
                    id=f"official:{skill_dir.name}",
                    name=name,
                    description=description,
                    scope="official",
                    source="official",
                    path=str(skill_dir.resolve(strict=False)),
                    slug=skill_dir.name,
                    readonly=True,
                    deletable=False,
                    is_locked=False if bundled else True,
                    lock_reason=None if bundled else "Connect Reportify to unlock official skills",
                    origin_label="Built-in" if bundled else "Official",
                    tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
                    content_hash=content_hash,
                    manifest_hash=manifest_hash,
                    version=version,
                )
            )
        return manifests

    @staticmethod
    def _summary_from_body(body: str) -> str:
        for line in body.splitlines():
            candidate = line.strip()
            if candidate and not candidate.startswith("#"):
                return candidate[:180]
        return "Official skill."
