"""Runtime-neutral citation evidence registry and final-answer guard.

Tools are the trust boundary for citations.  A source-bearing tool may attach
one or more ``_valuz_evidence`` envelopes to its JSON result.  The model sees
only an opaque ``evidenceHandle`` and may bind a claim with a Markdown link to
``evidence://<handle>``.  Before the final assistant event is persisted or
broadcast, :class:`CitationGuard` replaces those temporary links with
``citation://<citationId>`` and builds the canonical ``CitationBundleV1`` from
the registered tool envelopes.

The model never gets to author source metadata, quotes, document ids or
locators.  Unknown handles are unlinked and reported through integrity
metadata instead of being promoted into citations.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from src.core.citation_quality import evaluate_citation_quality
from src.core.claim_audit import auto_bind_unique_claims

POLICY_REVISION = "citation-v1"
EVIDENCE_ENVELOPE_KEY = "_valuz_evidence"

_HANDLE_RE = re.compile(r"^ev_[A-Za-z0-9_-]{8,128}$")
_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\n]{0,240})\]\((evidence|citation)://([A-Za-z0-9_-]{1,160})\)"
)
_BARE_EVIDENCE_RE = re.compile(r"(?<![\w/])evidence://([A-Za-z0-9_-]{1,160})")
_REPAIR_MARKER_RE = re.compile(
    r"(?:\[\[evidence:([A-Za-z0-9_-]{1,160})\]\]|"
    r"<evidence:([A-Za-z0-9_-]{1,160})>)"
)
_NUMBERED_EVIDENCE_SOURCE_RE = re.compile(
    r"(?m)^[ \t]*(?:[-*][ \t]+)?\[(\d{1,3})\][ \t]+"
    r"\[[^\]\n]{1,240}\]\(evidence://([A-Za-z0-9_-]{1,160})\)"
)
_BARE_NUMBERED_MARKER_RE = re.compile(r"(?<![\\\w])\[(\d{1,3})\](?!\()")
_SOURCE_SECTION_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?[ \t]*"
    r"(?:sources?|references?|citations?|来源|参考来源|引用来源|参考资料)"
    r"[ \t]*[:：]?[ \t]*(?:\*\*|__)?[ \t]*$"
)
_CANONICAL_CITATION_URI_RE = re.compile(r"citation://([A-Za-z0-9_-]{1,160})")
_MARKDOWN_DESTINATION_RE = re.compile(r"\]\(([^)\n]+)\)")
_EXPLICIT_CITATION_RE = re.compile(
    r"(?:引用|引文|出处|来源|根据.{0,12}(?:文档|资料)|核验|"
    r"总结.{0,12}(?:文档|文件)|citation|citations|cite|source(?:s)?\b|"
    r"according to (?:the )?(?:document|file|report))",
    re.IGNORECASE,
)

_SOURCE_TYPES = {"document", "web", "dataset", "tool-result", "conversation"}
_EVIDENCE_KINDS = {"text", "structured-data", "calculation"}
_LOCATOR_KINDS = {"chunk", "html", "pdf", "external"}
_MAX_REGISTRY_RECORDS = 2_000
_MAX_SOURCE_ID_CHARS = 512
_MAX_SOURCE_TEXT_CHARS = 1_024
_MAX_URL_CHARS = 4_096
_MAX_QUOTE_CHARS = 32_000
_MAX_SNIPPET_CHARS = 4_000
_MAX_CONTEXT_CHARS = 512
_MAX_STRUCTURED_STRING_CHARS = 4_096
_MAX_CALCULATION_INPUTS = 128
_MAX_RECTS = 128
_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}


@dataclass(frozen=True)
class EvidenceRecord:
    """Validated immutable snapshot accepted from a tool result."""

    handle: str
    source: dict[str, Any]
    evidence: dict[str, Any]
    locator: dict[str, Any] | None
    tool_name: str | None


@dataclass(frozen=True)
class GuardResult:
    """Canonical final body and optional citation sidecar."""

    text: str
    bundle: dict[str, Any] | None


class EvidenceRegistry:
    """Collect validated evidence envelopes from this turn's tool results."""

    _MAX_TOOL_RESULT_CHARS = 2_000_000
    # Structured financial tools may return several hundred exact per-field
    # envelopes in one response.  Keep a hard bound, but do not truncate a
    # normal eight-period financial statement before its cited field.
    _MAX_VISITED_NODES = 20_000
    _MAX_DEPTH = 12

    def __init__(
        self,
        *,
        allowed_document_ids: set[str] | None = None,
    ) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._rejected_count = 0
        self._overflow_reasons: set[str] = set()
        self._allowed_document_ids = (
            {str(item) for item in allowed_document_ids if str(item)}
            if allowed_document_ids is not None
            else None
        )

    def register_tool_result(self, content: Any, *, tool_name: str | None = None) -> int:
        """Register every valid envelope nested inside ``content``.

        MCP/SDK runtimes usually surface tool output as a JSON string, while
        in-process runtimes may surface a dict or list.  Invalid or oversized
        payloads are ignored: citation collection must never break the turn.
        Returns the number of newly registered handles.
        """

        payload = _decode_json_payload(content, max_chars=self._MAX_TOOL_RESULT_CHARS)
        if payload is None:
            if _contains_evidence_marker(content):
                self._rejected_count += 1
                self._overflow_reasons.add("tool_result_invalid_or_oversized")
            return 0

        before = len(self._records)
        visited = 0
        stack: list[tuple[Any, int]] = [(payload, 0)]
        while stack and visited < self._MAX_VISITED_NODES:
            node, depth = stack.pop()
            visited += 1
            if depth > self._MAX_DEPTH:
                self._rejected_count += 1
                self._overflow_reasons.add("max_depth")
                continue
            if isinstance(node, dict):
                envelope = node.get(EVIDENCE_ENVELOPE_KEY)
                for candidate in _as_envelope_items(envelope):
                    if len(self._records) >= _MAX_REGISTRY_RECORDS:
                        self._rejected_count += 1
                        self._overflow_reasons.add("max_records")
                        break
                    record = _validate_evidence_item(candidate, tool_name=tool_name)
                    if record is None:
                        self._rejected_count += 1
                    elif self._source_is_allowed(record.source):
                        # First writer wins.  A later tool result cannot replace
                        # the evidence snapshot bound to an already-seen handle.
                        self._records.setdefault(record.handle, record)
                    else:
                        self._rejected_count += 1
                stack.extend((value, depth + 1) for value in node.values())
            elif isinstance(node, list):
                stack.extend((value, depth + 1) for value in node)
            elif isinstance(node, str):
                # MCP SDKs commonly wrap the actual JSON result in a text
                # content block (`{"content":[{"type":"text","text":"{...}"}]}`).
                # Decode those nested blocks as well as top-level JSON strings;
                # otherwise Codex/Claude would display the evidence handles to
                # the model while the guard silently missed the registry entry.
                nested = _decode_json_payload(
                    node,
                    max_chars=self._MAX_TOOL_RESULT_CHARS,
                )
                if nested is not None:
                    stack.append((nested, depth + 1))
        if stack:
            self._rejected_count += 1
            self._overflow_reasons.add("max_visited_nodes")
        return len(self._records) - before

    def get(self, handle: str) -> EvidenceRecord | None:
        return self._records.get(handle)

    def values(self) -> Iterable[EvidenceRecord]:
        return self._records.values()

    def __len__(self) -> int:
        return len(self._records)

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def overflow_reasons(self) -> tuple[str, ...]:
        return tuple(sorted(self._overflow_reasons))

    @property
    def had_evidence_activity(self) -> bool:
        return bool(self._records) or self._rejected_count > 0

    def _source_is_allowed(self, source: dict[str, Any]) -> bool:
        """Fail closed for a document-research session's locked source scope."""

        if self._allowed_document_ids is None:
            return True
        document_id = source.get("documentId")
        return (
            source.get("sourceType") == "document"
            and isinstance(document_id, str)
            and document_id in self._allowed_document_ids
        )


class CitationGuard:
    """Bind a final assistant body to evidence registered during this turn."""

    def __init__(
        self,
        registry: EvidenceRegistry,
        *,
        message_id: str,
        user_prompt: str,
        policy_available: bool,
        quality_policy: dict[str, Any] | None = None,
        force_required: bool = False,
    ) -> None:
        self._registry = registry
        self._message_id = message_id
        self._explicitly_requested = bool(_EXPLICIT_CITATION_RE.search(user_prompt or ""))
        self._policy_available = policy_available
        self._quality_policy = quality_policy
        self._force_required = force_required

    @property
    def requires_citation(self) -> bool:
        """Whether the current turn must be sealed before text is published.

        The registry can become non-empty after construction, so this is a
        property rather than a cached flag.  Callers use it to keep candidate
        answer deltas provisional once source-bearing evidence is available.
        """

        return (
            self._force_required
            or self._registry.had_evidence_activity
            or self._explicitly_requested
        )

    def finalize(self, text: str, *, repair_attempts: int = 0) -> GuardResult:
        """Return a safe canonical body and its ``CitationBundleV1``.

        Expected ``evidence://`` links are normal protocol binding and do not
        count as a repair.  The single deterministic repair pass accepts the
        documented fallback markers ``[[evidence:HANDLE]]`` and
        ``<evidence:HANDLE>``.  Unknown evidence/citation ids are converted to
        plain labels so the client can never resolve them as trusted sources.
        """

        required = self.requires_citation
        if not required and "evidence://" not in text and "citation://" not in text:
            return GuardResult(text=text, bundle=None)

        repaired_text, repaired_handles = self._repair_markers(text)
        repair_attempts = max(
            1 if repaired_handles else 0,
            min(max(int(repair_attempts), 0), 1),
        )
        policy_mode = (
            self._quality_policy.get("mode")
            if isinstance(self._quality_policy, dict)
            else "required-on-evidence"
        )
        policy_config = (
            self._quality_policy.get("config") if isinstance(self._quality_policy, dict) else None
        )
        policy_config = policy_config if isinstance(policy_config, dict) else {}
        semantics = policy_config.get("semantics")
        semantics = semantics if isinstance(semantics, dict) else None
        auto_bind_result = auto_bind_unique_claims(
            repaired_text,
            self._registry.values(),
            mode=str(policy_mode or "required-on-evidence"),
            semantics=semantics,
        )
        repaired_text = auto_bind_result.text
        auto_bound_claims_by_handle: dict[str, list[str]] = {}
        for claim_id, handle in auto_bind_result.claim_handles.items():
            auto_bound_claims_by_handle.setdefault(handle, []).append(claim_id)

        citations: list[dict[str, Any]] = []
        cited_handles: list[str] = []
        unknown_ids: list[str] = []
        missing_locator_ids: list[str] = []
        canonical_to_handle = {
            self._citation_id(record.handle): record.handle for record in self._registry.values()
        }

        def append_handle(identifier: str) -> str | None:
            record = self._registry.get(identifier)
            if record is None:
                handle = canonical_to_handle.get(identifier)
                record = self._registry.get(handle) if handle else None
                identifier = handle or identifier
            if record is None:
                _append_unique(unknown_ids, identifier)
                return None
            citation_id = self._citation_id(identifier)
            if identifier in cited_handles:
                return citation_id
            # Mark before traversing calculation inputs so a malformed cyclic
            # envelope cannot recurse forever.
            cited_handles.append(identifier)
            evidence = copy.deepcopy(record.evidence)
            if evidence.get("kind") == "calculation":
                for item in evidence.get("inputs", []):
                    if not isinstance(item, dict):
                        continue
                    input_ref = item.get("citationId")
                    if not isinstance(input_ref, str):
                        continue
                    canonical_input = append_handle(input_ref)
                    if canonical_input is not None:
                        item["citationId"] = canonical_input
            citation = {
                "citationId": citation_id,
                "source": copy.deepcopy(record.source),
                "evidence": evidence,
                "resolutionStatus": "ready",
            }
            annotations: dict[str, Any] = {}
            if record.tool_name:
                annotations["provenance"] = {"toolName": record.tool_name}
            auto_bound_claim_ids = auto_bound_claims_by_handle.get(identifier)
            if auto_bound_claim_ids:
                annotations["binding"] = {
                    "autoBoundClaimIds": list(auto_bound_claim_ids),
                }
            if annotations:
                citation["annotations"] = annotations
            if record.locator is not None:
                citation["locator"] = copy.deepcopy(record.locator)
            elif record.source.get("sourceType") == "document":
                citation["resolutionStatus"] = "degraded"
                missing_locator_ids.append(citation_id)
            citations.append(citation)
            return citation_id

        def replace_link(match: re.Match[str]) -> str:
            label, scheme, identifier = match.groups()
            if scheme == "citation":
                # The model cannot mint canonical ids.  Even if it guessed an
                # id that would hash to a registered handle, only handles in a
                # tool envelope are accepted at this boundary.
                _append_unique(unknown_ids, identifier)
                return label or "source"
            citation_id = append_handle(identifier)
            if citation_id is None:
                return label or "source"
            return f"[{label or 'source'}](citation://{citation_id})"

        numbered_bindings = _numbered_evidence_bindings(repaired_text)
        canonical_text = _MARKDOWN_LINK_RE.sub(replace_link, repaired_text)

        def replace_bare(match: re.Match[str]) -> str:
            handle = match.group(1)
            citation_id = append_handle(handle)
            if citation_id is None:
                return "source"
            # Bare handles are not valid final prose, but the deterministic
            # repair can safely wrap a known handle without inventing evidence.
            nonlocal repair_attempts
            repair_attempts = 1
            return f"[source](citation://{citation_id})"

        canonical_text = _BARE_EVIDENCE_RE.sub(replace_bare, canonical_text)

        # Models occasionally render the requested visual form (``[1]``)
        # beside claims but put the trusted evidence link only in a numbered
        # source list. Bind those claim markers deterministically when, and
        # only when, that same answer contains one unambiguous
        # ``[n] [label](evidence://HANDLE)`` entry. The source-list marker
        # itself stays plain because the following canonical link is already
        # interactive.
        linked_text = canonical_text

        def replace_bare_number(match: re.Match[str]) -> str:
            handle = numbered_bindings.get(match.group(1))
            if handle is None:
                return match.group(0)
            citation_id = append_handle(handle)
            if citation_id is None:
                return match.group(0)
            following = linked_text[match.end() :]
            source_link = re.match(
                r"[ \t]+\[[^\]\n]{1,240}\]\(citation://" + re.escape(citation_id) + r"\)",
                following,
            )
            if source_link is not None:
                return match.group(0)
            nonlocal repair_attempts
            repair_attempts = 1
            return f"[{match.group(1)}](citation://{citation_id})"

        canonical_text = _BARE_NUMBERED_MARKER_RE.sub(
            replace_bare_number,
            linked_text,
        )
        canonical_text = _strip_redundant_source_section(canonical_text)

        all_citation_ids = [self._citation_id(record.handle) for record in self._registry.values()]
        used_citation_ids = {self._citation_id(handle) for handle in cited_handles}
        unused_ids = [item for item in all_citation_ids if item not in used_citation_ids]

        degraded = (
            bool(unknown_ids)
            or bool(missing_locator_ids)
            or self._registry.rejected_count > 0
            or (required and not citations)
            or (required and not self._policy_available)
        )
        if degraded:
            status = "degraded"
            # A missing/unknown binding consumes the one guard repair budget,
            # even when there is nothing safe to repair.
            repair_attempts = 1
        elif repair_attempts:
            status = "repaired"
        elif required:
            status = "passed"
        else:
            status = "not-required"

        bundle = {
            "version": 1,
            "citations": citations,
            "integrity": {
                "status": status,
                "unknownCitationIds": unknown_ids,
                "unusedCitationIds": unused_ids,
                "missingLocatorCitationIds": missing_locator_ids,
                "repairAttempts": repair_attempts,
                "policyRevision": POLICY_REVISION,
                "evidenceRegisteredCount": len(self._registry),
                "evidenceRejectedCount": self._registry.rejected_count,
                "evidenceOverflowReasons": list(self._registry.overflow_reasons),
            },
        }
        bundle = evaluate_citation_quality(
            canonical_text,
            bundle,
            self._quality_policy,
            available_evidence=self._registry.values(),
        )
        return GuardResult(text=canonical_text, bundle=bundle)

    def _repair_markers(self, text: str) -> tuple[str, list[str]]:
        repaired: list[str] = []

        def replace(match: re.Match[str]) -> str:
            handle = match.group(1) or match.group(2)
            repaired.append(handle)
            return f"[source](evidence://{handle})"

        return _REPAIR_MARKER_RE.sub(replace, text), repaired

    def _citation_id(self, handle: str) -> str:
        digest = hashlib.sha256(f"{self._message_id}\0{handle}".encode()).hexdigest()[:20]
        return f"cit_{digest}"


def _numbered_evidence_bindings(text: str) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for label, handle in _NUMBERED_EVIDENCE_SOURCE_RE.findall(text):
        candidates.setdefault(label, set()).add(handle)
    return {
        label: next(iter(handles)) for label, handles in candidates.items() if len(handles) == 1
    }


def _strip_redundant_source_section(text: str) -> str:
    """Drop a trailing model bibliography already represented by body links.

    The canonical client renders one source list from ``CitationBundleV1``.
    Models nevertheless sometimes append their own ``Sources``/``来源``
    section.  Remove that section only when every Markdown destination in it
    is a canonical citation and every cited id already occurs in the answer
    body.  External or partially bound bibliographies are preserved so this
    cleanup can never hide the only copy of an unregistered source.
    """

    matches = list(_SOURCE_SECTION_HEADING_RE.finditer(text))
    if not matches:
        return text
    heading = matches[-1]
    body = text[: heading.start()]
    bibliography = text[heading.end() :]
    bibliography_ids = set(_CANONICAL_CITATION_URI_RE.findall(bibliography))
    if not bibliography_ids:
        return text
    destinations = _MARKDOWN_DESTINATION_RE.findall(bibliography)
    if not destinations or any(
        not destination.startswith("citation://") for destination in destinations
    ):
        return text
    body_ids = set(_CANONICAL_CITATION_URI_RE.findall(body))
    if not bibliography_ids.issubset(body_ids):
        return text

    # A horizontal rule immediately before the generated bibliography belongs
    # to that block as well; retaining it would leave an unexplained divider
    # before the runtime-rendered source cards.
    body = body.rstrip()
    body = re.sub(
        r"(?:^|\r?\n)[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$",
        "",
        body,
    )
    return body.rstrip()


def _decode_json_payload(content: Any, *, max_chars: int) -> Any | None:
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str) or len(content) > max_chars:
        return None
    stripped = content.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _contains_evidence_marker(content: Any) -> bool:
    if isinstance(content, str):
        return EVIDENCE_ENVELOPE_KEY in content
    if isinstance(content, dict):
        if EVIDENCE_ENVELOPE_KEY in content:
            return True
        return any(_contains_evidence_marker(value) for value in content.values())
    if isinstance(content, list):
        return any(_contains_evidence_marker(value) for value in content)
    return False


def _as_envelope_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _validate_evidence_item(
    item: dict[str, Any], *, tool_name: str | None
) -> EvidenceRecord | None:
    handle = item.get("evidenceHandle")
    source = item.get("source")
    evidence = item.get("evidence")
    locator = item.get("locator")
    if not isinstance(handle, str) or not _HANDLE_RE.fullmatch(handle):
        return None
    if not isinstance(source, dict) or not isinstance(evidence, dict):
        return None
    normalized_source = _normalize_source(source)
    normalized_evidence = _normalize_evidence(evidence)
    if normalized_source is None or normalized_evidence is None:
        return None
    normalized_locator = _normalize_locator(locator)
    if locator is not None and normalized_locator is None:
        return None
    return EvidenceRecord(
        handle=handle,
        source=normalized_source,
        evidence=normalized_evidence,
        locator=normalized_locator,
        tool_name=(
            tool_name if _bounded_nonempty_string(tool_name, _MAX_SOURCE_TEXT_CHARS) else None
        ),
    )


def _normalize_source(value: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("sourceType") not in _SOURCE_TYPES:
        return None
    required_limits = {
        "sourceId": _MAX_SOURCE_ID_CHARS,
        "providerId": _MAX_SOURCE_ID_CHARS,
        "title": _MAX_SOURCE_TEXT_CHARS,
        "retrievedAt": 128,
    }
    if any(
        not _bounded_nonempty_string(value.get(key), limit)
        for key, limit in required_limits.items()
    ):
        return None
    result = {
        key: value[key]
        for key in (
            "sourceId",
            "providerId",
            "sourceType",
            "title",
            "retrievedAt",
        )
    }
    optional_limits = {
        "documentId": _MAX_SOURCE_ID_CHARS,
        "documentVersion": _MAX_SOURCE_ID_CHARS,
        "sourceCategory": _MAX_SOURCE_TEXT_CHARS,
        "mimeType": 256,
        "organization": _MAX_SOURCE_TEXT_CHARS,
        "author": _MAX_SOURCE_TEXT_CHARS,
        "publishedAt": 128,
    }
    for key, limit in optional_limits.items():
        if _bounded_nonempty_string(value.get(key), limit):
            result[key] = value[key]
    canonical_url = value.get("canonicalUrl")
    if (
        isinstance(canonical_url, str)
        and len(canonical_url) <= _MAX_URL_CHARS
        and _safe_canonical_url(canonical_url)
    ):
        result["canonicalUrl"] = canonical_url
    return result


def _normalize_evidence(value: dict[str, Any]) -> dict[str, Any] | None:
    kind = value.get("kind")
    if kind not in _EVIDENCE_KINDS:
        return None
    if kind == "text":
        if (
            not _bounded_nonempty_string(value.get("quote"), _MAX_QUOTE_CHARS)
            or not _bounded_string(value.get("snippet"), _MAX_SNIPPET_CHARS)
            or not _bounded_nonempty_string(value.get("capturedAt"), 128)
        ):
            return None
        result = {key: value[key] for key in ("kind", "quote", "snippet", "capturedAt")}
        for key, limit in {
            "prefix": _MAX_CONTEXT_CHARS,
            "suffix": _MAX_CONTEXT_CHARS,
            "language": 128,
            "contentHash": 256,
        }.items():
            if _bounded_string(value.get(key), limit):
                result[key] = value[key]
        return result
    if kind == "structured-data":
        if any(
            not _bounded_nonempty_string(value.get(key), limit)
            for key, limit in {
                "datasetId": _MAX_SOURCE_ID_CHARS,
                "toolName": _MAX_SOURCE_TEXT_CHARS,
                "field": _MAX_SOURCE_TEXT_CHARS,
                "capturedAt": 128,
            }.items()
        ) or not _safe_scalar(
            value.get("value"),
            allow_none=True,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        ):
            return None
        result = _pick_fields(
            value,
            (
                "kind",
                "datasetId",
                "toolName",
                "recordKey",
                "entityId",
                "entityName",
                "field",
                "metric",
                "value",
                "unit",
                "currency",
                "scale",
                "period",
                "asOf",
                "scope",
                "basis",
                "capturedAt",
                "toolTraceRef",
            ),
        )
        # ``null`` is an authoritative structured value in the wire schema,
        # not the same as an omitted field.
        result["value"] = value["value"]
        coverage = value.get("coverage")
        if isinstance(coverage, dict):
            normalized_coverage = {
                key: coverage[key]
                for key in ("start", "end")
                if _bounded_nonempty_string(coverage.get(key), 128)
            }
            if normalized_coverage:
                result["coverage"] = normalized_coverage
        return result
    if any(
        not _bounded_nonempty_string(value.get(key), limit)
        for key, limit in {
            "expression": _MAX_STRUCTURED_STRING_CHARS,
            "calculatedAt": 128,
        }.items()
    ) or not _safe_scalar(
        value.get("result"),
        allow_none=False,
        max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
    ):
        return None
    inputs = value.get("inputs")
    if not isinstance(inputs, list) or not inputs or len(inputs) > _MAX_CALCULATION_INPUTS:
        return None
    normalized_inputs: list[dict[str, Any]] = []
    for item in inputs:
        if (
            not isinstance(item, dict)
            or not _bounded_nonempty_string(item.get("name"), _MAX_SOURCE_TEXT_CHARS)
            or not _bounded_nonempty_string(item.get("citationId"), _MAX_SOURCE_ID_CHARS)
            or not _safe_scalar(
                item.get("value"),
                allow_none=False,
                max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
            )
        ):
            return None
        normalized_inputs.append(_pick_fields(item, ("name", "citationId", "value", "unit")))
    result = _pick_fields(
        value,
        (
            "kind",
            "expression",
            "result",
            "unit",
            "rounding",
            "calculatedAt",
            "entityId",
            "entityName",
            "metric",
            "period",
            "scope",
            "basis",
        ),
    )
    result["inputs"] = normalized_inputs
    return result


def _normalize_locator(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") not in _LOCATOR_KINDS:
        return None
    kind = value["kind"]
    if kind == "chunk":
        if not _bounded_nonempty_string(value.get("chunkId"), _MAX_SOURCE_ID_CHARS):
            return None
        result = _pick_fields(value, ("kind", "chunkId", "segmentId"))
    elif kind == "html":
        result = _pick_fields(
            value,
            ("kind", "chunkId", "elementId", "cssSelector"),
        )
    elif kind == "pdf":
        page = value.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return None
        result = _pick_fields(
            value,
            ("kind", "page", "chunkId", "coordinateSpace", "pageRotation"),
        )
        rects = value.get("rects")
        if rects is not None:
            normalized_rects = _normalize_rects(rects)
            if normalized_rects is None:
                return None
            result["rects"] = normalized_rects
    else:
        result = _pick_fields(value, ("kind", "fragment"))

    quote = value.get("quote")
    if quote is not None:
        normalized_quote = _normalize_quote(quote)
        if normalized_quote is None:
            return None
        result["quote"] = normalized_quote
    if kind == "html" and "quote" not in result:
        return None
    return result


def _normalize_quote(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or not _bounded_nonempty_string(
        value.get("exact"), _MAX_QUOTE_CHARS
    ):
        return None
    result = {"exact": value["exact"]}
    for key in ("prefix", "suffix"):
        if _bounded_string(value.get(key), _MAX_CONTEXT_CHARS):
            result[key] = value[key]
    return result


def _normalize_rects(value: Any) -> list[dict[str, float]] | None:
    if not isinstance(value, list) or len(value) > _MAX_RECTS:
        return None
    result: list[dict[str, float]] = []
    for rect in value:
        if not isinstance(rect, dict):
            return None
        normalized: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            coordinate = rect.get(key)
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, (int, float))
                or not 0 <= float(coordinate) <= 1
            ):
                return None
            normalized[key] = float(coordinate)
        if normalized["width"] <= 0 or normalized["height"] <= 0:
            return None
        result.append(normalized)
    return result


def _safe_canonical_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    for raw_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        key = raw_key.lower().replace("-", "_")
        if (
            key in _SECRET_QUERY_KEYS
            or key.startswith("x_amz_")
            or key.startswith("x_oss_")
            or key.endswith("_token")
            or key.endswith("_signature")
        ):
            return False
    return True


def _pick_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value[key])
        for key in fields
        if key in value
        and _safe_scalar(
            value[key],
            allow_none=False,
            max_string_chars=_MAX_STRUCTURED_STRING_CHARS,
        )
    }


def _bounded_string(value: Any, max_chars: int) -> bool:
    return isinstance(value, str) and len(value) <= max_chars


def _bounded_nonempty_string(value: Any, max_chars: int) -> bool:
    return _bounded_string(value, max_chars) and bool(value.strip())


def _safe_scalar(
    value: Any,
    *,
    allow_none: bool,
    max_string_chars: int | None = None,
) -> bool:
    if value is None:
        return allow_none
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return max_string_chars is None or len(value) <= max_string_chars
    return isinstance(value, (str, int, bool))


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


__all__ = [
    "CitationGuard",
    "EVIDENCE_ENVELOPE_KEY",
    "EvidenceRecord",
    "EvidenceRegistry",
    "GuardResult",
    "POLICY_REVISION",
]
