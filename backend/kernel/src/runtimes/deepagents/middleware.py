"""Custom langchain middleware used by ``DeepAgentsRuntime``.

DeepAgents wires extra behavior into a graph by composing langchain
``AgentMiddleware`` subclasses. This module collects the harness-side
middleware so the runtime stays focused on graph wiring and event mapping.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from src.core.citation import compact_citation_tool_content

logger = logging.getLogger(__name__)

_CITATION_ARTIFACT_KEY = "_valuz_citation_content"


class ToolErrorTolerantMiddleware(AgentMiddleware):
    """Catch tool exceptions and feed them back to the model as a ToolMessage.

    DeepAgents (langchain) lets a tool raise propagate up the graph, which
    aborts the run. For transient/recoverable failures (HTTP 4xx/5xx, network
    blips, validation errors) we'd rather hand the error string to the model
    so it can read the message and try again on the next step. Permanent
    bugs still surface — the agent will see the same error repeatedly and
    eventually give up via max_turns.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except Exception as exc:
            tool_call = request.tool_call
            logger.warning(
                "Tool '%s' raised %s — returning error to model: %s",
                tool_call.get("name"),
                type(exc).__name__,
                exc,
            )
            return ToolMessage(
                content=f"Error calling tool '{tool_call.get('name')}': {exc}",
                tool_call_id=tool_call["id"],
                name=tool_call.get("name"),
                status="error",
            )


class CitationEvidenceCompactionMiddleware(AgentMiddleware):
    """Keep full citation evidence private while giving the model slim handles.

    Source-bearing MCP tools can return hundreds of repeated source/evidence
    envelopes.  LangChain would otherwise add all of that metadata to every
    subsequent model call.  The model needs the handle-to-field mapping, while
    CitationGuard needs the full immutable envelope.  Store the original in a
    ToolMessage artifact (not sent to the model) and compact only the visible
    content.  The runtime event adapter forwards the artifact through the
    existing private ``_citation_content`` sidecar.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result
        compacted = compact_citation_tool_content(result.content)
        if compacted is None:
            return result
        artifact = dict(result.artifact) if isinstance(result.artifact, dict) else {}
        if result.artifact is not None and not isinstance(result.artifact, dict):
            artifact["originalArtifact"] = result.artifact
        artifact[_CITATION_ARTIFACT_KEY] = _serialize_tool_content(result.content)
        return result.model_copy(update={"content": compacted, "artifact": artifact})


def citation_artifact_content(output: Any) -> str | None:
    if not isinstance(output, ToolMessage) or not isinstance(output.artifact, dict):
        return None
    value = output.artifact.get(_CITATION_ARTIFACT_KEY)
    return value if isinstance(value, str) else None


def _serialize_tool_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(content)
