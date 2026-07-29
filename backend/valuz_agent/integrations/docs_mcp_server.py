"""In-process MCP server exposing the project's bound knowledge base.

Why this exists
---------------
The valuz-project-docs builtin skill (auto-loaded when a project has any
KB binding) advertises ``doc_search`` and ``list_doc_scope`` to the agent.
Those tools live in the host: the agent's runtime (Claude Agent SDK or
DeepAgents) is the kernel side of the boundary, the EmbeddedDocsRuntime
is the host side. The kernel only speaks MCP for custom tools, so we
ship the docs runtime as a tiny in-process MCP HTTP server and hand the
session a ``McpServerConfig`` pointing at our own loopback address.

Wire shape
----------
    POST /_internal/mcp/docs/mcp
      (also served at the legacy ``/internal/mcp/docs/mcp`` — ADR-013
      dual-mount, see ``api/app.py::_mount_internal``)
      headers:
        X-Valuz-Internal:    <per-process token>
        X-Valuz-Session-Id:  <kernel session id>
        ↳ FastMCP streamable_http_app, scoped to the session via a
          ContextVar set by ``build_docs_mcp_asgi``'s wrapper.

The tool handlers:

- ``doc_search(query, folder_ids?, document_ids?, top_k?)`` — keyword
  search across the docs bound to this session's project. Optional
  ``folder_ids`` / ``document_ids`` further narrow scope; both are
  intersected with the project's bindings, so a runaway agent cannot
  reach docs from a different project even if it guesses an id.
- ``list_doc_scope(folder_id?)`` — returns the document tree the
  project is bound to. With no argument: every bound KB / folder /
  doc. With ``folder_id``: only that subtree.

Security note
-------------
The URL is mounted at the host's loopback address only and gated by a
per-process ``X-Valuz-Internal`` header (configured via
``settings.internal_mcp_token``). The session id rides another
header — keeping it out of the URL avoids confusing FastMCP's
internal Starlette routing while still letting each request scope
itself to the right project.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from valuz_agent.integrations._mcp_asgi import (
    build_internal_mcp_asgi,
    get_current_mcp_session_id,
    get_current_mcp_user_id,
    internal_mcp_transport_security,
)

logger = logging.getLogger(__name__)

# Bound for the duration of one HTTP request by the ASGI wrapper in
# ``mount_docs_mcp``. Tools read it to scope their datastore access.


def _current_session_id() -> str:
    sid = get_current_mcp_session_id()
    if not sid:
        raise RuntimeError("docs MCP tool called outside of a session-scoped request")
    return sid


def _current_user_id() -> str:
    return get_current_mcp_user_id()


async def _resolve_project_id(user_id: str, session_id: str) -> str | None:
    """Map ``session_id`` → ``project_id`` via the host project↔session index.

    A host fact (``valuz_project_session``), so no kernel round-trip — works
    even when a remote sandbox kernel is gone (DataService design §5).
    """
    from valuz_agent.modules.sessions import project_index

    project_id = await project_index.project_of(session_id)
    return str(project_id) if project_id else None


async def _resolve_session_knowledge_bases(
    user_id: str,
    session_id: str,
) -> list[str] | None:
    """Return the session's all-available KB snapshot, or project mode.

    ``None`` means use the existing project bindings. An explicit list
    (including ``[]``) means the session was created under all-available policy.
    DocumentService re-authorizes every id at tool-call time.
    """
    from valuz_agent.adapters.data_reader import data_reader

    session = await data_reader().get_session(user_id, session_id)
    if session is None:
        return []
    metadata = getattr(session, "metadata", None) or {}
    valuz = metadata.get("valuz", {}) if isinstance(metadata, dict) else {}
    manifest = valuz.get("capability_manifest", {}) if isinstance(valuz, dict) else {}
    if not isinstance(manifest, dict) or manifest.get("policy") != "all_available":
        return None
    ids = manifest.get("knowledge_bases", [])
    return [str(item) for item in ids] if isinstance(ids, list) else []


def _build_doc_service(db: Any, user_id: str) -> Any:  # type: ignore[no-untyped-def]
    """Build a one-shot DocumentLibraryService against ``db`` (an open
    ``AsyncSession``).

    Each tool call opens its own async unit of work (see the call sites);
    this just wires the service against that session. The cost is a few μs
    per invocation; in return we don't have to thread a long-lived session
    through the FastMCP request pipeline.
    """
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
    from valuz_agent.integrations.parser_light_local import LightLocalParser
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.docs.service import DocumentLibraryService

    preview_dir = fs_registry.docs_preview_dir(user_id)
    return DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=LightLocalParser(),
        docs_runtime=EmbeddedDocsRuntime(preview_dir=preview_dir),
        event_bus=event_bus,
        scan_state_dir=fs_registry.docs_scan_state_dir(user_id),
    )


# ---------------------------------------------------------------------------
# FastMCP app — single module-level instance shared across sessions.
# ---------------------------------------------------------------------------

_mcp = FastMCP(
    "valuz-project-docs",
    transport_security=internal_mcp_transport_security(),
    # Stateless like the toolkit server: session state in process memory 404s
    # any follow-up request that lands on another replica/worker behind a
    # load balancer (client surfaces it as "McpError: Session terminated").
    stateless_http=True,
)


@_mcp.tool()
async def doc_search(
    query: str,
    folder_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Search the project's bound documents for ``query`` (keyword, ranked).

    Returns up to ``top_k`` hits, each ``{document_id, filename, score,
    snippet, page_ref?, chunk_ref?}``. Empty list when nothing matches —
    that's a normal answer, not an error.

    ``folder_ids`` / ``document_ids`` can narrow the search; both are
    intersected with the project's bindings server-side so the agent
    cannot reach docs outside its scope by guessing ids.
    """
    from valuz_agent.infra.db import async_unit_of_work

    session_id = _current_session_id()
    user_id = _current_user_id()
    project_id = await _resolve_project_id(user_id, session_id)
    if project_id is None:
        return []
    knowledge_base_ids = await _resolve_session_knowledge_bases(user_id, session_id)
    async with async_unit_of_work(commit=False) as db:
        svc = _build_doc_service(db, user_id)
        hits = await svc.search_docs(
            user_id,
            project_id=project_id,
            query=query,
            folder_ids=folder_ids or None,
            document_ids=document_ids or None,
            top_k=top_k or 5,
            knowledge_base_ids=knowledge_base_ids,
        )
    return [
        {
            "document_id": h.document_id,
            "filename": h.filename,
            "score": h.score,
            "snippet": h.snippet,
            "page_ref": h.page_ref,
            "chunk_ref": h.chunk_ref,
        }
        for h in hits
    ]


@_mcp.tool()
async def list_doc_scope(folder_id: str | None = None) -> dict[str, Any]:
    """Return the document tree bound to this project.

    With no argument: a flat-ish view of every KB / folder / document
    bound to the project. With ``folder_id``: only that subtree.

    Output shape is JSON-serialisable and tuned for an agent: each node
    has ``kind`` (``kb``/``folder``/``document``), ``id``, ``name``, and
    ``children`` (where applicable). The ``bound_directly`` flag tells
    the agent whether the user explicitly bound that node vs inheriting
    via a parent.
    """
    from valuz_agent.infra.db import async_unit_of_work

    del folder_id  # full-tree view is enough today; folder drilldown is a TODO.
    session_id = _current_session_id()
    user_id = _current_user_id()
    project_id = await _resolve_project_id(user_id, session_id)
    if project_id is None:
        return {"knowledge_bases": [], "total_documents": 0}
    knowledge_base_ids = await _resolve_session_knowledge_bases(user_id, session_id)
    async with async_unit_of_work(commit=False) as db:
        svc = _build_doc_service(db, user_id)
        tree = await svc.build_doc_scope_tree(
            user_id,
            project_id,
            knowledge_base_ids=knowledge_base_ids,
        )
    return _scope_tree_to_dict(tree)


def _scope_tree_to_dict(tree: Any) -> dict[str, Any]:
    """Recursively convert ``DocScopeTreeView`` (nested dataclasses) to dicts."""

    def _node(n: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": getattr(n, "kind", "kb"),
            "id": n.id,
            "name": n.name,
            "bound_directly": bool(getattr(n, "bound_directly", False)),
        }
        if getattr(n, "path", None) is not None:
            out["path"] = n.path
        if getattr(n, "document_count", None) is not None:
            out["document_count"] = n.document_count
        children = getattr(n, "children", ())
        if children:
            out["children"] = [_node(c) for c in children]
        return out

    return {
        "knowledge_bases": [_node(kb) for kb in tree.knowledge_bases],
        "total_documents": tree.total_documents,
    }


# ---------------------------------------------------------------------------
# ASGI wrapper — adds the session-id scoping + token check.
# ---------------------------------------------------------------------------


def docs_mcp_session_manager_run() -> Any:
    """Return the FastMCP session-manager async context manager.

    Mounted-as-sub-app FastMCP doesn't get its own ``lifespan`` events
    fired by the parent FastAPI app, so the
    ``StreamableHTTPSessionManager`` would never start its background
    request-handling task and every client request would die with
    ``Session terminated``. The host's ``create_app`` lifespan calls
    this and keeps the returned context open for the app's lifetime.

    Lazily-initialised: the session manager itself is only constructed
    when ``streamable_http_app()`` is first called (FastMCP's design),
    so ensure the ASGI app is built before this runs.
    """
    # Force the lazy init.
    _mcp.streamable_http_app()
    return _mcp.session_manager.run()


def build_docs_mcp_asgi() -> Any:
    """Return an ASGI app to mount at ``/_internal/mcp/docs`` (and, dual-mounted
    for pre-ADR-013 session compatibility, ``/internal/mcp/docs`` — see
    ``api/app.py::_mount_internal``)."""
    return build_internal_mcp_asgi(_mcp.streamable_http_app())


def docs_mcp_url(*, base_url: str) -> str:
    """Compose the docs MCP endpoint the kernel client should call.

    Session id flows through the ``X-Valuz-Session-Id`` header instead
    of the URL — see ``build_docs_mcp_asgi`` for the rationale. The
    fixed path keeps FastMCP's internal routing happy.

    ADR-013: newly created sessions get the ``/_internal/...`` path;
    ``/internal/...`` stays mounted so session snapshots that persisted the
    pre-rename URL keep working on restore (see ``api/app.py::_mount_internal``,
    removed the next OSS major version).
    """
    return f"{base_url.rstrip('/')}/_internal/mcp/docs/mcp"


__all__ = [
    "build_docs_mcp_asgi",
    "docs_mcp_url",
]
