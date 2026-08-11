from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    document_id: str
    score: float
    snippet: str
    page_ref: str | None = None
    chunk_ref: str | None = None
    filename: str | None = None
    preview_path: str | None = None
    match_line: int | None = None
    total_lines: int | None = None


@dataclass
class DocsHealthSnapshot:
    provider_id: str
    status: str  # healthy | degraded | unavailable
    reason: str | None = None


class DocsRuntimePort(Protocol):
    """Port: document retrieval execution."""

    async def search(
        self,
        query: str,
        doc_scope_ids: list[str],
        top_k: int = 5,
        doc_paths: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        """Search the given documents.

        ``doc_paths`` maps document id → resolved preview file path. The docs
        service resolves these under the owner's data dir and passes them so
        runtimes that read preview files (the embedded ripgrep baseline) do
        not re-derive paths; index-backed runtimes may ignore it. Defaulted so
        existing implementations remain valid.
        """
        ...

    async def health(self) -> DocsHealthSnapshot: ...

    @property
    def provider_id(self) -> str: ...
