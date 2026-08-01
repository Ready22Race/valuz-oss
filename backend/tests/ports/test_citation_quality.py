from __future__ import annotations

import pytest

from valuz_agent.ports.citation_quality import (
    CitationQualityPolicyRegistry,
    CitationQualityPolicySnapshot,
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
