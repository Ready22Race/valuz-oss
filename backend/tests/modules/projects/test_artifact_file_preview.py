from __future__ import annotations

import pytest

from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService


class FakeProjectDatastore:
    def __init__(self, row: ProjectRow | None) -> None:
        self.row = row

    async def get_by_id(self, user_id: str, project_id: str) -> ProjectRow | None:
        if self.row and self.row.id == project_id:
            return self.row
        return None


def _service(root_path: str) -> ProjectService:
    row = ProjectRow(id="proj_1", name="Demo", kind="project", root_path=root_path)
    return ProjectService(datastore=FakeProjectDatastore(row), event_bus=None)  # type: ignore[arg-type]


async def test_read_markdown_file_returns_text_artifact(tmp_path) -> None:
    (tmp_path / "report.md").write_text("# Report\n\nhello", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.md")

    assert result.artifact.kind == "project_file"
    assert result.artifact.preview_kind == "markdown"
    assert result.artifact.path == "report.md"
    assert result.artifact.capabilities.can_copy_content is True
    assert result.content.kind == "text"
    assert "# Report" in result.content.content
    assert result.content.truncated is False


async def test_read_xlsx_file_returns_spreadsheet_artifact(tmp_path) -> None:
    (tmp_path / "model.xlsx").write_bytes(b"not-a-real-xlsx")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "model.xlsx")

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.artifact.capabilities.can_preview is True
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/model.xlsx"


async def test_read_csv_file_returns_spreadsheet_artifact(tmp_path) -> None:
    (tmp_path / "data.csv").write_text("ticker,value\nAAPL,1", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "data.csv")

    assert result.artifact.preview_kind == "spreadsheet"
    assert result.artifact.capabilities.can_preview is True
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/data.csv"


async def test_read_html_file_returns_html_artifact(tmp_path) -> None:
    (tmp_path / "report.html").write_text(
        "<!doctype html><title>Report</title><h1>Report</h1>",
        encoding="utf-8",
    )

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.html")

    assert result.artifact.preview_kind == "html"
    assert result.artifact.capabilities.can_preview is True
    assert result.artifact.capabilities.can_copy_content is True
    assert result.content.kind == "text"
    assert "<h1>Report</h1>" in result.content.content


async def test_read_docx_file_returns_docx_artifact(tmp_path) -> None:
    (tmp_path / "memo.docx").write_bytes(b"PK\x03\x04 demo docx")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "memo.docx")

    assert result.artifact.preview_kind == "docx"
    assert result.artifact.capabilities.can_preview is True
    assert result.artifact.capabilities.can_copy_content is False
    assert result.content.kind == "binary"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/memo.docx"


async def test_read_legacy_doc_file_is_not_docx_preview(tmp_path) -> None:
    (tmp_path / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0 demo doc")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "legacy.doc")

    assert result.artifact.preview_kind == "unsupported"
    assert result.artifact.capabilities.can_preview is False
    assert result.content.kind == "external"


async def test_read_image_file_returns_data_url_artifact(tmp_path) -> None:
    (tmp_path / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "chart.png")

    assert result.artifact.preview_kind == "image"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "image/png"
    assert result.content.open_url.startswith("data:image/png;base64,")


async def test_read_media_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "clip.mp4")

    assert result.artifact.preview_kind == "media"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "video/mp4"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/clip.mp4"


async def test_read_large_media_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "large.mp4").write_bytes(
        b"\x00\x00\x00\x18ftypmp42" + (b"0" * (6 * 1024 * 1024))
    )

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "large.mp4")

    assert result.artifact.preview_kind == "media"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "video/mp4"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/large.mp4"


async def test_read_pdf_file_returns_raw_url_artifact(tmp_path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%demo\n")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.pdf")

    assert result.artifact.preview_kind == "pdf"
    assert result.content.kind == "binary"
    assert result.content.mime_type == "application/pdf"
    assert result.content.open_url == "/v1/projects/proj_1/raw-files/report.pdf"


async def test_resolve_file_resource_returns_safe_raw_file_metadata(tmp_path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n%demo\n")

    resource = await _service(str(tmp_path)).resolve_file_resource(
        "user-1",
        "proj_1",
        "report.pdf",
    )

    assert resource.path == tmp_path / "report.pdf"
    assert resource.rel_path == "report.pdf"
    assert resource.name == "report.pdf"
    assert resource.mime_type == "application/pdf"
    assert resource.size == 15


async def test_artifact_response_serializes_frontend_aliases(tmp_path) -> None:
    (tmp_path / "report.md").write_text("# Report", encoding="utf-8")

    result = await _service(str(tmp_path)).read_file("user-1", "proj_1", "report.md")
    payload = result.model_dump(by_alias=True)

    assert payload["artifact"]["projectId"] == "proj_1"
    assert payload["artifact"]["previewKind"] == "markdown"
    assert payload["artifact"]["capabilities"]["canCopyContent"] is True
    assert payload["content"]["modifiedAt"]


async def test_read_file_rejects_traversal(tmp_path) -> None:
    (tmp_path / "safe.md").write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError):
        await _service(str(tmp_path)).read_file("user-1", "proj_1", "../safe.md")


async def test_read_file_rejects_hidden_files(tmp_path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    with pytest.raises(PermissionError):
        await _service(str(tmp_path)).read_file("user-1", "proj_1", ".env")
