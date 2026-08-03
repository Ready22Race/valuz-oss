"""Deterministic claim-local citation repair protocol.

The hidden repair model never owns the full assistant answer. It may propose
bounded replacements for claim ids selected by CitationGuard; the host applies
those replacements to the sealed baseline and owns all evidence links.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

CITATION_CLAIM_PATCH_VERSION = "citation-claim-patch-v1"
_FENCED_JSON_RE = re.compile(r"\A\s*```(?:json)?\s*(.*?)\s*```\s*\Z", re.DOTALL)
_EVIDENCE_LINK_RE = re.compile(
    r"\[[^\]\n]*\]\((?:evidence|citation)://[^)\n]+\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationClaimPatch:
    claim_id: str
    replacement_text: str
    evidence_handles: tuple[str, ...]


@dataclass(frozen=True)
class ClaimPatchResult:
    accepted: bool
    text: str | None = None
    code: str | None = None


def apply_citation_claim_patch(
    *,
    baseline_text: str,
    baseline_bundle: Mapping[str, Any] | None,
    response_text: str,
    allowed_claim_ids: set[str],
    allowed_evidence_handles: set[str],
    required_fields_by_claim: Mapping[str, tuple[str, ...]] | None = None,
) -> ClaimPatchResult:
    """Parse, validate, and apply a claim patch to a sealed baseline."""

    parsed = _parse_patch_response(response_text)
    if isinstance(parsed, str):
        return ClaimPatchResult(accepted=False, code=parsed)
    if not parsed:
        return ClaimPatchResult(accepted=False, code="empty-patch")

    claim_locations = _claim_locations(baseline_bundle)
    replacements: list[tuple[int, int, str]] = []
    seen_claim_ids: set[str] = set()
    for patch in parsed:
        if patch.claim_id in seen_claim_ids:
            return ClaimPatchResult(accepted=False, code="duplicate-claim-id")
        seen_claim_ids.add(patch.claim_id)
        if patch.claim_id not in allowed_claim_ids:
            return ClaimPatchResult(accepted=False, code="claim-not-repairable")
        if any(handle not in allowed_evidence_handles for handle in patch.evidence_handles):
            return ClaimPatchResult(accepted=False, code="unknown-evidence-handle")
        location = claim_locations.get(patch.claim_id)
        if location is None:
            return ClaimPatchResult(accepted=False, code="claim-location-missing")
        start, end = location
        if start < 0 or end <= start or end > len(baseline_text):
            return ClaimPatchResult(accepted=False, code="claim-location-invalid")
        replacement = patch.replacement_text.strip()
        if not replacement or len(replacement) > 4_000:
            return ClaimPatchResult(accepted=False, code="replacement-invalid")
        if _EVIDENCE_LINK_RE.search(replacement):
            return ClaimPatchResult(accepted=False, code="model-owned-evidence-link")
        required_fields = (required_fields_by_claim or {}).get(patch.claim_id, ())
        folded_replacement = _fold(replacement)
        if any(_fold(field) not in folded_replacement for field in required_fields):
            return ClaimPatchResult(accepted=False, code="requested-field-not-preserved")
        links = " ".join(
            f"[{index}](evidence://{handle})"
            for index, handle in enumerate(patch.evidence_handles, start=1)
        )
        if links:
            replacement = f"{replacement} {links}"
        replacements.append((start, end, replacement))

    # Descending source offsets keep every untouched span byte-for-byte stable.
    patched = baseline_text
    last_start = len(baseline_text) + 1
    for start, end, replacement in sorted(replacements, reverse=True):
        if end > last_start:
            return ClaimPatchResult(accepted=False, code="overlapping-claim-patches")
        patched = patched[:start] + replacement + patched[end:]
        last_start = start
    if not patched.strip():
        return ClaimPatchResult(accepted=False, code="empty-result")
    return ClaimPatchResult(accepted=True, text=patched)


def repairable_claim_ids(
    bundle: Mapping[str, Any] | None,
    *,
    repairable_issue_codes: set[str],
) -> set[str]:
    quality = bundle.get("quality") if isinstance(bundle, Mapping) else None
    claims = quality.get("claims") if isinstance(quality, Mapping) else None
    result: set[str] = set()
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, Mapping) or claim.get("citationRequired") is not True:
            continue
        issue_codes = claim.get("issueCodes")
        if not isinstance(issue_codes, list) or not repairable_issue_codes.intersection(
            str(code) for code in issue_codes
        ):
            continue
        claim_id = claim.get("claimId")
        if isinstance(claim_id, str) and claim_id:
            result.add(claim_id)
    return result


def _parse_patch_response(response_text: str) -> list[CitationClaimPatch] | str:
    text = response_text.strip()
    fenced = _FENCED_JSON_RE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return "invalid-json"
    if not isinstance(payload, Mapping):
        return "invalid-patch-envelope"
    if payload.get("version") != CITATION_CLAIM_PATCH_VERSION:
        return "unsupported-patch-version"
    raw_patches = payload.get("patches")
    if not isinstance(raw_patches, list):
        return "invalid-patch-list"
    patches: list[CitationClaimPatch] = []
    for raw in raw_patches:
        if not isinstance(raw, Mapping):
            return "invalid-patch-item"
        claim_id = raw.get("claimId")
        replacement = raw.get("replacementText")
        handles = raw.get("evidenceHandles", [])
        if not isinstance(claim_id, str) or not isinstance(replacement, str):
            return "invalid-patch-item"
        if not isinstance(handles, list) or any(not isinstance(value, str) for value in handles):
            return "invalid-evidence-handles"
        patches.append(
            CitationClaimPatch(
                claim_id=claim_id,
                replacement_text=replacement,
                evidence_handles=tuple(dict.fromkeys(handles)),
            )
        )
    return patches


def _claim_locations(bundle: Mapping[str, Any] | None) -> dict[str, tuple[int, int]]:
    quality = bundle.get("quality") if isinstance(bundle, Mapping) else None
    claims = quality.get("claims") if isinstance(quality, Mapping) else None
    locations: dict[str, tuple[int, int]] = {}
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, Mapping):
            continue
        claim_id = claim.get("claimId")
        location = claim.get("location")
        if not isinstance(claim_id, str) or not isinstance(location, Mapping):
            continue
        start = location.get("sourceStart")
        end = location.get("sourceEnd")
        if isinstance(start, int) and isinstance(end, int):
            locations[claim_id] = (start, end)
    return locations


def _fold(value: str) -> str:
    return re.sub(r"[\s`*_：:()（）]", "", value).lower()
