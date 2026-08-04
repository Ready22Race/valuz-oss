#!/usr/bin/env python3
"""Run layered offline Task Coverage evaluation fixtures."""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
KERNEL_ROOT = BACKEND_ROOT / "kernel"
if str(KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(KERNEL_ROOT))

from src.core.task_coverage import (  # noqa: E402
    TASK_COVERAGE_RESOLVER_REVISION,
    TaskCoverageTracker,
    parse_task_contract,
    pending_task_clarification,
    resume_pending_task_clarification,
    task_coverage_tool_mapping,
)

DEFAULT_FIXTURE = BACKEND_ROOT / "tests/evaluation/fixtures/task_coverage_cases.json"
DEFAULT_POLICY = BACKEND_ROOT / "valuz_agent/resources/citation-policies/oss/policy.yaml"
_POLICY_LAYER_ORDER = {"oss": 0, "commercial": 1, "distribution": 2}
_OWNER_LAYER_ORDER = {
    "oss": 0,
    "commercial": 1,
    "distribution:team": 2,
    "distribution:finance": 2,
}
_OWNER_PREFIX = {
    "oss": "OSS-TASK-",
    "commercial": "COM-TASK-",
    "distribution:team": "TEAM-TASK-",
    "distribution:finance": "FIN-TASK-",
}
_GENERATED_FAMILIES = {
    "content-mappings",
    "dimension-ontology",
    "topic-ontology",
}


def _merge(base: Any, addition: Any) -> Any:
    if isinstance(base, dict) and isinstance(addition, dict):
        result = copy.deepcopy(base)
        for key, value in addition.items():
            result[key] = _merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(addition, list):
        result = copy.deepcopy(base)
        markers = {_stable(item) for item in result}
        for item in addition:
            marker = _stable(item)
            if marker not in markers:
                result.append(copy.deepcopy(item))
                markers.add(marker)
        return result
    if isinstance(base, bool) and isinstance(addition, bool):
        return base or addition
    return copy.deepcopy(addition)


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_policy_layers(
    paths: Sequence[Path],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    effective: dict[str, Any] = {}
    layers: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    previous_order = -1
    seen: set[str] = set()
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Task Coverage policy must be an object: {path}")
        layer = str(payload.get("layer") or "")
        order = _POLICY_LAYER_ORDER.get(layer)
        if order is None or order < previous_order or layer in seen:
            raise ValueError("Task Coverage policies must use unique fixed layer order")
        previous_order = order
        seen.add(layer)
        effective = _merge(effective, payload)
        deltas.append(copy.deepcopy(payload))
        layers.append(
            {
                "layer": layer,
                "policy_id": str(payload.get("policy_id") or ""),
                "revision": str(payload.get("version") or ""),
                "path": str(path),
            }
        )
    return effective, layers, deltas


def load_evaluation_payload(
    fixture_paths: Sequence[Path],
    policy_paths: Sequence[Path],
) -> dict[str, Any]:
    effective, policy_layers, policy_deltas = _load_policy_layers(policy_paths)
    cases: list[dict[str, Any]] = []
    fixture_layers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_owners: set[str] = set()
    previous_order = -1
    revisions: set[str] = set()
    declared_refs: set[str] = set()
    for index, path in enumerate(fixture_paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Task Coverage fixture must be an object: {path}")
        owner = str(payload.get("owner_layer") or "")
        order = _OWNER_LAYER_ORDER.get(owner)
        if order is None or order < previous_order or owner in seen_owners:
            raise ValueError("Task Coverage fixtures must use unique fixed owner order")
        previous_order = order
        seen_owners.add(owner)
        revision = str(payload.get("resolverRevision") or "")
        revisions.add(revision)
        expected_policy_layer = "distribution" if owner.startswith("distribution:") else owner
        if index >= len(policy_layers) or policy_layers[index]["layer"] != expected_policy_layer:
            raise ValueError(f"Task Coverage fixture {owner} has no matching Policy layer")
        families = payload.get("generated_case_families") or []
        if not isinstance(families, list) or any(
            not isinstance(family, str) or family not in _GENERATED_FAMILIES for family in families
        ):
            raise ValueError(f"Task Coverage generated families are invalid: {path}")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"Task Coverage fixture cases must be a list: {path}")
        prefix = _OWNER_PREFIX[owner]
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise ValueError(f"Task Coverage case must be an object: {path}")
            case_id = str(raw_case.get("task_coverage_case_id") or "")
            if not case_id.startswith(prefix) or case_id in seen_ids:
                raise ValueError(f"Invalid or duplicate Task Coverage case id: {case_id}")
            seen_ids.add(case_id)
            case = copy.deepcopy(raw_case)
            case["owner_layer"] = owner
            case["source_fixture"] = str(path)
            refs = case.get("policy_refs") or []
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValueError(f"Task Coverage case policy_refs are invalid: {case_id}")
            declared_refs.update(refs)
            cases.append(case)
        fixture_layers.append(
            {
                "owner_layer": owner,
                "version": payload.get("version"),
                "path": str(path),
                "declared_case_count": len(raw_cases),
                "generated_families": list(families),
            }
        )
    if revisions != {TASK_COVERAGE_RESOLVER_REVISION}:
        raise ValueError("All fixtures must target the current Task Coverage Resolver")
    unknown_refs = sorted(ref for ref in declared_refs if not _policy_ref_exists(effective, ref))
    if unknown_refs:
        raise ValueError("Unknown Task Coverage policy refs: " + ", ".join(unknown_refs))
    generated = _evaluate_content_mapping_families(
        fixture_layers,
        policy_deltas,
        effective,
    )
    generated.extend(
        _evaluate_topic_ontology_families(
            fixture_layers,
            policy_deltas,
            effective,
        )
    )
    generated.extend(
        _evaluate_dimension_ontology_families(
            fixture_layers,
            policy_deltas,
            effective,
        )
    )
    return {
        "version": 1,
        "resolverRevision": TASK_COVERAGE_RESOLVER_REVISION,
        "effectivePolicy": effective,
        "policyLayers": policy_layers,
        "fixtureLayers": fixture_layers,
        "cases": cases,
        "generatedChecks": generated,
        "declaredPolicyRefs": sorted(declared_refs),
    }


def _policy_ref_exists(policy: Mapping[str, Any], ref: str) -> bool:
    current: Any = policy
    parts = ref.split(".")
    index = 0
    while index < len(parts):
        part = parts[index]
        if isinstance(current, Mapping):
            if part not in current:
                return False
            current = current[part]
            index += 1
        elif isinstance(current, list):
            remaining = ".".join(parts[index:])
            if remaining in {str(item) for item in current if not isinstance(item, Mapping)}:
                return True
            selected = next(
                (
                    item
                    for item in current
                    if isinstance(item, Mapping)
                    and str(item.get("id") or item.get("name") or "") == part
                ),
                None,
            )
            if selected is None:
                return False
            current = selected
            index += 1
        else:
            return False
    return True


def _probe_tool_name(pattern: str) -> str:
    value = pattern.replace("*", "task_coverage_probe")
    value = value.replace("?", "x")
    return value or "task_coverage_probe"


def _evaluate_content_mapping_families(
    fixture_layers: Sequence[Mapping[str, Any]],
    policy_deltas: Sequence[Mapping[str, Any]],
    effective_policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, layer in enumerate(fixture_layers):
        if "content-mappings" not in layer.get("generated_families", []):
            continue
        task = policy_deltas[index].get("task_coverage")
        retrieval = task.get("retrieval") if isinstance(task, Mapping) else None
        mappings = retrieval.get("content_mappings") if isinstance(retrieval, Mapping) else None
        for mapping in mappings if isinstance(mappings, list) else []:
            if not isinstance(mapping, Mapping):
                continue
            patterns = mapping.get("tool_patterns")
            pattern = str(patterns[0]) if isinstance(patterns, list) and patterns else ""
            tool_name = _probe_tool_name(pattern)
            actual_role, actual_source = task_coverage_tool_mapping(
                tool_name,
                policy_snapshot=effective_policy,
            )
            expected_role = str(mapping.get("role"))
            expected_source = str(mapping.get("coverage_text") or "result")
            checks.append(
                {
                    "mapping_id": str(mapping.get("id") or ""),
                    "owner_layer": layer["owner_layer"],
                    "tool_name": tool_name,
                    "pattern_matched": fnmatch.fnmatchcase(
                        tool_name.casefold(), pattern.casefold()
                    ),
                    "expected": [expected_role, expected_source],
                    "actual": [actual_role, actual_source],
                    "passed": actual_role == expected_role and actual_source == expected_source,
                }
            )
    return checks


def _evaluate_topic_ontology_families(
    fixture_layers: Sequence[Mapping[str, Any]],
    policy_deltas: Sequence[Mapping[str, Any]],
    effective_policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand every layered topic definition through the shared parser."""

    checks: list[dict[str, Any]] = []
    for index, layer in enumerate(fixture_layers):
        if "topic-ontology" not in layer.get("generated_families", []):
            continue
        task = policy_deltas[index].get("task_coverage")
        contract = task.get("contract") if isinstance(task, Mapping) else None
        topic_ontology = contract.get("topic_ontology") if isinstance(contract, Mapping) else None
        topics = topic_ontology.get("topics") if isinstance(topic_ontology, Mapping) else None
        for topic_id, definition in topics.items() if isinstance(topics, Mapping) else []:
            aliases = definition.get("aliases") if isinstance(definition, Mapping) else None
            expected_aliases = [str(alias) for alias in aliases or [] if str(alias).strip()]
            if not expected_aliases:
                checks.append(
                    {
                        "mapping_id": f"topic:{topic_id}",
                        "owner_layer": layer["owner_layer"],
                        "expected": ["non-empty aliases"],
                        "actual": [],
                        "passed": False,
                    }
                )
                continue
            contract = parse_task_contract(
                f"总结测试公司最近一个季度电话会中管理层对{expected_aliases[0]}的表述，"
                "并按季度引用。",
                policy_snapshot={"revision": "task-effective-eval", "config": effective_policy},
            )
            requirements = [item for item in contract.requirements if item.kind == "topic"]
            actual_aliases = (
                set(requirements[0].aliases.get("topic", ())) if requirements else set()
            )
            expected = {str(topic_id), *expected_aliases}
            checks.append(
                {
                    "mapping_id": f"topic:{topic_id}",
                    "owner_layer": layer["owner_layer"],
                    "expected": sorted(expected),
                    "actual": sorted(actual_aliases),
                    "passed": len(requirements) == 1 and expected.issubset(actual_aliases),
                }
            )
    return checks


def _probe_metric(effective_policy: Mapping[str, Any]) -> tuple[str, str]:
    """Choose one declared metric for ontology parser probes.

    Dimension checks exercise the real Task Contract parser rather than a
    private helper. A structured requirement therefore needs a metric. Prefer
    operating revenue when the distribution defines it, then fall back to the
    first metric with a non-empty alias.
    """

    semantics = effective_policy.get("semantics")
    metric_ontology = semantics.get("metric_ontology") if isinstance(semantics, Mapping) else None
    metrics = metric_ontology.get("metrics") if isinstance(metric_ontology, Mapping) else None
    if not isinstance(metrics, Mapping):
        return "", ""
    ordered_ids = ["operating_revenue", *metrics]
    for metric_id in dict.fromkeys(str(item) for item in ordered_ids):
        definition = metrics.get(metric_id)
        aliases = definition.get("aliases") if isinstance(definition, Mapping) else None
        values = [str(alias) for alias in aliases or [] if str(alias).strip()]
        if values:
            return metric_id, values[0]
    return "", ""


def _evaluate_dimension_ontology_families(
    fixture_layers: Sequence[Mapping[str, Any]],
    policy_deltas: Sequence[Mapping[str, Any]],
    effective_policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand every layered categorical member through the shared parser."""

    checks: list[dict[str, Any]] = []
    metric_id, metric_alias = _probe_metric(effective_policy)
    for index, layer in enumerate(fixture_layers):
        if "dimension-ontology" not in layer.get("generated_families", []):
            continue
        task = policy_deltas[index].get("task_coverage")
        contract = task.get("contract") if isinstance(task, Mapping) else None
        ontology = contract.get("dimension_ontology") if isinstance(contract, Mapping) else None
        dimensions = ontology.get("dimensions") if isinstance(ontology, Mapping) else None
        for dimension_id, definition in (
            dimensions.items() if isinstance(dimensions, Mapping) else []
        ):
            dimension_aliases = (
                definition.get("aliases") if isinstance(definition, Mapping) else None
            )
            dimension_values = [
                str(alias) for alias in dimension_aliases or [] if str(alias).strip()
            ]
            members = definition.get("members") if isinstance(definition, Mapping) else None
            for member_id, member_definition in (
                members.items() if isinstance(members, Mapping) else []
            ):
                member_aliases = (
                    member_definition.get("aliases")
                    if isinstance(member_definition, Mapping)
                    else member_definition
                )
                member_values = [str(alias) for alias in member_aliases or [] if str(alias).strip()]
                expected = {str(member_id), *member_values}
                policy_ref = (
                    "task_coverage.contract.dimension_ontology.dimensions."
                    f"{dimension_id}.members.{member_id}"
                )
                if not dimension_values or not member_values or not metric_alias:
                    missing = []
                    if not dimension_values:
                        missing.append("dimension aliases")
                    if not member_values:
                        missing.append("member aliases")
                    if not metric_alias:
                        missing.append("probe metric")
                    checks.append(
                        {
                            "mapping_id": f"dimension:{dimension_id}:{member_id}",
                            "owner_layer": layer["owner_layer"],
                            "expected": sorted(expected),
                            "actual": [],
                            "missing": missing,
                            "passed": False,
                        }
                    )
                    continue
                task_contract = parse_task_contract(
                    "只列出测试公司2024年报中"
                    f"{dimension_values[0]}为{member_values[0]}的{metric_alias}。",
                    policy_snapshot={
                        "revision": "task-effective-eval",
                        "config": effective_policy,
                    },
                )
                requirements = [
                    item
                    for item in task_contract.requirements
                    if item.kind == "structured-slot"
                    and item.slots.get("metric") == metric_id
                    and item.slots.get("dimensions", {}).get(str(dimension_id)) == str(member_id)
                ]
                actual_aliases = (
                    set(requirements[0].aliases.get(f"dimension:{dimension_id}", ()))
                    if requirements
                    else set()
                )
                actual_refs = set(requirements[0].policy_refs) if requirements else set()
                checks.append(
                    {
                        "mapping_id": f"dimension:{dimension_id}:{member_id}",
                        "owner_layer": layer["owner_layer"],
                        "expected": sorted(expected),
                        "actual": sorted(actual_aliases),
                        "passed": (
                            len(requirements) == 1
                            and expected.issubset(actual_aliases)
                            and policy_ref in actual_refs
                        ),
                    }
                )
    return checks


def evaluate_case(case: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    prompt = str(case.get("prompt") or "")
    policy_snapshot = {
        "revision": "task-effective-eval",
        "config": dict(policy),
    }
    contract_prompt = prompt
    clarification_continuation: bool | None = None
    clarification_resolved: bool | None = None
    context_inputs_resolved = False
    clarification_follow_up = str(case.get("clarification_follow_up") or "").strip()
    if clarification_follow_up:
        initial_contract = parse_task_contract(
            prompt,
            policy_snapshot=policy_snapshot,
            document_ids=case.get("document_ids") or (),
        )
        pending = pending_task_clarification(initial_contract, prompt)
        resolution = resume_pending_task_clarification(
            pending or {},
            clarification_follow_up,
        )
        clarification_continuation = resolution.continuation
        clarification_resolved = resolution.resolved
        contract_prompt = resolution.effective_prompt
        context_inputs_resolved = resolution.resolved
    contract = parse_task_contract(
        contract_prompt,
        policy_snapshot=policy_snapshot,
        document_ids=case.get("document_ids") or (),
        context_inputs_resolved=context_inputs_resolved,
    )
    tracker = TaskCoverageTracker(
        contract,
        policy_snapshot={"config": dict(policy)},
    )
    for tool in case.get("tools") or []:
        tracker.record_tool_result(
            tool.get("name"),
            tool.get("input"),
            tool.get("result"),
            citation_ready=(bool(tool.get("citation_ready")) if "citation_ready" in tool else None),
            citation_join_id=(
                str(tool.get("citation_join_id")) if tool.get("citation_join_id") else None
            ),
        )
    audit = tracker.evaluate(str(case.get("answer") or ""))
    failures: list[str] = []
    expected_contract = case.get("expected_contract") or {}
    kind_counts = Counter(item.kind for item in contract.requirements)
    actual_contract = {
        "task_type": contract.task_type,
        "requirement_count": len(contract.requirements),
        "required_count": len(contract.required_requirements),
        "kind_counts": dict(kind_counts),
        "entities": contract.declared_scope.get("entities", []),
        "topics": contract.declared_scope.get("topics", []),
        "metrics": list(
            dict.fromkeys(
                str(item.slots.get("metric"))
                for item in contract.requirements
                if item.kind == "structured-slot" and item.slots.get("metric")
            )
        ),
        "context_required_inputs": contract.declared_scope.get(
            "contextRequiredInputs", []
        ),
        "enforceable": contract.enforceable,
        "ambiguous": contract.ambiguous,
        **(
            {
                "clarification_continuation": clarification_continuation,
                "clarification_resolved": clarification_resolved,
            }
            if clarification_follow_up
            else {}
        ),
    }
    _compare_expected("contract", expected_contract, actual_contract, failures)
    metrics = audit["metrics"]
    expected_coverage = case.get("expected_coverage") or {}
    missing_result_items = [
        str(item)
        for row in audit["requirements"]
        if isinstance(row, Mapping)
        for item in (
            row.get("missingResultItems") if isinstance(row.get("missingResultItems"), list) else []
        )
    ]
    revision_requested = tracker.should_request_revision(audit)
    result_item_retrieval_count = 0
    if revision_requested and missing_result_items:
        revision_prompt = tracker.revision_prompt(
            audit,
            str(case.get("answer") or ""),
            contract_prompt,
        )
        marker = "Restricted task-coverage context (JSON):\n"
        if marker in revision_prompt:
            revision_context = json.loads(revision_prompt.split(marker, 1)[1])
            retrieval_rows = revision_context.get("resultItemRetrieval")
            if isinstance(retrieval_rows, list):
                result_item_retrieval_count = len(retrieval_rows)
    actual_coverage = {
        "status": audit["status"],
        "answer_missing_count": metrics["answerRequirementMissingCount"],
        "answer_fulfilled_count": metrics["answerRequirementFulfilledCount"],
        "retrieval_available_count": metrics["retrievalRequirementAvailableCount"],
        "model_input_visible_count": metrics["modelInputRequirementVisibleCount"],
        "revision_requested": revision_requested,
        "missing_result_items": missing_result_items,
        "result_item_retrieval_count": result_item_retrieval_count,
    }
    _compare_expected("coverage", expected_coverage, actual_coverage, failures)
    expected_plan = case.get("expected_retrieval_plan") or {}
    strategy_counts = Counter(step.strategy for step in tracker.retrieval_plan.steps)
    actual_plan = {
        "step_count": len(tracker.retrieval_plan.steps),
        "strategy_counts": dict(strategy_counts),
        "max_attempts": sorted({step.max_attempts for step in tracker.retrieval_plan.steps}),
        "entity_step_count": sum(bool(step.entity_ids) for step in tracker.retrieval_plan.steps),
        "period_step_count": sum(bool(step.periods) for step in tracker.retrieval_plan.steps),
        "planner_revision": tracker.retrieval_plan.planner_revision,
    }
    _compare_expected("retrieval_plan", expected_plan, actual_plan, failures)
    for description in case.get("expected_missing_descriptions") or []:
        if not any(
            description in str(row.get("description")) and row.get("answerStatus") != "fulfilled"
            for row in audit["requirements"]
        ):
            failures.append(f"missing requirement not detected: {description}")
    for description in case.get("expected_available_descriptions") or []:
        if not any(
            description in str(row.get("description")) and row.get("retrievalStatus") == "available"
            for row in audit["requirements"]
        ):
            failures.append(f"available retrieval not detected: {description}")
    return {
        "task_coverage_case_id": case["task_coverage_case_id"],
        "owner_layer": case["owner_layer"],
        "business_group": case.get("business_group"),
        "passed": not failures,
        "failures": failures,
        "contract": actual_contract,
        "coverage": actual_coverage,
        "retrieval_plan": actual_plan,
        "audit": audit,
        "policy_refs": case.get("policy_refs") or [],
    }


def _compare_expected(
    label: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    failures: list[str],
) -> None:
    for key, value in expected.items():
        if actual.get(key) != value:
            failures.append(f"{label}.{key}: expected {value!r}, got {actual.get(key)!r}")


def run(payload: Mapping[str, Any]) -> dict[str, Any]:
    results = [evaluate_case(case, payload["effectivePolicy"]) for case in payload["cases"]]
    generated = payload["generatedChecks"]
    failed = [case for case in results if not case["passed"]]
    failed_generated = [check for check in generated if not check["passed"]]
    by_layer: dict[str, dict[str, int]] = {}
    for case in results:
        stats = by_layer.setdefault(case["owner_layer"], {"case_count": 0, "failed": 0})
        stats["case_count"] += 1
        stats["failed"] += not case["passed"]
    return {
        "version": 1,
        "resolverRevision": payload["resolverRevision"],
        "metrics": {
            "case_count": len(results),
            "failed_case_count": len(failed),
            "generated_check_count": len(generated),
            "failed_generated_check_count": len(failed_generated),
        },
        "layerMetrics": by_layer,
        "policyLayers": payload["policyLayers"],
        "fixtureLayers": payload["fixtureLayers"],
        "declaredPolicyRefs": payload["declaredPolicyRefs"],
        "generatedChecks": generated,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="append", type=Path)
    parser.add_argument("--policy", action="append", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--allow-failures", action="store_true")
    args = parser.parse_args()
    payload = load_evaluation_payload(
        args.fixture or [DEFAULT_FIXTURE],
        args.policy or [DEFAULT_POLICY],
    )
    result = run(payload)
    metrics = result["metrics"]
    print(
        "Task Coverage [effective] "
        f"cases={metrics['case_count']} failed={metrics['failed_case_count']} "
        f"generated={metrics['generated_check_count']} "
        f"generated_failed={metrics['failed_generated_check_count']}"
    )
    for case in result["cases"]:
        if not case["passed"]:
            print(f"FAIL {case['task_coverage_case_id']}: {'; '.join(case['failures'])}")
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    failed = metrics["failed_case_count"] + metrics["failed_generated_check_count"]
    return 0 if args.allow_failures or failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
