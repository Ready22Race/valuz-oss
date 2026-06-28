# Artifact File Viewer — Unified File Preview Design

> Status: M3 implemented, awaiting product acceptance (2026-06-28)
>
> One-line direction: **treat every in-app file preview as an artifact surface**.
> The first implementation still reads ordinary project files, but it routes
> them through a shared artifact shell and a replaceable renderer registry so
> later iterations can grow into document, slides, webpage, and other structured
> artifacts without rebuilding the UI.

---

## Implementation Snapshot

M1 is implemented in the `feat/artifact-file-viewer` worktree:

- Project file clicks open a shared artifact surface in the center workspace.
- Task deliverable rows and task-page file-tree selections use the same artifact
  surface instead of opening a separate system preview path.
- The selected file is stored in `?file=...`; refreshing the page restores the
  preview and removing the query param closes it.
- Markdown, code, plain text, HTML, PDF, DOCX, spreadsheet, image, and media
  files have in-app preview paths.
- Unsupported files still open in the same artifact shell and expose metadata
  plus external-open behavior.
- Editing remains a later iteration; current scope is read-only preview.

M2 is implemented and awaiting acceptance:

- Code/plain text move to a read-only CodeMirror renderer.
- PDF files return a same-origin raw preview URL and render in the artifact
  shell, avoiding unreliable iframe rendering of large `data:` URLs in
  Electron/Chromium.
- Renderer lookup is centralized so later renderer swaps stay local.
- Common actions include copy content, reload, close, and open external.
- The shared viewer surface supports basic keyboard actions: `Esc` closes,
  `Cmd/Ctrl+R` reloads, and `Cmd/Ctrl+Shift+O` opens externally.

Current focused verification:

- `backend/.venv/bin/pytest backend/tests/modules/projects/test_artifact_file_preview.py`
- `backend/.venv/bin/ruff check backend/valuz_agent/modules/projects/service.py backend/valuz_agent/api/routes/projects.py backend/tests/modules/projects/test_artifact_file_preview.py`
- `pnpm --filter @valuz/app typecheck`
- `pnpm --filter @valuz/ui typecheck`
- `pnpm exec eslint packages/ui/src/components/artifacts/ArtifactViewerShell.tsx`

Repository-wide `make typecheck` and `make lint` still fail on existing backend
mypy/module-boundary baseline issues unrelated to this artifact viewer work.

## 1. Problem

Project file trees currently behave like navigation only. Users can see that a
project or chat workspace contains files, but the app does not provide a first
class way to inspect those files inside the client.

The product promise is broader than "open a file": Valuz projects are workspaces
where agents produce material, reports, code, research outputs, and assets. Those
outputs should feel like live work objects in the app, not just paths in a tree.

The first goal is browsing, not editing. Editing should be designed into the
contracts from day one, but shipped after the preview architecture is stable.

---

## 2. Inspiration And Boundary

YouMind's document surface is useful as a product reference:

- Chat remains visible while the generated file opens in a right-side surface.
- The opened file has artifact chrome: title, toolbar, cover/content area, and
  type-specific actions.
- Its "Document" view is not a generic file preview. It is a structured
  Tiptap/ProseMirror document editor with custom nodes, citations, dividers, and
  cover controls.
- YouMind appears to separate raw materials from created artifacts such as
  document, page, slides, video/cast, image, PDF, office, and webpage files.

Valuz should not copy the whole artifact data system in the first pass. The
near-term target is **artifact feel with file-backed data**:

- A file opens in the same artifact panel no matter where it came from.
- The shell is shared.
- The renderer is type-specific and replaceable.
- A future structured artifact kind can be added without replacing the shell,
  routing, URL state, or panel layout.

---

## 3. Design Principles

1. **One shell, many renderers**  
   All previews use the same `ArtifactViewerShell`; each file type chooses a
   renderer from a registry.

2. **File-backed first, artifact-ready always**  
   MVP artifacts can be backed by project files or session outputs. The API and
   UI should still call them artifacts so later `document_artifact` or
   `slides_artifact` kinds fit naturally.

3. **Preview before edit**  
   Browsing lands first. Editing capabilities are exposed as metadata and hidden
   or disabled until the write path is implemented.

4. **Renderer replacement should be local**  
   Replacing Markdown preview with Tiptap, `<pre>` with CodeMirror, or iframe
   PDF preview with PDF.js should not require changes to project pages, task
   pages, chat pages, routing, or file tree selection.

5. **Never scatter type checks through pages**  
   No page-level `path.endsWith(".md")` branches. Type detection and renderer
   selection live in one artifact service/registry layer.

6. **Preserve local-first filesystem semantics**  
   Project files remain ordinary files on disk. The viewer must not require a
   database artifact migration before users can inspect the current workspace.

---

## 4. Target Architecture

```
ProjectFileTree / OutputCard / TaskArtifactLink
              │
              ▼
      ArtifactSelectionState
   (URL + local panel state)
              │
              ▼
      ArtifactViewerShell
  toolbar · metadata · actions
              │
              ▼
      ArtifactRendererRegistry
              │
 ┌────────────┼─────────────┬──────────────┬────────────┐
 ▼            ▼             ▼              ▼            ▼
Markdown    Code          Image            PDF          Unsupported
Renderer    Renderer      Renderer         Renderer     Renderer
```

The shell owns layout and common behavior. Renderers own only type-specific
presentation.

---

## 5. Core Contracts

### 5.1 Artifact Descriptor

The descriptor is lightweight metadata used before content is fetched.

```ts
type ArtifactKind =
  | "project_file"
  | "session_output"
  | "document_artifact"
  | "slides_artifact"
  | "webpage_artifact";

type ArtifactPreviewKind =
  | "markdown"
  | "code"
  | "image"
  | "pdf"
  | "html"
  | "docx"
  | "media"
  | "spreadsheet"
  | "plain"
  | "unsupported";

type ArtifactDescriptor = {
  id: string;
  kind: ArtifactKind;
  projectId?: string;
  sessionId?: string;
  path?: string;
  name: string;
  mimeType?: string;
  extension?: string;
  size?: number;
  modifiedAt?: string;
  previewKind: ArtifactPreviewKind;
  capabilities: {
    canPreview: boolean;
    canEdit: boolean;
    canOpenExternal: boolean;
    canCopyContent: boolean;
    canDownload: boolean;
  };
};
```

MVP only needs `project_file` and possibly `session_output`, but the union
includes future artifact types to force the shell to be generic from the start.

### 5.2 Artifact Content

Content is fetched on selection. It is intentionally separate from descriptor
metadata so file trees stay cheap.

```ts
type ArtifactContent =
  | {
      kind: "text";
      encoding: "utf-8";
      content: string;
      truncated: boolean;
      etag?: string;
      modifiedAt?: string;
    }
  | {
      kind: "binary";
      objectUrl: string;
      mimeType: string;
      size?: number;
    }
  | {
      kind: "external";
      openUrl?: string;
      reason: string;
    };
```

For local files the backend should normally return bytes or text through a
Valuz API rather than exposing raw filesystem paths to the browser.

### 5.3 Renderer Props

```ts
type ArtifactRendererProps = {
  artifact: ArtifactDescriptor;
  content: ArtifactContent | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
};
```

Renderers should not know whether they are inside a project page, conversation
page, or task page.

---

## 6. Renderer Registry

Initial registry:

| Preview kind | First renderer | Later renderer |
|---|---|---|
| `markdown` | Existing `MarkdownContent` source/rendered view | Tiptap document renderer/editor |
| `code` | Read-only `<pre>` or minimal text view | CodeMirror 6 |
| `plain` | Wrapped monospace text | CodeMirror or plain text editor |
| `image` | Native `<img>` with fit/zoom controls | Pan/zoom gallery renderer |
| `pdf` | Browser blob iframe/object preview | PDF.js with page nav/search |
| `html` | Sandboxed iframe with Preview/Source toggle | HTML artifact renderer with asset rewriting |
| `docx` | docx-preview readonly render | Optional conversion fallback for legacy `.doc` |
| `media` | Native `<audio>` / `<video>` | Timeline-aware renderer |
| `spreadsheet` | SheetJS parse + readonly grid | Virtualized grid, formatting, formula view/edit |
| `unsupported` | Metadata card + open external | Type-specific renderer when added |

Important: Tiptap is a strong fit for structured documents, not a universal
preview engine. CodeMirror is a strong fit for code and text, not rich reports.
PDF.js is a strong fit for PDFs, not editing. The architecture should let each
tool do its job without forcing one component to become a kitchen sink.

### 6.1 Spreadsheet Preview

Research workflows commonly rely on `.xlsx`, `.xls`, and `.csv` files for
models, factor tables, screening results, and portfolio data. The first
spreadsheet iteration should prioritize readable inspection rather than editing
or perfect Excel fidelity:

- Parse workbooks in the client with SheetJS.
- Fetch spreadsheet files through the existing raw file endpoint rather than
  embedding binary bytes into JSON.
- Show workbook sheet tabs, a compact table grid, row numbers, column letters,
  and a lightweight summary of row/column counts.
- Use virtualized rows and columns so large sheets remain browsable without
  imposing a visible row/column preview cap.
- Freeze the coordinate headers by overlaying row/column headers in the same
  scroll coordinate system as the virtual grid.
- Support cell search with match navigation and scroll-to-match.
- Support selecting cells, rows, columns, or shift-extended ranges and copying
  the selected area as TSV for pasting back into spreadsheet tools.
- Preserve common display formatting through SheetJS formatted cell values and
  workbook metadata: merged cells, row heights, column widths, and best-effort
  font/fill/alignment/border styles when available.
- Treat formulas, charts, pivots, macros, and complex conditional formatting as
  later work.

---

## 7. Backend API Shape

The API contract should be added to `api/openapi.yaml` first.

MVP project-file endpoints:

```http
GET /v1/projects/{project_id}/files
GET /v1/projects/{project_id}/files/{file_path:path}
```

The existing list endpoint can remain, but its file nodes should eventually
include enough metadata to build descriptors:

```json
{
  "name": "report.md",
  "type": "file",
  "path": "reports/report.md",
  "size": 12345,
  "modified": "2026-06-26T10:30:00Z",
  "mime_type": "text/markdown",
  "preview_kind": "markdown"
}
```

The read endpoint should return a descriptor plus content:

```json
{
  "artifact": {
    "id": "project_file:abc:reports/report.md",
    "kind": "project_file",
    "projectId": "abc",
    "path": "reports/report.md",
    "name": "report.md",
    "mimeType": "text/markdown",
    "previewKind": "markdown",
    "capabilities": {
      "canPreview": true,
      "canEdit": false,
      "canOpenExternal": true,
      "canCopyContent": true,
      "canDownload": false
    }
  },
  "content": {
    "kind": "text",
    "encoding": "utf-8",
    "content": "# Report",
    "truncated": false,
    "etag": "mtime-size-hash",
    "modifiedAt": "2026-06-26T10:30:00Z"
  }
}
```

Security requirements:

- Resolve paths against the project root and reject traversal.
- Skip hidden/system directories by default.
- Refuse or truncate large files.
- Detect binary files before decoding.
- Never expose secrets such as `.env` content by default.
- Use MIME and extension heuristics in one backend utility, mirrored by frontend
  fallback logic only when needed.

---

## 8. Frontend Integration Points

Current reusable pieces:

- `ProjectFileTree` already supports `onFileClick`, `onFileDoubleClick`, and
  `activeFilePath`.
- `ProjectContextPanel` already renders the file tree as a tab.
- `SkillDetailPanel` already demonstrates a tree + preview split with Markdown
  rendering, source view, and copy affordances.

Target additions:

| Component | Role |
|---|---|
| `ArtifactViewerShell` | Shared chrome for all file/artifact previews |
| `ArtifactToolbar` | Title, type, reload, open external, copy, future edit |
| `ArtifactRendererRegistry` | Maps `previewKind` to renderer component |
| `ProjectFileArtifactProvider` | Fetches project-file descriptors/content |
| `useArtifactSelection` | URL and local state for selected artifact |
| `FileWorkspacePanel` | Combines tree and selected artifact viewer |

Suggested URL shape:

```text
/projects/:id?file=reports/report.md
/conversations/:id?file=reports/report.md
/tasks/:id?file=tasks/output.md
```

If the selected file is missing or no longer previewable, keep the panel open
and show a recoverable error. Do not silently collapse the viewer.

---

## 9. Iteration Plan

### M0 — Design And Contracts

Goal: lock the abstraction before implementation.

- Add this design document.
- Add OpenAPI shapes for file content read.
- Decide file size limits, hidden path policy, and MIME detection rules.
- Add frontend types for `ArtifactDescriptor`, `ArtifactContent`, and renderer
  props.

Acceptance:

- Project file preview has a documented API contract.
- Frontend artifact types compile without UI behavior changes.

### M1 — Read-Only Artifact Shell

Goal: every project file can open in a first-class in-app surface.

- Implement safe backend file read.
- Implement `ArtifactViewerShell`.
- Implement `markdown`, `plain`, `image`, `media`, `unsupported` renderers.
- Wire project file tree click to open the viewer instead of only inserting an
  `@filename` mention.
- Preserve a separate "reference in chat" action.
- Store selected file in URL state.

Acceptance:

- Clicking a Markdown/text/image/media file opens it in the right panel.
- Unknown files show metadata and "open external".
- Reloading the page restores the selected file.

### M2 — Code And PDF Browsing

Goal: make common engineering and report files pleasant to inspect.

- Add CodeMirror 6 for code/plain text read-only mode.
- Add browser-iframe PDF preview or PDF.js MVP.
- Add source/rendered toggle for Markdown.
- Add copy content/path actions.
- Add keyboard navigation between tree and viewer.

Acceptance:

- Code, JSON, YAML, Markdown, PDF, and image files have distinct renderers.
- Renderer replacement is local to the registry.

### M3 — Editing Foundation

Goal: turn selected renderer types into safe editors.

- Add `PUT /v1/projects/{project_id}/files/{file_path:path}`.
- Add `etag` / `modifiedAt` optimistic concurrency checks.
- Enable CodeMirror editing for text/code allowlist.
- Add dirty state, save, reload, conflict modal, and discard flow.
- Keep `.env`, hidden files, binaries, large files, and generated system files
  read-only.

Acceptance:

- Editing a Markdown/text/code file is safe against concurrent agent writes.
- Conflicts are visible and recoverable.

### M4 — Structured Document Artifacts

Goal: introduce YouMind-style structured documents without breaking file
preview.

- Add `document_artifact` kind.
- Add Tiptap/ProseMirror schema for reports.
- Add conversion from Markdown/HTML/parser output into document blocks.
- Add document renderer/editor behind the existing `markdown` or new
  `document` renderer slot.
- Keep file-backed Markdown preview working.

Acceptance:

- Agent-generated reports can open as structured documents.
- Existing project file previews still use the same shell.

### M5 — Rich Artifact Types

Goal: grow beyond documents.

- Add `slides_artifact` renderer.
- Add webpage artifact renderer for generated HTML/sites.
- Add richer media and image review surfaces.
- Add artifact version history, export, and AI rewrite controls.

Acceptance:

- Artifact types are added through descriptors and renderer registration, not
  new page-level preview implementations.

---

## 10. Non-Goals For The First Pass

- No universal editor.
- No full YouMind-style artifact database migration.
- No Office-native editing.
- No PDF annotation or page-level edits.
- No automatic conversion of every local project file into a database artifact.
- No agent rewrite UI until read-only browsing is stable.

---

## 11. Open Questions

1. Should chat/session generated files use the same project-file read endpoint
   when they live under the session cwd, or should they have a separate
   `session_output` endpoint from day one?
2. Should Markdown reports stay file-backed in M1-M3, or should agent-generated
   reports immediately get a lightweight artifact record?
3. What is the default maximum preview size for text files? Candidate: 1 MiB
   full read, larger files truncated with explicit UI.
4. Should hidden files be entirely invisible in the viewer, or visible but
   blocked from content read?
5. How should "reference in chat" behave after file click becomes preview?
   Candidate: make it a toolbar/context-menu action, not the primary click.

---

## 12. Success Criteria

This architecture is working if:

- Project, conversation, and task pages all open files through the same artifact
  viewer.
- Adding a new renderer does not touch those pages.
- Editing can be introduced by changing capabilities and renderer behavior,
  without redesigning the file tree or right panel.
- Structured document artifacts can coexist with raw project files.
- Users experience files as first-class work objects inside Valuz, not as inert
  filesystem rows.
