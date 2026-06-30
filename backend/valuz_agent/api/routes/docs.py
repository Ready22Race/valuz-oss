from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from valuz_agent.api.deps import get_document_service, get_current_user_id
from valuz_agent.modules.docs.service import (
    DocSearchHit,
    DocumentDetail,
    DocumentLibraryService,
    DocumentListItem,
    ImportTaskResult,
    KbDetail,
    KbTreeNode,
)

router = APIRouter(tags=["docs"])


# ── Request models ────────────────────────────────────────────────────


class CreateKbRequest(BaseModel):
    name: str
    root_path: str
    parser_routing: str = "local_only"
    auto_discover: bool = False


class UpdateKbRequest(BaseModel):
    name: str | None = None
    parser_routing: str | None = None


class ReindexRequest(BaseModel):
    document_ids: list[str]


class SearchRequest(BaseModel):
    query: str
    project_id: str
    top_k: int = 5
    folder_ids: list[str] | None = None
    document_ids: list[str] | None = None


class BindingItem(BaseModel):
    binding_kind: str  # kb | folder | document
    target_id: str


class UpdateBindingsRequest(BaseModel):
    bindings: list[BindingItem]


# ── KB CRUD ───────────────────────────────────────────────────────────


@router.post("/v1/kb")
async def create_kb(
    body: CreateKbRequest,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> KbDetail:
    return await svc.create_kb(
        user_id,
        name=body.name,
        root_path=body.root_path,
        parser_routing=body.parser_routing,
        auto_discover=body.auto_discover,
    )


@router.get("/v1/kb")
async def list_kbs(
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict:
    from valuz_agent.ports.extensions import ext

    rows = await svc.list_kbs(user_id)
    items = [item.model_dump() if hasattr(item, "model_dump") else item for item in rows]
    items = await ext.resource_list_hook.apply("kb", items)
    return {"knowledge_bases": items}


@router.get("/v1/kb/{kb_id}")
async def get_kb(
    kb_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> KbDetail:
    return await svc.get_kb(user_id, kb_id)


@router.patch("/v1/kb/{kb_id}")
async def update_kb(
    kb_id: str,
    body: UpdateKbRequest,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> KbDetail:
    return await svc.update_kb(user_id, kb_id, name=body.name, parser_routing=body.parser_routing)


@router.delete("/v1/kb/{kb_id}")
async def delete_kb(
    kb_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, str]:
    await svc.delete_kb(user_id, kb_id)
    return {"kb_id": kb_id}


@router.post("/v1/kb/{kb_id}/rescan")
async def rescan_kb(
    kb_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> ImportTaskResult:
    # ``start_rescan_kb`` returns immediately with a ``processing``
    # task row and runs the actual diff + reindex on a daemon thread.
    # A user-triggered rescan on a folder with thousands of files
    # would otherwise hold the request open for many seconds and
    # freeze the dialog / button. Progress is polled via
    # /v1/docs/tasks/{task_id}.
    return await svc.start_rescan_kb(user_id, kb_id)


@router.get("/v1/kb/{kb_id}/tree")
async def get_kb_tree(
    kb_id: str,
    folder_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, list[KbTreeNode]]:
    return {"nodes": await svc.get_kb_tree(user_id, kb_id, folder_id=folder_id)}


# ── Document CRUD ─────────────────────────────────────────────────────


@router.get("/v1/docs")
async def list_docs(
    q: str | None = None,
    status: str | None = None,
    kb_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, list[DocumentListItem]]:
    return {"documents": await svc.list_documents(user_id, query=q, status=status, kb_id=kb_id)}


@router.get("/v1/docs/health")
async def docs_health(
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, object]:
    return await svc.get_docs_health(user_id)


@router.post("/v1/docs/reindex")
async def reindex_docs(
    body: ReindexRequest,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> ImportTaskResult:
    return await svc.reindex_documents(user_id, body.document_ids)


@router.post("/v1/docs/search")
async def search_docs(
    body: SearchRequest,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, list[DocSearchHit]]:
    return {
        "hits": await svc.search_docs(
            user_id,
            body.project_id,
            body.query,
            body.top_k,
            folder_ids=body.folder_ids,
            document_ids=body.document_ids,
        )
    }


@router.get("/v1/docs/tasks/{task_id}")
async def get_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> ImportTaskResult:
    return await svc.get_import_task(user_id, task_id)


@router.get("/v1/docs/{doc_id}")
async def get_doc(
    doc_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> DocumentDetail:
    return await svc.get_document(user_id, doc_id)


@router.get("/v1/docs/{doc_id}/preview")
async def get_preview(
    doc_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, str]:
    md = await svc.get_document_preview(user_id, doc_id)
    return {"document_id": doc_id, "markdown": md}


@router.delete("/v1/docs/{doc_id}")
async def delete_doc(
    doc_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, str]:
    await svc.delete_document(user_id, doc_id)
    return {"document_id": doc_id}


# ── Project KB bindings ───────────────────────────────────────────────


@router.get("/v1/projects/{project_id}/kb-bindings")
async def list_bindings(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, list[dict[str, str]]]:
    rows = await svc.list_project_bindings(user_id, project_id)
    return {
        "bindings": [
            {
                "project_id": r.project_id,
                "binding_kind": r.binding_kind,
                "target_id": r.target_id,
            }
            for r in rows
        ]
    }


@router.put("/v1/projects/{project_id}/kb-bindings")
async def update_bindings(
    project_id: str,
    body: UpdateBindingsRequest,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, list[dict[str, str]]]:
    rows = await svc.update_project_bindings(
        user_id,
        project_id,
        [{"binding_kind": b.binding_kind, "target_id": b.target_id} for b in body.bindings],
    )
    return {
        "bindings": [
            {
                "project_id": r.project_id,
                "binding_kind": r.binding_kind,
                "target_id": r.target_id,
            }
            for r in rows
        ]
    }


@router.delete("/v1/projects/{project_id}/kb-bindings")
async def remove_bindings(
    project_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: DocumentLibraryService = Depends(get_document_service),
) -> dict[str, bool]:
    await svc.remove_project_bindings(user_id, project_id)
    return {"ok": True}
