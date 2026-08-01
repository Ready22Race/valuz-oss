"""DeepAgents citation evidence compaction tests."""

from __future__ import annotations

import json
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.runtimes.deepagents.middleware import (
    CitationEvidenceCompactionMiddleware,
    citation_artifact_content,
)


async def test_citation_evidence_is_compacted_for_model_and_preserved_privately() -> None:
    envelope = {
        "evidenceHandle": "ev_revenue_12345678",
        "source": {
            "sourceId": "financials:600519",
            "providerId": "valuz-stock",
            "sourceType": "dataset",
            "title": "Company income statement · 600519",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "income_statement",
            "recordKey": "600519|2024 FY",
            "field": "total_revenue.operating_revenue",
            "metric": "operating_revenue",
            "value": 170_899_152_276,
            "unit": "CNY",
            "period": "2024 FY",
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }
    full_payload = {
        "_valuz_evidence": [envelope],
        "data": [{"total_revenue": {"operating_revenue": 170_899_152_276}}],
    }
    original_content = [{"type": "text", "text": json.dumps(full_payload)}]
    original = ToolMessage(
        content=original_content,
        tool_call_id="call-1",
        name="income_statement",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert isinstance(result, ToolMessage)
    compact_text = result.content[0]["text"]
    compact = json.loads(compact_text)
    assert compact["data"] == full_payload["data"]
    assert compact["_valuz_evidence"] == [
        {
            "evidenceHandle": "ev_revenue_12345678",
            "kind": "structured-data",
            "field": "total_revenue.operating_revenue",
            "metric": "operating_revenue",
            "value": 170_899_152_276,
            "unit": "CNY",
            "period": "2024 FY",
            "recordKey": "600519|2024 FY",
            "sourceTitle": "Company income statement · 600519",
        }
    ]
    assert "capturedAt" not in compact_text
    private_content = citation_artifact_content(result)
    assert private_content is not None
    assert json.loads(private_content) == original_content


async def test_non_citation_tool_result_is_unchanged() -> None:
    original = ToolMessage(content="plain result", tool_call_id="call-1", name="plain")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert result is original
    assert citation_artifact_content(result) is None
