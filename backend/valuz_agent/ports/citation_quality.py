"""Trusted, layered citation quality policies.

OSS owns the baseline policy and the ordered merge contract. Commercial and
distribution overlays register additive providers in fixed slots; a later
provider can tighten the effective policy, but cannot replace an earlier
layer or disable an OSS invariant.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml

CitationPolicyMode = Literal["required-on-evidence", "strict-domain"]
CitationPolicyLayer = Literal["oss", "commercial", "distribution"]
CitationPolicySnapshotLayer = CitationPolicyLayer | Literal["effective"]

_LAYER_ORDER: tuple[CitationPolicyLayer, ...] = (
    "oss",
    "commercial",
    "distribution",
)
_MODE_STRENGTH: dict[CitationPolicyMode, int] = {
    "required-on-evidence": 0,
    "strict-domain": 1,
}
_PUBLISH_STRENGTH = {"ready": 0, "draft_only": 1, "blocked": 2}
_MAX_POLICY_BYTES = 128_000
_TASK_COVERAGE_SECTIONS = {
    "contract": {
        "requirement_kinds",
        "dimensions",
        "selectors",
        "output_constraints",
        "ontology_refs",
        "dimension_ontology",
        "topic_ontology",
    },
    "retrieval": {
        "content_mappings",
        "identity_mappings",
        "candidate_selection",
        "source_constraints",
    },
    "answer": {"structure_rules"},
    "remediation": {"allowed_actions"},
    "observability": {"record"},
}
_OSS_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "citation-policies" / "oss" / "policy.yaml"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CitationQualityPolicySnapshot:
    policy_id: str
    revision: str
    mode: CitationPolicyMode
    config: dict[str, Any]
    layer: CitationPolicySnapshotLayer = "distribution"
    layers: tuple[dict[str, str], ...] = field(default_factory=tuple)
    unavailable_layers: tuple[CitationPolicyLayer, ...] = field(default_factory=tuple)

    def session_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "policy_id": self.policy_id,
            "revision": self.revision,
            "mode": self.mode,
            "config": copy.deepcopy(self.config),
        }
        if self.layers:
            payload["layers"] = copy.deepcopy(list(self.layers))
        if self.unavailable_layers:
            payload["unavailable_layers"] = list(self.unavailable_layers)
        # Fail at the trusted host boundary instead of sending an
        # unserializable or unbounded object into a remote kernel.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_POLICY_BYTES:
            raise ValueError("citation quality policy exceeds 128 KiB")
        return payload


class CitationQualityPolicyPort(Protocol):
    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot | None: ...


class PackagedCitationQualityPolicy:
    """Load one immutable policy pack shipped with a trusted distribution."""

    def __init__(
        self,
        path: Path,
        *,
        policy_id: str,
        layer: CitationPolicyLayer,
        revision_prefix: str,
    ) -> None:
        self._path = path
        self._policy_id = policy_id
        self._layer = layer
        self._revision_prefix = revision_prefix

    def load(self) -> dict[str, Any]:
        return load_citation_policy_document(
            self._path,
            expected_policy_id=self._policy_id,
            expected_layer=self._layer,
            revision_prefix=self._revision_prefix,
        )

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot:
        if not user_id:
            raise ValueError("user_id is required")
        policy = self.load()
        return citation_policy_snapshot_from_document(
            policy,
            session_metadata=session_metadata,
        )


class CitationQualityPolicyRegistry:
    """Fixed-order policy registry with monotonic merge semantics."""

    def __init__(self, *, oss_provider: CitationQualityPolicyPort | None = None) -> None:
        self._providers: dict[CitationPolicyLayer, CitationQualityPolicyPort] = {
            "oss": oss_provider
            or PackagedCitationQualityPolicy(
                _OSS_POLICY_PATH,
                policy_id="oss-citation-baseline",
                layer="oss",
                revision_prefix="citation-baseline-v",
            )
        }

    def register(
        self,
        layer: Literal["commercial", "distribution"],
        provider: CitationQualityPolicyPort,
    ) -> None:
        if layer in self._providers:
            raise RuntimeError(f"citation quality policy layer already registered: {layer}")
        self._providers[layer] = provider

    def unregister(self, layer: Literal["commercial", "distribution"]) -> None:
        """Remove an overlay layer during explicit lifecycle teardown/tests."""

        self._providers.pop(layer, None)

    def provider(self, layer: CitationPolicyLayer) -> CitationQualityPolicyPort | None:
        return self._providers.get(layer)

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot:
        snapshots: list[CitationQualityPolicySnapshot] = []
        unavailable: list[CitationPolicyLayer] = []
        for layer in _LAYER_ORDER:
            provider = self._providers.get(layer)
            if provider is None:
                continue
            try:
                snapshot = await provider.resolve(
                    user_id,
                    session_metadata=session_metadata,
                )
                if snapshot is None:
                    raise RuntimeError("registered provider returned no snapshot")
                if snapshot.layer != layer:
                    raise RuntimeError(
                        f"citation policy layer mismatch: registered={layer} "
                        f"snapshot={snapshot.layer}"
                    )
            except Exception:
                if layer == "oss":
                    raise
                logger.exception("citation quality policy layer unavailable: %s", layer)
                unavailable.append(layer)
                continue
            snapshots.append(snapshot)

        if not snapshots or snapshots[0].layer != "oss":
            raise RuntimeError("OSS citation quality policy is unavailable")
        return merge_citation_quality_policy_snapshots(
            snapshots,
            unavailable_layers=unavailable,
        )


class NoopCitationQualityPolicy:
    """Compatibility noop for consumers that have not adopted the registry."""

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> None:
        del user_id, session_metadata
        return None


def citation_policy_snapshot_from_document(
    policy: dict[str, Any],
    *,
    session_metadata: dict[str, Any],
) -> CitationQualityPolicySnapshot:
    activation = policy.get("activation")
    activation = activation if isinstance(activation, dict) else {}
    layer = cast(CitationPolicyLayer, policy["layer"])
    return CitationQualityPolicySnapshot(
        policy_id=str(policy["policy_id"]),
        revision=str(policy["version"]),
        mode=citation_policy_activation_mode(activation, session_metadata),
        config={
            key: copy.deepcopy(value)
            for key, value in policy.items()
            if key not in {"version", "policy_id", "layer"}
        },
        layer=layer,
    )


def citation_policy_activation_mode(
    activation: dict[str, Any],
    session_metadata: dict[str, Any],
) -> CitationPolicyMode:
    valuz = session_metadata.get("valuz")
    valuz = valuz if isinstance(valuz, dict) else {}
    research = valuz.get("document_research")
    if isinstance(research, dict) and research.get("purpose") == "document-research":
        document_mode = activation.get("document_research_mode")
        if document_mode in _MODE_STRENGTH:
            return cast(CitationPolicyMode, document_mode)
    creation = valuz.get("creation_context")
    creation = creation if isinstance(creation, dict) else {}
    task_type = creation.get("task_type") or creation.get("kind")
    task_modes = activation.get("task_types")
    if isinstance(task_modes, dict) and isinstance(task_type, str):
        selected = task_modes.get(task_type)
        if selected in _MODE_STRENGTH:
            return cast(CitationPolicyMode, selected)
    default = activation.get("default_mode")
    return cast(
        CitationPolicyMode,
        default if default in _MODE_STRENGTH else "required-on-evidence",
    )


def merge_citation_quality_policy_snapshots(
    snapshots: list[CitationQualityPolicySnapshot],
    *,
    unavailable_layers: list[CitationPolicyLayer] | None = None,
) -> CitationQualityPolicySnapshot:
    if not snapshots:
        raise ValueError("at least one citation policy snapshot is required")
    expected_order = [layer for layer in _LAYER_ORDER if any(s.layer == layer for s in snapshots)]
    actual_order = [snapshot.layer for snapshot in snapshots]
    if actual_order != expected_order or len(set(actual_order)) != len(actual_order):
        raise ValueError("citation policy snapshots must use unique fixed-order layers")

    config: dict[str, Any] = {}
    mode: CitationPolicyMode = "required-on-evidence"
    layers: list[dict[str, str]] = []
    for snapshot in snapshots:
        config = _monotonic_merge(config, snapshot.config)
        if _MODE_STRENGTH[snapshot.mode] > _MODE_STRENGTH[mode]:
            mode = snapshot.mode
        layers.append(
            {
                "layer": snapshot.layer,
                "policy_id": snapshot.policy_id,
                "revision": snapshot.revision,
                "status": "active",
            }
        )
    for layer in unavailable_layers or []:
        layers.append(
            {
                "layer": layer,
                "policy_id": "unavailable",
                "revision": "unavailable",
                "status": "unavailable",
            }
        )
    layers.sort(key=lambda item: _LAYER_ORDER.index(cast(CitationPolicyLayer, item["layer"])))
    revision_input = json.dumps(
        {"layers": layers, "mode": mode, "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    revision = f"citation-effective-{hashlib.sha256(revision_input.encode()).hexdigest()[:16]}"
    return CitationQualityPolicySnapshot(
        policy_id="effective-citation-policy",
        revision=revision,
        mode=mode,
        config=config,
        layer="effective",
        layers=tuple(layers),
        unavailable_layers=tuple(unavailable_layers or ()),
    )


def _monotonic_merge(base: Any, addition: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(base, dict) and isinstance(addition, dict):
        result = copy.deepcopy(base)
        for key, value in addition.items():
            result[key] = (
                _monotonic_merge(result[key], value, (*path, str(key)))
                if key in result
                else copy.deepcopy(value)
            )
        return result
    if isinstance(base, list) and isinstance(addition, list):
        # Source tiers are ordered matchers: a distribution's more-specific
        # match must run before the commercial generic fallback while both
        # definitions remain in the effective snapshot.
        result = copy.deepcopy(addition if path == ("source_tiers",) else base)
        candidates = base if path == ("source_tiers",) else addition
        seen = {_stable_json(item) for item in result}
        for item in candidates:
            marker = _stable_json(item)
            if marker not in seen:
                result.append(copy.deepcopy(item))
                seen.add(marker)
        return result
    if isinstance(base, bool) and isinstance(addition, bool):
        return base or addition
    if path == ("failure", "publish_on_degraded"):
        base_strength = _PUBLISH_STRENGTH.get(str(base), 0)
        addition_strength = _PUBLISH_STRENGTH.get(str(addition), 0)
        return addition if addition_strength > base_strength else base
    if path == ("failure", "repair_attempts"):
        try:
            return min(max(int(base), 0), max(int(addition), 0), 1)
        except (TypeError, ValueError):
            return min(max(int(base), 0), 1)
    return copy.deepcopy(addition)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=32)
def _load_citation_policy_document_cached(
    path: str,
    expected_policy_id: str,
    expected_layer: CitationPolicyLayer,
    revision_prefix: str,
) -> dict[str, Any]:
    policy_path = Path(path)
    if policy_path.stat().st_size > _MAX_POLICY_BYTES:
        raise RuntimeError("citation policy file exceeds 128 KiB")
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("citation policy must be a mapping")
    if payload.get("policy_id") != expected_policy_id:
        raise RuntimeError("citation policy id mismatch")
    if payload.get("layer") != expected_layer:
        raise RuntimeError("citation policy layer mismatch")
    revision = payload.get("version")
    if not isinstance(revision, str) or not revision.startswith(revision_prefix):
        raise RuntimeError("citation policy version is invalid")
    activation = payload.get("activation")
    if not isinstance(activation, dict) or activation.get("default_mode") not in _MODE_STRENGTH:
        raise RuntimeError("citation policy activation is invalid")
    for key in ("rules", "failure"):
        if key in payload and not isinstance(payload[key], dict):
            raise RuntimeError(f"citation policy {key} must be a mapping")
    _validate_task_coverage_policy(payload.get("task_coverage"))
    # Validate JSON safety and the host/kernel metadata budget up front.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_POLICY_BYTES:
        raise RuntimeError("citation policy payload exceeds 128 KiB")
    return payload


def _validate_task_coverage_policy(value: Any) -> None:
    """Validate the shared additive Task Coverage policy surface.

    Lists intentionally remain open vocabularies: OSS owns the resolver
    primitives while later layers may add named dimensions, selectors and
    mappings without changing this loader.  What is fixed here is the shape
    consumed by the shared resolver, so a typo cannot silently become a
    distribution-only side channel.
    """

    if value is None:
        return
    if not isinstance(value, dict):
        raise RuntimeError("citation policy task_coverage must be a mapping")
    unknown_sections = set(value) - set(_TASK_COVERAGE_SECTIONS)
    if unknown_sections:
        raise RuntimeError(
            "citation policy task_coverage has unknown sections: "
            + ", ".join(sorted(unknown_sections))
        )
    for section, allowed_keys in _TASK_COVERAGE_SECTIONS.items():
        section_value = value.get(section)
        if section_value is None:
            continue
        if not isinstance(section_value, dict):
            raise RuntimeError(f"citation policy task_coverage.{section} must be a mapping")
        unknown_keys = set(section_value) - allowed_keys
        if unknown_keys:
            raise RuntimeError(
                f"citation policy task_coverage.{section} has unknown keys: "
                + ", ".join(sorted(unknown_keys))
            )
        for key, entries in section_value.items():
            if key == "dimension_ontology":
                _validate_task_coverage_dimension_ontology(entries)
                continue
            if key == "topic_ontology":
                _validate_task_coverage_topic_ontology(entries)
                continue
            if not isinstance(entries, list):
                raise RuntimeError(f"citation policy task_coverage.{section}.{key} must be a list")
            if key == "content_mappings":
                _validate_task_coverage_content_mappings(entries)
            elif key == "identity_mappings":
                _validate_task_coverage_identity_mappings(entries)
            elif not all(isinstance(entry, str) and entry for entry in entries):
                raise RuntimeError(
                    f"citation policy task_coverage.{section}.{key} must contain non-empty strings"
                )


def _validate_task_coverage_topic_ontology(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"revision", "topics"}:
        raise RuntimeError(
            "citation policy task_coverage topic ontology requires revision and topics"
        )
    revision = value.get("revision")
    topics = value.get("topics")
    if not isinstance(revision, str) or not revision or not isinstance(topics, dict) or not topics:
        raise RuntimeError(
            "citation policy task_coverage topic ontology revision or topics is invalid"
        )
    for topic_id, definition in topics.items():
        if (
            not isinstance(topic_id, str)
            or not topic_id
            or not isinstance(definition, dict)
            or set(definition) != {"aliases"}
        ):
            raise RuntimeError(
                "citation policy task_coverage topic ontology definitions are invalid"
            )
        aliases = definition.get("aliases")
        if (
            not isinstance(aliases, list)
            or not aliases
            or not all(isinstance(alias, str) and alias for alias in aliases)
        ):
            raise RuntimeError(
                "citation policy task_coverage topic ontology aliases are invalid"
            )


def _validate_task_coverage_dimension_ontology(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"revision", "dimensions"}:
        raise RuntimeError(
            "citation policy task_coverage dimension ontology requires "
            "revision and dimensions"
        )
    revision = value.get("revision")
    dimensions = value.get("dimensions")
    if (
        not isinstance(revision, str)
        or not revision
        or not isinstance(dimensions, dict)
        or not dimensions
    ):
        raise RuntimeError(
            "citation policy task_coverage dimension ontology revision or dimensions "
            "is invalid"
        )
    for dimension_id, definition in dimensions.items():
        if (
            not isinstance(dimension_id, str)
            or not dimension_id
            or not isinstance(definition, dict)
            or set(definition) != {"aliases", "members"}
        ):
            raise RuntimeError(
                "citation policy task_coverage dimension ontology definitions are invalid"
            )
        aliases = definition.get("aliases")
        members = definition.get("members")
        if (
            not isinstance(aliases, list)
            or not aliases
            or not all(isinstance(alias, str) and alias for alias in aliases)
            or not isinstance(members, dict)
            or not members
        ):
            raise RuntimeError(
                "citation policy task_coverage dimension ontology aliases or members "
                "are invalid"
            )
        for member_id, member_definition in members.items():
            if (
                not isinstance(member_id, str)
                or not member_id
                or not isinstance(member_definition, dict)
                or set(member_definition) != {"aliases"}
            ):
                raise RuntimeError(
                    "citation policy task_coverage dimension ontology member "
                    "definitions are invalid"
                )
            member_aliases = member_definition.get("aliases")
            if (
                not isinstance(member_aliases, list)
                or not member_aliases
                or not all(
                    isinstance(alias, str) and alias for alias in member_aliases
                )
            ):
                raise RuntimeError(
                    "citation policy task_coverage dimension ontology member aliases "
                    "are invalid"
                )


def _validate_task_coverage_content_mappings(entries: list[Any]) -> None:
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("citation policy task_coverage retrieval mappings must be mappings")
        required = {"id", "role", "tool_patterns"}
        allowed = {*required, "coverage_text", "coverage_scope"}
        if not required.issubset(entry) or set(entry) - allowed:
            raise RuntimeError(
                "citation policy task_coverage retrieval mapping requires id, "
                "role and tool_patterns, with optional coverage_text and coverage_scope"
            )
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise RuntimeError("citation policy task_coverage retrieval mapping id is invalid")
        if entry.get("role") not in {"candidate", "content"}:
            raise RuntimeError("citation policy task_coverage retrieval mapping role is invalid")
        if entry.get("coverage_text", "result") not in {
            "result",
            "input-and-result",
        }:
            raise RuntimeError(
                "citation policy task_coverage retrieval mapping coverage_text is invalid"
            )
        if entry.get("coverage_scope", "partial") not in {
            "partial",
            "full-document",
            "full-record",
        }:
            raise RuntimeError(
                "citation policy task_coverage retrieval mapping coverage_scope is invalid"
            )
        patterns = entry.get("tool_patterns")
        if (
            not isinstance(patterns, list)
            or not patterns
            or not all(isinstance(pattern, str) and pattern for pattern in patterns)
        ):
            raise RuntimeError(
                "citation policy task_coverage retrieval mapping patterns are invalid"
            )


def _validate_task_coverage_identity_mappings(entries: list[Any]) -> None:
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(
                "citation policy task_coverage identity mappings must be mappings"
            )
        required = {"id", "tool_patterns"}
        allowed = {*required, "query_fields", "result_fields"}
        if not required.issubset(entry) or set(entry) - allowed:
            raise RuntimeError(
                "citation policy task_coverage identity mapping requires id and "
                "tool_patterns, with optional query_fields and result_fields"
            )
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise RuntimeError(
                "citation policy task_coverage identity mapping id is invalid"
            )
        for key in ("tool_patterns", "query_fields", "result_fields"):
            values = entry.get(key)
            if values is None and key != "tool_patterns":
                continue
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise RuntimeError(
                    f"citation policy task_coverage identity mapping {key} is invalid"
                )


def load_citation_policy_document(
    path: Path,
    *,
    expected_policy_id: str,
    expected_layer: CitationPolicyLayer,
    revision_prefix: str,
) -> dict[str, Any]:
    return copy.deepcopy(
        _load_citation_policy_document_cached(
            str(path.resolve()),
            expected_policy_id,
            expected_layer,
            revision_prefix,
        )
    )


__all__ = [
    "CitationPolicyLayer",
    "CitationPolicyMode",
    "CitationQualityPolicyPort",
    "CitationQualityPolicyRegistry",
    "CitationQualityPolicySnapshot",
    "NoopCitationQualityPolicy",
    "PackagedCitationQualityPolicy",
    "citation_policy_activation_mode",
    "citation_policy_snapshot_from_document",
    "load_citation_policy_document",
    "merge_citation_quality_policy_snapshots",
]
