from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.ports.citation_quality import (
    CitationQualityPolicyRegistry,
    CitationQualityPolicySnapshot,
    load_citation_policy_document,
    merge_citation_quality_policy_snapshots,
)


def _snapshot(
    layer: str,
    *,
    mode: str = "required-on-evidence",
    config: dict | None = None,
) -> CitationQualityPolicySnapshot:
    return CitationQualityPolicySnapshot(
        policy_id=f"{layer}-policy",
        revision=f"{layer}-v1",
        mode=mode,  # type: ignore[arg-type]
        config=config or {},
        layer=layer,  # type: ignore[arg-type]
    )


def test_merge_is_ordered_additive_and_cannot_weaken_earlier_rules() -> None:
    merged = merge_citation_quality_policy_snapshots(
        [
            _snapshot(
                "oss",
                config={
                    "rules": {"factual_claim": {"citation_required": True}},
                    "failure": {"repair_attempts": 1, "publish_on_degraded": "ready"},
                    "checks": ["integrity"],
                },
            ),
            _snapshot(
                "commercial",
                config={
                    "rules": {
                        "factual_claim": {"citation_required": False},
                        "numeric_claim": {"require_unit": True},
                    },
                    "failure": {
                        "repair_attempts": 3,
                        "publish_on_degraded": "draft_only",
                    },
                    "checks": ["integrity", "source-independence"],
                },
            ),
            _snapshot(
                "distribution",
                mode="strict-domain",
                config={
                    "rules": {"derived_value": {"recompute": True}},
                    "failure": {"publish_on_degraded": "blocked"},
                },
            ),
        ]
    )

    assert merged.mode == "strict-domain"
    assert merged.layer == "effective"
    assert merged.config["rules"] == {
        "factual_claim": {"citation_required": True},
        "numeric_claim": {"require_unit": True},
        "derived_value": {"recompute": True},
    }
    assert merged.config["checks"] == ["integrity", "source-independence"]
    assert merged.config["failure"] == {
        "repair_attempts": 1,
        "publish_on_degraded": "blocked",
    }
    assert [item["layer"] for item in merged.layers] == [
        "oss",
        "commercial",
        "distribution",
    ]


def test_merge_rejects_out_of_order_or_duplicate_layers() -> None:
    with pytest.raises(ValueError, match="fixed-order"):
        merge_citation_quality_policy_snapshots(
            [_snapshot("commercial"), _snapshot("oss")]
        )
    with pytest.raises(ValueError, match="fixed-order"):
        merge_citation_quality_policy_snapshots([_snapshot("oss"), _snapshot("oss")])


def test_task_coverage_policy_merges_all_layers_additively() -> None:
    merged = merge_citation_quality_policy_snapshots(
        [
            _snapshot(
                "oss",
                config={
                    "task_coverage": {
                        "contract": {
                            "dimensions": ["entity", "period"],
                            "selectors": ["explicit"],
                        },
                        "remediation": {"allowed_actions": ["regenerate"]},
                    }
                },
            ),
            _snapshot(
                "commercial",
                config={
                    "task_coverage": {
                        "contract": {
                            "dimensions": ["connector-scope"],
                            "selectors": ["locked-resource"],
                        }
                    }
                },
            ),
            _snapshot(
                "distribution",
                config={
                    "task_coverage": {
                        "contract": {
                            "dimensions": ["financial-metric"],
                            "selectors": ["latest-published"],
                        }
                    }
                },
            ),
        ]
    )

    assert merged.config["task_coverage"]["contract"] == {
        "dimensions": ["entity", "period", "connector-scope", "financial-metric"],
        "selectors": ["explicit", "locked-resource", "latest-published"],
    }
    assert merged.config["task_coverage"]["remediation"]["allowed_actions"] == [
        "regenerate"
    ]


def test_policy_loader_accepts_task_coverage_identity_mapping(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  retrieval:
    identity_mappings:
      - id: company-identity
        tool_patterns: [\"*company_search*\"]
        query_fields: [query]
        result_fields: [symbol, ticker, name]
""",
        encoding="utf-8",
    )

    loaded = load_citation_policy_document(
        policy_path,
        expected_policy_id="test-policy",
        expected_layer="distribution",
        revision_prefix="test-policy-v",
    )

    mapping = loaded["task_coverage"]["retrieval"]["identity_mappings"][0]
    assert mapping["id"] == "company-identity"


def test_policy_loader_rejects_unknown_identity_mapping_key(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  retrieval:
    identity_mappings:
      - id: company-identity
        tool_patterns: [\"*company_search*\"]
        typo_fields: [symbol]
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="identity mapping requires"):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


def test_policy_loader_accepts_task_coverage_topic_ontology(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  contract:
    topic_ontology:
      revision: test-topics-v1
      topics:
        capital_expenditure:
          aliases:
            - capital expenditure
            - gigawatt of capacity
""",
        encoding="utf-8",
    )

    loaded = load_citation_policy_document(
        policy_path,
        expected_policy_id="test-policy",
        expected_layer="distribution",
        revision_prefix="test-policy-v",
    )

    topic = loaded["task_coverage"]["contract"]["topic_ontology"]["topics"][
        "capital_expenditure"
    ]
    assert topic["aliases"] == ["capital expenditure", "gigawatt of capacity"]


@pytest.mark.parametrize(
    "topic_ontology",
    [
        "[]",
        "{revision: test-topics-v1, topics: []}",
        "{revision: test-topics-v1, topics: {capital_expenditure: {aliases: []}}}",
        "{revision: test-topics-v1, topics: {capital_expenditure: {aliases: [capex], typo: true}}}",
    ],
)
def test_policy_loader_rejects_invalid_task_coverage_topic_ontology(
    tmp_path: Path,
    topic_ontology: str,
) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        f"""policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  contract:
    topic_ontology: {topic_ontology}
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="topic ontology"):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


def test_policy_loader_accepts_task_coverage_dimension_ontology(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  contract:
    dimension_ontology:
      revision: test-dimensions-v1
      dimensions:
        sales_channel:
          aliases: [channel, sales channel]
          members:
            direct:
              aliases: [direct, direct sales]
            wholesale:
              aliases: [wholesale]
""",
        encoding="utf-8",
    )

    loaded = load_citation_policy_document(
        policy_path,
        expected_policy_id="test-policy",
        expected_layer="distribution",
        revision_prefix="test-policy-v",
    )

    dimension = loaded["task_coverage"]["contract"]["dimension_ontology"][
        "dimensions"
    ]["sales_channel"]
    assert dimension["members"]["direct"]["aliases"] == ["direct", "direct sales"]


@pytest.mark.parametrize(
    "dimension_ontology",
    [
        "[]",
        "{revision: test-dimensions-v1, dimensions: []}",
        (
            "{revision: test-dimensions-v1, dimensions: "
            "{sales_channel: {aliases: [], members: {direct: {aliases: [direct]}}}}}"
        ),
        (
            "{revision: test-dimensions-v1, dimensions: "
            "{sales_channel: {aliases: [channel], members: {direct: {aliases: []}}}}}"
        ),
        (
            "{revision: test-dimensions-v1, dimensions: "
            "{sales_channel: {aliases: [channel], members: "
            "{direct: {aliases: [direct], typo: true}}}}}"
        ),
    ],
)
def test_policy_loader_rejects_invalid_task_coverage_dimension_ontology(
    tmp_path: Path,
    dimension_ontology: str,
) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        f"""policy_id: test-policy
layer: distribution
version: test-policy-v1
activation:
  default_mode: required-on-evidence
task_coverage:
  contract:
    dimension_ontology: {dimension_ontology}
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="dimension ontology"):
        load_citation_policy_document(
            policy_path,
            expected_policy_id="test-policy",
            expected_layer="distribution",
            revision_prefix="test-policy-v",
        )


async def test_registry_preserves_available_layers_when_commercial_fails() -> None:
    class _Provider:
        def __init__(self, snapshot: CitationQualityPolicySnapshot) -> None:
            self.snapshot = snapshot

        async def resolve(self, user_id: str, *, session_metadata: dict):
            return self.snapshot

    class _Unavailable:
        async def resolve(self, user_id: str, *, session_metadata: dict):
            raise RuntimeError("commercial service unavailable")

    registry = CitationQualityPolicyRegistry(oss_provider=_Provider(_snapshot("oss")))
    registry.register("commercial", _Unavailable())
    registry.register("distribution", _Provider(_snapshot("distribution", mode="strict-domain")))

    result = await registry.resolve("owner-1", session_metadata={})

    assert result.mode == "strict-domain"
    assert result.unavailable_layers == ("commercial",)
    assert [item["status"] for item in result.layers] == [
        "active",
        "unavailable",
        "active",
    ]


def test_registry_rejects_duplicate_overlay_registration() -> None:
    class _Provider:
        async def resolve(self, user_id: str, *, session_metadata: dict):
            return _snapshot("commercial")

    registry = CitationQualityPolicyRegistry()
    registry.register("commercial", _Provider())
    with pytest.raises(RuntimeError, match="already registered"):
        registry.register("commercial", _Provider())
