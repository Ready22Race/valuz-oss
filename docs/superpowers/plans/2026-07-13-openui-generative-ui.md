# OpenUI Generative UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an in-conversation agent, when it judges a rich UI more helpful, call a built-in `generate_ui` MCP tool. The backend uses the OpenUI `genui-lib` system prompt to run one LLM call that produces OpenUI Lang, and the frontend renders it inline as an interactive component via the official `<Renderer>`. **Phase 1** ships generate-then-render (synchronous); **Phase 2** upgrades it to render-while-generating — the ephemeral session's `text_delta` is forwarded in real time into the calling session as `tool_output_delta`, and the frontend `<Renderer isStreaming>` paints the UI token by token.

**Architecture:** Reuse the memory module's "built-in MCP tool + ephemeral-session completer" pattern. The `generate_ui` tool is registered in the host toolkit MCP `base` toolset (runtime-agnostic); the handler resolves runtime/provider/model from the calling session, opens a cloned ephemeral session, and runs a one-shot no-tools LLM call (system prompt = vendored `genui-lib` prompt + request + optional data), returning OpenUI Lang as `ToolResult.content`. The frontend lifts the tool in `ConversationPage.renderToolCall` via `isToolNamed(name, "generate_ui")` and mounts `<Renderer response={tool.output} isStreaming={false} />` (Phase 1). Phase 2 reuses the ready-made host→kernel live-injection channel `kernel_client.emit_live_event` to forward the ephemeral `text_delta` into the calling session; because the MCP request carries no `tool_use_id`, the id is self-discovered from the calling session's `tool_use` events by an input fingerprint (concurrency-safe, timestamp tiebreak). `run_turn`'s full text is still returned as the `ToolResult` (canonical fallback). **No main-system-prompt change, no new event type, no new storage, no kernel/runtime/MCP-server changes.**

**Tech Stack:** Python 3.12 / FastAPI (memory pattern), `@openuidev/react-lang` + `@openuidev/react-ui` (React 19, already satisfied), vitest, pytest, i18n gen_types.

**Spec:** `docs/superpowers/specs/2026-07-13-openui-generative-ui-design.md`

## Global Constraints

- **Do not `git commit`.** The user reviews the working tree and commits themselves (repo convention: multi-step implementation commits once at the end — see `feedback_unified_commit_at_end`).
- Python 3.12–3.13; ruff line-length 100; host mypy (kernel `src.*` / `kernel.*` goes through `follow_imports=skip`, so accesses like `ctx.user_id` are consistent with memory and need no ignore).
- All host DB access goes through `infra/db.py`; this feature has **no** migration, **no** new table, **no** new HTTP endpoint, **no** OpenAPI change, **no** `make generate-types` (the tool is discovered via the MCP tool list; its result is a string).
- Phase 2 adds no new persistence: `emit_live_event` is live-only (not persisted) and reuses the existing `/api/v1/sessions/{id}/events?live_only=true`.
- Module boundaries: `modules/genui/` may import only `adapters/` (kernel_client), `infra/` (db/fs_registry/auth_context), `modules/providers/service.py`, `src.core` — **must not** import sibling modules' datastores (`scripts/check_module_boundaries.py` enforces this).
- Frontend: every visible string goes through `t()`, with zh-CN / en-US locales updated together; run `cd backend && uv run python ../i18n/scripts/gen_types.py` after changes. Phase 2 adds **no** new i18n keys (reuses Phase 1's `genui.*`).
- Quality gates (must pass before completion): `make test-all`, `make typecheck`, `make lint`; UI changes need browser-verify. Phase 2 must add no new mypy / boundary debt beyond pre-existing ones.

---

## File Structure

**New (backend) — Phase 1**
- `backend/valuz_agent/modules/genui/__init__.py` — empty package marker.
- `backend/valuz_agent/modules/genui/prompts.py` — `TOOL_DESCRIPTION`, `build_openui_prompt(request, data)`, `_load_library_prompt()`.
- `backend/valuz_agent/modules/genui/runner.py` — `_make_completer(...)`, `_resolve_provider_id(source)`.
- `backend/valuz_agent/modules/genui/tools.py` — `build_generative_ui_tool_defs()`, `_generate_ui_handler`.
- `backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt` — vendored generated artifact (committed, read at runtime).
- `backend/tests/modules/genui/test_prompt_asset.py` / `test_prompts.py` / `test_runner.py` / `test_tools.py`.

**New (backend) — Phase 2**
- `backend/valuz_agent/modules/genui/ids.py` — `resolve_tool_use_id` + `normalize_input` (pure functions; self-discover the tool_use_id from the calling session's tool_use events by input match).
- `backend/tests/modules/genui/test_ids.py`.

**New (frontend) — Phase 1**
- `frontend/packages/ui/scripts/gen_openui_prompt.mjs` — dev-only; regenerates the txt above from `@openuidev/react-ui/genui-lib`.
- `frontend/packages/ui/src/components/conversation/GenerativeUICard.tsx` — the shell card that mounts `<Renderer>`.
- `frontend/packages/ui/src/components/conversation/GenerativeUICard.test.tsx`.

**Modified — Phase 1**
- `backend/valuz_agent/boot/steps.py:~287,~303` — import + add to the `shared` tuple.
- `frontend/packages/ui/package.json` — add the two openuidev deps + a `gen:openui-prompt` script.
- `frontend/packages/ui/src/index.ts` — export `GenerativeUICard`.
- `frontend/packages/app/src/pages/ConversationPage.tsx:~2044` — add a `generate_ui` branch to `renderToolCall`.
- `i18n/locales/zh-CN.json` + `i18n/locales/en-US.json` — add the `genui` namespace.

**Modified — Phase 2**
- `backend/valuz_agent/modules/genui/runner.py` — `_make_completer` gains `calling_session_id` / `tool_use_id`; when set, subscribe to the ephemeral `text_delta` and forward via `emit_live_event`; run `run_turn` concurrently; `tool_use_id=None` stays synchronous.
- `backend/valuz_agent/modules/genui/tools.py` — handler calls `resolve_tool_use_id` to get R and passes it to the completer.
- `backend/tests/modules/genui/test_runner.py` / `test_tools.py` — streaming branch + handler pass-through tests.
- `frontend/packages/ui/src/components/conversation/GenerativeUICard.tsx` — `<Renderer isStreaming={status === "running"}>`.
- `frontend/packages/app/src/pages/ConversationPage.tsx` — `renderToolCall` lifts `generate_ui` to `GenerativeUICard` while running too.
- `frontend/packages/ui/src/components/conversation/GenerativeUICard.test.tsx` — `isStreaming` tests.

---

# Phase 1 — Synchronous generate-then-render

## Task 1: Frontend deps + generate the vendored OpenUI prompt

**Files:**
- Modify: `frontend/packages/ui/package.json` (deps + script)
- Create: `frontend/packages/ui/scripts/gen_openui_prompt.mjs`
- Create: `backend/valuz_agent/modules/genui/__init__.py`
- Create: `backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt` (generated by the script; must be committed)
- Test: `backend/tests/modules/genui/test_prompt_asset.py`

**Interfaces:**
- Produces: `openui_genui_lib_prompt.txt` (non-empty text, read by Task 2's `_load_library_prompt()`); `@openuidev/react-lang` / `@openuidev/react-ui` (used by Task 5).

- [ ] **Step 1: Add frontend deps and script**

Edit `frontend/packages/ui/package.json`:
- Add to `dependencies`:
  ```json
  "@openuidev/react-lang": "^0.5.0",
  "@openuidev/react-ui": "^0.5.0",
  ```
  (`^0.5.0` is the latest major at writing time; after install, trust the version `pnpm` actually resolves.)
- Add to `scripts`:
  ```json
  "gen:openui-prompt": "node scripts/gen_openui_prompt.mjs",
  ```

- [ ] **Step 2: Install deps**

Run: `pnpm install`
Expected: installs successfully; `frontend/packages/ui/node_modules/@openuidev/react-ui` exists.

- [ ] **Step 3: Write the generator script**

Create `frontend/packages/ui/scripts/gen_openui_prompt.mjs`:
```js
// Regenerates the vendored OpenUI genui-lib system prompt. Run after bumping
// @openuidev/react-ui. Output is loaded by the generate_ui tool at runtime.
//   pnpm --filter @valuz/ui gen:openui-prompt
import { openuiLibrary, openuiPromptOptions } from "@openuidev/react-ui/genui-lib";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../../../backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt");
writeFileSync(out, openuiLibrary.prompt(openuiPromptOptions));
console.log(`wrote ${out}`);
```

- [ ] **Step 4: Create the genui package + run the script to generate the txt**

Create `backend/valuz_agent/modules/genui/__init__.py` (empty file; a one-line module docstring is enough).

Run: `pnpm --filter @valuz/ui gen:openui-prompt`
Expected: console prints `wrote .../openui_genui_lib_prompt.txt`; the file `backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt` exists and is non-empty.

- [ ] **Step 5: Write the failing test**

Create `backend/tests/modules/genui/test_prompt_asset.py`:
```python
"""The vendored OpenUI prompt asset is present and loadable as a package resource."""

from importlib import resources


def test_library_prompt_asset_is_nonempty():
    text = (
        resources.files("valuz_agent.modules.genui")
        .joinpath("openui_genui_lib_prompt.txt")
        .read_text(encoding="utf-8")
    )
    assert len(text) > 200, "vendored OpenUI prompt looks empty — run gen:openui-prompt"
```

- [ ] **Step 6: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_prompt_asset.py -v`
Expected: PASS.

---

## Task 2: Backend `prompts.py` (pure-function prompt assembly)

**Files:**
- Create: `backend/valuz_agent/modules/genui/prompts.py`
- Test: `backend/tests/modules/genui/test_prompts.py`

**Interfaces:**
- Consumes: `openui_genui_lib_prompt.txt` (Task 1).
- Produces: `TOOL_DESCRIPTION: str`; `build_openui_prompt(request: str, data: object | None) -> str`; `GENERATIVE_UI_INSTRUCTIONS: str` (used by Task 3's completer as the ephemeral session's instructions).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/modules/genui/test_prompts.py`:
```python
"""genui prompt builder — pure function tests."""

from valuz_agent.modules.genui.prompts import (
    GENERATIVE_UI_INSTRUCTIONS,
    TOOL_DESCRIPTION,
    build_openui_prompt,
)


def test_build_prompt_splices_request_and_data():
    p = build_openui_prompt("a bar chart of Q1-Q4 sales", {"q1": 10, "q2": 20})
    assert "REQUEST:" in p
    assert "a bar chart of Q1-Q4 sales" in p
    assert '"q1": 10' in p
    # bundled library prompt is large
    assert len(p) > 500


def test_build_prompt_without_data():
    p = build_openui_prompt("just a table")
    assert "REQUEST:" in p
    assert "just a table" in p


def test_constants_are_set():
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()
    assert "OpenUI Lang" in GENERATIVE_UI_INSTRUCTIONS
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_prompts.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Write the implementation**

Create `backend/valuz_agent/modules/genui/prompts.py`:
```python
"""OpenUI generative-UI prompt assembly.

The ``genui-lib`` system prompt is vendored as a package asset (regenerated by
``frontend/packages/ui/scripts/gen_openui_prompt.mjs``); here we splice it with
the agent's request (+ optional data) into the single user turn sent to the
ephemeral generative-UI session.
"""

from __future__ import annotations

import json
from importlib import resources

TOOL_DESCRIPTION = (
    "Generate a rich, interactive UI — charts, tables, forms, KPI cards, or a "
    "dashboard — when a visual component would communicate the answer more "
    "clearly than prose. Pass a natural-language `request` describing what to "
    "show, and optional `data` (structured values to render directly). Do NOT "
    "call this for ordinary Q&A that text answers well. The tool returns OpenUI "
    "Lang that the client renders inline; do not repeat the same content as "
    "text afterwards."
)

GENERATIVE_UI_INSTRUCTIONS = (
    "You generate user interfaces in OpenUI Lang. Output ONLY valid OpenUI Lang "
    "using the components described in the library below — no prose, no code "
    "fences, no explanations. Render the requested information directly into the "
    "components."
)


def _load_library_prompt() -> str:
    return (
        resources.files(__package__)
        .joinpath("openui_genui_lib_prompt.txt")
        .read_text(encoding="utf-8")
    )


def build_openui_prompt(request: str, data: object | None = None) -> str:
    parts = [
        GENERATIVE_UI_INSTRUCTIONS,
        "",
        _load_library_prompt(),
        "",
        "REQUEST:",
        request.strip(),
    ]
    if data is not None:
        parts.append("")
        parts.append("DATA (render these values directly into the components):")
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_prompts.py -v`
Expected: PASS (3 cases).

---

## Task 3: Backend `runner.py` (ephemeral-session completer)

**Files:**
- Create: `backend/valuz_agent/modules/genui/runner.py`
- Test: `backend/tests/modules/genui/test_runner.py`

**Interfaces:**
- Consumes: `GENERATIVE_UI_INSTRUCTIONS` (Task 2); `kernel_client`, `fs_registry`, `app.schemas` (CreateSessionRequest / AgentConfigSchema / ModelProviderInputSchema).
- Produces: `_make_completer(*, user_id: str, runtime_provider: Any, model: str, mp: Any) -> Callable[[str], Awaitable[str]]`; `_resolve_provider_id(source: Any) -> str | None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/modules/genui/test_runner.py`:
```python
"""genui runner — ephemeral-session completer tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
import valuz_agent.modules.genui.runner as r
from valuz_agent.modules.genui.runner import _resolve_provider_id


def test_resolve_provider_id_prefers_locked():
    src = SimpleNamespace(
        metadata={"valuz": {"locked_provider_id": "p1"}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p1"


def test_resolve_provider_id_falls_back_to_agent_config():
    src = SimpleNamespace(
        metadata={"valuz": {}},
        agent_config=SimpleNamespace(metadata={"provider_id": "p2"}),
    )
    assert _resolve_provider_id(src) == "p2"


def test_resolve_provider_id_none_when_missing():
    src = SimpleNamespace(metadata={"valuz": {}}, agent_config=SimpleNamespace(metadata={}))
    assert _resolve_provider_id(src) is None


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Stub kernel_client + fs_registry so _make_completer runs without a kernel."""
    monkeypatch.setattr(
        r.fs_registry, "data_dir", lambda user_id: tmp_path / "app"
    )

    captured: dict = {}

    async def _create(user_id, req):
        captured["req"] = req

    async def _run_turn(user_id, sid, prompt):
        captured["prompt"] = prompt
        return SimpleNamespace(assistant_message="Chart\n  data: 1,2,3")

    async def _delete(user_id, sid):
        captured.setdefault("deleted", []).append(sid)

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    return captured


async def test_completer_builds_ephemeral_session_and_returns_text(patched):
    completer = r._make_completer(
        user_id="u1", runtime_provider="claude_agent", model="claude-sonnet-4-6", mp=None
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    req = patched["req"]
    assert req.id  # ephemeral id set
    assert req.model == "claude-sonnet-4-6"
    assert req.runtime_provider == "claude_agent"
    assert req.model_provider is None  # mp=None → OAuth-style self-auth
    assert req.metadata == {"valuz": {"ephemeral_generative_ui": True}}
    assert "OpenUI Lang" in req.instructions
    assert patched["prompt"] == "PROMPT"
    assert patched["deleted"] == [req.id]  # cleanup ran
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_runner.py -v`
Expected: FAIL (module/functions do not exist).

- [ ] **Step 3: Write the implementation**

Create `backend/valuz_agent/modules/genui/runner.py`:
```python
"""Generative-UI ephemeral-session completer — the LLM-call seam.

Mirrors ``modules/memory/runner.py::_make_completer``: a throwaway no-tools
kernel session cloning the calling session's resolved runtime/provider/model,
one ``run_turn`` returning OpenUI Lang, then delete + rmtree. Best-effort by
contract — failures bubble to the tool handler, which converts them to an
error result without affecting the originating turn.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.genui.prompts import GENERATIVE_UI_INSTRUCTIONS

logger = logging.getLogger(__name__)

Completer = Callable[[str], Awaitable[str]]


def _resolve_provider_id(source: Any) -> str | None:
    """Provider id for the ephemeral session: prefer the host-stamped
    ``valuz.locked_provider_id`` (chat/project), fall back to the embedded
    agent config's ``metadata.provider_id`` (task lead)."""
    valuz = (getattr(source, "metadata", None) or {}).get("valuz", {}) or {}
    pid = valuz.get("locked_provider_id")
    if pid:
        return str(pid)
    ac = getattr(source, "agent_config", None)
    meta = (getattr(ac, "metadata", None) or {}) if ac is not None else {}
    pid = meta.get("provider_id")
    return str(pid) if pid else None


def _make_completer(
    *, user_id: str, runtime_provider: Any, model: str, mp: Any
) -> Completer:
    """Build the ``complete`` seam backed by a throwaway no-tools kernel session
    cloning the source's runtime/provider/model."""

    async def _complete(prompt: str) -> str:
        from app.schemas import AgentConfigSchema, CreateSessionRequest, ModelProviderInputSchema

        # OAuth/subscription channels (Codex/Claude login) resolve to mp=None and
        # carry no static key — create the session with model_provider=None so the
        # runtime self-authenticates, exactly like the source session.
        mp_schema = (
            ModelProviderInputSchema(
                base_url=mp.base_url, api_key=mp.api_key, api_protocol=mp.api_protocol
            )
            if (mp is not None and getattr(mp, "api_key", None))
            else None
        )
        ephem_id = uuid4().hex
        gen_cwd = fs_registry.data_dir(user_id) / "generative-ui" / ephem_id
        gen_cwd.mkdir(parents=True, exist_ok=True)
        marker = {"valuz": {"ephemeral_generative_ui": True}}
        req = CreateSessionRequest(
            id=ephem_id,
            agent_config=AgentConfigSchema(
                name="generative-ui",
                model=model,
                runtime_provider=runtime_provider,
                instructions=GENERATIVE_UI_INSTRUCTIONS,
                metadata=marker,
            ),
            cwd=str(gen_cwd),
            runtime_provider=runtime_provider,
            model=model,
            model_provider=mp_schema,
            instructions=GENERATIVE_UI_INSTRUCTIONS,
            permission_mode="default",
            metadata=marker,
        )
        await kernel_client.create_session(user_id, req)
        try:
            msg = await kernel_client.run_turn(user_id, ephem_id, prompt)
            return msg.assistant_message or ""
        finally:
            try:
                await kernel_client.delete_session(user_id, ephem_id)
            except Exception:  # noqa: BLE001
                logger.debug("generative-ui: ephemeral session cleanup failed")
            shutil.rmtree(gen_cwd, ignore_errors=True)

    return _complete
```

> **Phase 2 note:** Task 9 replaces this `_make_completer` (and its inner `_complete`) with the streaming version that uses a single shared scratch cwd (`fs_registry.generative_ui_cwd`) and forwards `text_delta`. Keep `_resolve_provider_id` unchanged.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_runner.py -v`
Expected: PASS (4 cases).

---

## Task 4: Backend `tools.py` (the `generate_ui` tool) + boot registration

**Files:**
- Create: `backend/valuz_agent/modules/genui/tools.py`
- Modify: `backend/valuz_agent/boot/steps.py:~287` (import) + `~303` (add to `shared`)
- Test: `backend/tests/modules/genui/test_tools.py`

**Interfaces:**
- Consumes: `build_openui_prompt`, `TOOL_DESCRIPTION` (Task 2); `_make_completer`, `_resolve_provider_id` (Task 3); `kernel_client.get_session`; `resolve_model_provider_for_user`.
- Produces: `build_generative_ui_tool_defs() -> tuple[ToolDef, ...]` (consumed by boot); `GENERATIVE_UI_TOOL_NAME = "generate_ui"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/modules/genui/test_tools.py`:
```python
"""generate_ui tool — handler + def tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
import valuz_agent.modules.genui.tools as t
from src.core.tools import ExecContext
from valuz_agent.modules.genui.tools import build_generative_ui_tool_defs


def _ctx(session_id="s1", user_id="u1"):
    ctx = ExecContext(session_id=session_id)
    ctx.user_id = user_id  # HostExecContext adds this at runtime
    return ctx


@pytest.fixture
def patched(monkeypatch):
    async def _get_session(user_id, sid):
        return SimpleNamespace(
            model="claude-sonnet-4-6",
            runtime_provider="claude_agent",
            metadata={"valuz": {"locked_provider_id": "p1"}},
            agent_config=SimpleNamespace(metadata={}),
        )

    async def _resolve(**kw):
        return SimpleNamespace(base_url=None, api_key="k", api_protocol="anthropic")

    monkeypatch.setattr(t.kernel_client, "get_session", _get_session)
    monkeypatch.setattr(t, "resolve_model_provider", _resolve)

    async def _fake_completer(prompt):
        return "Chart\n  data: 5,10"

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _fake_completer)


async def test_handler_returns_openui_lang(patched):
    defs = build_generative_ui_tool_defs()
    handler = defs[0].handler
    res = await handler({"request": "sales chart"}, _ctx())
    assert res.is_error is False
    assert res.content == "Chart\n  data: 5,10"


async def test_handler_requires_request(patched):
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "   "}, _ctx())
    assert res.is_error is True
    assert "request" in res.content


async def test_handler_passes_data_into_prompt(monkeypatch, patched):
    seen = {}

    async def _comp(prompt):
        seen["prompt"] = prompt
        return "Table"

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _comp)
    handler = build_generative_ui_tool_defs()[0].handler
    await handler({"request": "table", "data": {"rows": [1, 2]}}, _ctx())
    assert "rows" in seen["prompt"]


async def test_handler_no_session(patched, monkeypatch):
    async def _none(user_id, sid):
        return None

    monkeypatch.setattr(t.kernel_client, "get_session", _none)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "x"}, _ctx(session_id=""))
    assert res.is_error is True


async def test_handler_empty_output_is_error(monkeypatch, patched):
    async def _blank(prompt):
        return "   "

    monkeypatch.setattr(t, "_make_completer", lambda **kw: _blank)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "x"}, _ctx())
    assert res.is_error is True


def test_tool_def_shape():
    defs = build_generative_ui_tool_defs()
    assert len(defs) == 1
    assert defs[0].name == "generate_ui"
    assert defs[0].handler is not None
    assert defs[0].parameters["required"] == ["request"]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_tools.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Write the implementation**

Create `backend/valuz_agent/modules/genui/tools.py`:
```python
"""generative-UI in-process MCP tool — the ``generate_ui`` tool.

Registered in the host toolkit MCP ``base`` toolset (runtime-agnostic). The
handler resolves the caller's runtime/provider/model from the calling session,
builds the OpenUI prompt (vendored genui-lib + request + optional data), runs
one ephemeral no-tools LLM call via the memory-pattern completer, and returns
the OpenUI Lang as the tool result — which the frontend renders with OpenUI's
``<Renderer>``. Best-effort: every failure becomes an ``is_error`` result.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION, build_openui_prompt
from valuz_agent.modules.genui.runner import _make_completer, _resolve_provider_id
from valuz_agent.modules.providers.service import (
    resolve_model_provider_for_user as resolve_model_provider,
)

logger = logging.getLogger(__name__)

GENERATIVE_UI_TOOL_NAME = "generate_ui"

_PARAMS = {
    "type": "object",
    "properties": {
        "request": {
            "type": "string",
            "description": (
                "Natural-language description of the UI to generate — intent, "
                "layout, and what to show."
            ),
        },
        "data": {
            "type": "object",
            "description": "Optional structured values to render directly into the components.",
            "additionalProperties": True,
        },
    },
    "required": ["request"],
}


async def _generate_ui_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    request = args.get("request")
    data = args.get("data")
    if not request or not str(request).strip():
        return ToolResult(content="generate_ui: 'request' is required", is_error=True)

    source = (
        await kernel_client.get_session(user_id, ctx.session_id) if ctx.session_id else None
    )
    if source is None:
        return ToolResult(
            content="generate_ui: no active session to resolve a model from",
            is_error=True,
        )

    provider_id = _resolve_provider_id(source)
    model = source.model
    runtime_provider = source.runtime_provider
    if not provider_id or not model:
        return ToolResult(
            content="generate_ui: could not resolve a model channel for this session",
            is_error=True,
        )

    try:
        mp = await resolve_model_provider(
            user_id=user_id,
            provider_id=str(provider_id),
            model_id=model,
            runtime_provider=runtime_provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: provider resolve failed", exc_info=True)
        return ToolResult(
            content=f"generate_ui: model channel unavailable ({exc})", is_error=True
        )

    completer = _make_completer(
        user_id=user_id, runtime_provider=runtime_provider, model=model, mp=mp
    )
    try:
        openui = await completer(build_openui_prompt(str(request), data))
    except Exception as exc:  # noqa: BLE001
        logger.debug("generate_ui: generation failed", exc_info=True)
        return ToolResult(content=f"generate_ui: generation failed ({exc})", is_error=True)

    openui = (openui or "").strip()
    if not openui:
        return ToolResult(
            content="generate_ui: model returned no OpenUI Lang", is_error=True
        )
    return ToolResult(content=openui, is_error=False)


def build_generative_ui_tool_defs() -> tuple[ToolDef, ...]:
    """Build the ``generate_ui`` tool def (live handler) for the host toolkit MCP server."""
    td = ToolDef(
        name=GENERATIVE_UI_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_generate_ui_handler,
        read_only=False,
    )
    logger.info("Built generative-ui tool def: %s", GENERATIVE_UI_TOOL_NAME)
    return (td,)
```

> **Phase 2 note:** Task 10 changes the `completer = _make_completer(...)` block to first resolve `tool_use_id` and pass `calling_session_id` / `tool_use_id` through.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_tools.py -v`
Expected: PASS (6 cases).

- [ ] **Step 5: boot registration (import)**

Next to the existing memory import in `backend/valuz_agent/boot/steps.py` (~L287), add:
```python
from valuz_agent.modules.genui.tools import build_generative_ui_tool_defs
```

- [ ] **Step 6: boot registration (add to the `shared` tuple)**

Append one line to the `shared = ( ... )` tuple (~L303) in `backend/valuz_agent/boot/steps.py`:
```python
    shared = (
        build_memory_tool_defs()
        + build_project_instructions_tool_defs()
        + build_submit_skill_tool_defs()
        + build_agent_proposal_tool_defs()
        + build_deliver_artifacts_tool_defs()
        + build_generative_ui_tool_defs()
    )
```

- [ ] **Step 7: Run the backend gate**

Run: `cd backend && uv run pytest tests/modules/genui/ -v && uv run ruff check valuz_agent/modules/genui/ && uv run mypy valuz_agent/modules/genui/`
Expected: all tests PASS; ruff / mypy clean.

---

## Task 5: Frontend i18n keys + `GenerativeUICard` component

**Files:**
- Modify: `i18n/locales/zh-CN.json`, `i18n/locales/en-US.json` (add the `genui` namespace)
- Create: `frontend/packages/ui/src/components/conversation/GenerativeUICard.tsx`
- Modify: `frontend/packages/ui/src/index.ts` (export)
- Test: `frontend/packages/ui/src/components/conversation/GenerativeUICard.test.tsx`

**Interfaces:**
- Consumes: `Renderer` from `@openuidev/react-lang`, `openuiLibrary` from `@openuidev/react-ui/genui-lib` (installed in Task 1); `useTranslation` from `@valuz/core` (the consumer passes `t`, or the component uses a hook — this component lives in `@valuz/ui`, so use `useI18n` from `../../hooks/use-i18n`).
- Produces: `GenerativeUICard({ openui?: string; status?: "running"|"success"|"error" })` — consumed by Task 6's `renderToolCall` override.

- [ ] **Step 1: Add i18n keys**

Add a `genui` namespace to the top-level object in `i18n/locales/zh-CN.json` (sibling to the other namespaces):
```json
"genui": {
  "cardTitle": "生成式 UI",
  "empty": "尚无内容",
  "generating": "正在生成界面…",
  "error": "生成失败"
},
```
Add the same keys to `i18n/locales/en-US.json` (English values):
```json
"genui": {
  "cardTitle": "Generative UI",
  "empty": "Nothing to show yet",
  "generating": "Generating UI…",
  "error": "Generation failed"
},
```

- [ ] **Step 2: Regenerate types**

Run: `cd backend && uv run python ../i18n/scripts/gen_types.py`
Expected: the `I18nKey` union includes `genui.*`.

- [ ] **Step 3: Write the failing test**

Create `frontend/packages/ui/src/components/conversation/GenerativeUICard.test.tsx`:
```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@openuidev/react-lang", () => ({
  Renderer: (props: { response: string }) => (
    <div data-testid="renderer">{props.response}</div>
  ),
}));

import { GenerativeUICard } from "./GenerativeUICard";

describe("GenerativeUICard", () => {
  it("renders the OpenUI Renderer with the openui payload", () => {
    render(<GenerativeUICard openui={"Chart\n  data: 1"} />);
    expect(screen.getByTestId("renderer").textContent).toBe("Chart\n  data: 1");
  });

  it("shows an empty state when there is no output yet", () => {
    render(<GenerativeUICard openui={undefined} status="running" />);
    expect(screen.getByTestId("genui-empty")).toBeTruthy();
  });
});
```

- [ ] **Step 4: Run the test and confirm it fails**

Run: `cd frontend && pnpm --filter @valuz/ui test -- GenerativeUICard`
Expected: FAIL (component does not exist).

- [ ] **Step 5: Write the implementation**

Create `frontend/packages/ui/src/components/conversation/GenerativeUICard.tsx`:
```tsx
import { Renderer } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

import { useI18n } from "../../hooks/use-i18n";
import { Spinner } from "../ui/spinner";

export interface GenerativeUICardProps {
  /** OpenUI Lang string — the generate_ui tool's output. */
  openui?: string;
  /** Tool status; "running" while the tool hasn't returned yet. */
  status?: "running" | "success" | "error";
}

/**
 * Renders the OpenUI Lang produced by the ``generate_ui`` MCP tool as live,
 * interactive components. Mounted inline via ``ConversationPage``'s
 * ``renderToolCall`` override (the same lift-out seam AskUserQuestion and
 * submit_skill use).
 */
export function GenerativeUICard({ openui, status }: GenerativeUICardProps) {
  const { t } = useI18n();
  const body = (openui ?? "").trim();

  return (
    <div
      data-slot="generative-ui-card"
      className="rounded-xl border border-surface-border bg-surface overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-border">
        <span className="text-sm font-medium text-ink-heading">
          {t("genui.cardTitle" as Parameters<typeof t>[0])}
        </span>
        {status === "running" && <Spinner className="size-3.5" />}
      </div>
      <div className="p-3">
        {body ? (
          <Renderer library={openuiLibrary} response={body} isStreaming={false} />
        ) : (
          <div
            data-testid="genui-empty"
            className="flex items-center gap-2 text-sm text-ink-meta"
          >
            {status === "running" ? (
              <>
                <Spinner className="size-3.5" />
                {t("genui.generating" as Parameters<typeof t>[0])}
              </>
            ) : (
              t("genui.empty" as Parameters<typeof t>[0])
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

> If `../ui/spinner`'s actual export name/path differs (check under `packages/ui/src/components/ui/`), adjust the import accordingly. `rounded-xl` / `border-surface-border` / `bg-surface` / `text-ink-heading` / `text-ink-meta` are all existing repo semantic tokens (see `frontend/CLAUDE.md` "UI Component Spec"). Task 11 changes `isStreaming={false}` to `isStreaming={status === "running"}`.

- [ ] **Step 6: Export the component**

Next to the existing `export * from "./components/conversation/..."` line in `frontend/packages/ui/src/index.ts`, add:
```ts
export * from "./components/conversation/GenerativeUICard";
```

- [ ] **Step 7: Run the test and confirm it passes**

Run: `cd frontend && pnpm --filter @valuz/ui test -- GenerativeUICard`
Expected: PASS (2 cases).

---

## Task 6: Frontend `renderToolCall` override wiring

**Files:**
- Modify: `frontend/packages/app/src/pages/ConversationPage.tsx` (import + `renderToolCall` branch)
- Test: reuse the existing ConversationPage test convention (if router/page-level tests cannot assert the override directly, then under the assumption that the `GenerativeUICard` unit test already covers rendering, this task's gate is typecheck + manual browser-verify).

**Interfaces:**
- Consumes: `GenerativeUICard` (Task 5); `isToolNamed` (same file L487).
- Produces: a `generate_ui` tool call is lifted to `<GenerativeUICard>`; other tools unchanged.

> **First investigate (spec §6.3 open question):** confirm how `generate_ui` is reliably identified and how `submit_skill` / `AskUserQuestion` are identified today (likely a `title` / `subtitle` naming convention or a derived `kind`, since `PrototypeToolCall.kind` has no raw MCP tool name). Implement the branch to match whatever convention the codebase uses; `isToolNamed(name, "generate_ui")` below assumes `name` resolves to the MCP tool name.

- [ ] **Step 1: Add the import**

Add `GenerativeUICard` to the existing `from "@valuz/ui"` import block (~L117) in `frontend/packages/app/src/pages/ConversationPage.tsx` (alphabetically, or nearby).

- [ ] **Step 2: Add the branch to `renderToolCall`**

Inside `renderToolCall` (defined at L2044), after the `AskUserQuestion` branch and before the `propose_agent` branch, add:
```tsx
      // generate_ui — generative UI. The MCP tool returns OpenUI Lang as
      // ``tool.output``; render it with OpenUI's <Renderer> via
      // GenerativeUICard. Fall through to the generic tool card on error so the
      // failure stays visible.
      if (isToolNamed(name, "generate_ui")) {
        const openui = tool.output;
        return <GenerativeUICard openui={openui} status={openui ? "success" : "running"} />;
      }
```
(Branch placement: anywhere after `const name = tool.title || "";`, as long as it is before the function's final "fall through → default ToolCallCard".)

> **Phase 2 note:** Task 11 rewrites this branch to also lift while running and only fall back on `error`.

- [ ] **Step 3: typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS (the `t("genui.*")` key types were generated in Task 5 Step 2).

---

## Task 7: Full quality gate + manual browser-verify (Phase 1)

**Files:** none new.

- [ ] **Step 1: Backend full suite**

Run: `cd backend && uv run pytest tests/modules/genui/ -v && uv run ruff check valuz_agent/modules/genui/ && uv run mypy valuz_agent/modules/genui/`
Expected: all PASS.

- [ ] **Step 2: Repo-wide gate**

Run: `make test-all && make typecheck && make lint`
Expected: all PASS (CLAUDE.md requires all three green for completion).

- [ ] **Step 3: Start dev and verify manually**

Run: `./scripts/dev.sh` (backend on :8000 + the desktop dev shell)
In a conversation, prompt the agent to trigger a rich UI (e.g. "show a sample sales dataset as a chart") and confirm:
1. the agent called `generate_ui` (the tool card or the lifted GenerativeUICard appears);
2. after the tool returns, `<Renderer>` renders an interactive component;
3. after refreshing, the persisted tool result still re-renders (history replay is not lost).

> Per CLAUDE.md: "Browser-verify any UI change before it goes into a release build."

- [ ] **Step 4: Hand back to the user**

Report: Phase 1 implementation complete, all gates pass, browser-verified; **do not commit**. Let the user review the working tree and commit themselves (repo convention: one commit for the multi-step implementation).

---

# Phase 2 — Streaming render

## Task 8: `ids.py` — tool_use_id self-discovery (pure functions, TDD)

**Files:**
- Create: `backend/valuz_agent/modules/genui/ids.py`
- Test: `backend/tests/modules/genui/test_ids.py`

**Interfaces:**
- Produces: `normalize_input(value: Any) -> str`; `resolve_tool_use_id(*, user_id: str, session_id: str, arguments: dict[str, Any]) -> str | None` (consumed by Task 10's handler). Reads events via `kernel_client.get_events_window(user_id, session_id, turn_limit=20) -> EventWindowData` (field `.items: list[EventData]`; EventData has `.type` / `.data`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/modules/genui/test_ids.py`:
```python
"""genui ids — tool_use_id discovery by input fingerprint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.modules.genui.ids as ids


def test_normalize_input_sorts_keys_and_ignores_order():
    assert ids.normalize_input({"b": 1, "a": 2}) == ids.normalize_input({"a": 2, "b": 1})


def test_normalize_input_empty():
    assert ids.normalize_input(None) == ids.normalize_input({}) == "{}"


def _ev(type_: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(type=type_, data=data)


@pytest.fixture
def patched(monkeypatch):
    captured: dict = {}

    async def _window(user_id, session_id, *, turn_limit=20):
        captured["args"] = (user_id, session_id, turn_limit)
        return SimpleNamespace(items=captured.pop("events", []))

    monkeypatch.setattr(ids.kernel_client, "get_events_window", _window)
    return captured


async def test_resolves_by_matching_input(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "OTHER", "name": "generate_ui", "input": {"request": "other"}}),
        _ev("tool_use", {"id": "R1", "name": "generate_ui", "input": {"request": "chart", "data": {"x": 1}}}),
    ]
    r = await ids.resolve_tool_use_id(
        user_id="u1", session_id="s1", arguments={"data": {"x": 1}, "request": "chart"}
    )
    assert r == "R1"  # input fingerprint hit (order-independent)


async def test_recency_tiebreak_on_identical_input(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "OLD", "name": "generate_ui", "input": {"request": "same"}}),
        _ev("tool_use", {"id": "NEW", "name": "generate_ui", "input": {"request": "same"}}),
    ]
    r = await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "same"})
    assert r == "NEW"  # take the most recent


async def test_ignores_other_tools(patched):
    patched["events"] = [
        _ev("tool_use", {"id": "X", "name": "memory", "input": {"request": "chart"}}),
    ]
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "chart"}) is None


async def test_no_session_returns_none():
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="", arguments={"request": "x"}) is None


async def test_get_events_window_failure_returns_none(patched, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(ids.kernel_client, "get_events_window", _boom)
    assert await ids.resolve_tool_use_id(user_id="u1", session_id="s1", arguments={"request": "x"}) is None
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_ids.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Write the implementation**

Create `backend/valuz_agent/modules/genui/ids.py`:
```python
"""Resolve the calling tool_use id for a generate_ui invocation.

The host toolkit MCP server's handler gets ``(tool_name, arguments)`` but NOT
the runtime's tool_use id — the MCP ``@server.call_tool()`` decorator drops
``_meta``/``progressToken``. To key streamed ``tool_output_delta`` events to the
right frontend card we recover the id by matching the tool INPUT: the runtime
persists a ``tool_use`` event (carrying ``input``) on the calling session
before invoking the tool, and the handler received the same arguments. Distinct
concurrent calls have distinct inputs -> deterministic match; identical inputs
tiebreak by recency (identical output either way).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client

logger = logging.getLogger(__name__)


def normalize_input(value: Any) -> str:
    """Canonical JSON for an MCP arguments blob (sorted keys). Lets us compare
    the handler's ``arguments`` against a tool_use event's ``input`` without
    tripping on key order or None."""
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False, default=str)


async def resolve_tool_use_id(
    *, user_id: str, session_id: str, arguments: dict[str, Any]
) -> str | None:
    """The tool_use id of the generate_ui call that produced ``arguments`` on
    ``session_id``, or None if it can't be determined (caller then skips
    streaming and renders synchronously). Reads the recent calling-session
    event window, filters generate_ui tool_use blocks, matches by normalized
    input, tiebreaks by recency (last match wins). Best-effort: any failure ->
    None."""
    if not session_id:
        return None
    try:
        window = await kernel_client.get_events_window(user_id, session_id, turn_limit=20)
    except Exception:  # noqa: BLE001
        logger.debug("generate_ui: resolve_tool_use_id get_events_window failed", exc_info=True)
        return None

    target = normalize_input(arguments)
    match: str | None = None
    for ev in getattr(window, "items", None) or []:
        if getattr(ev, "type", None) != "tool_use":
            continue
        data = getattr(ev, "data", None) or {}
        if data.get("name") != "generate_ui":
            continue
        if normalize_input(data.get("input")) != target:
            continue
        eid = data.get("id")
        if eid:
            match = str(eid)  # keep last (most recent) match
    return match
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_ids.py -v`
Expected: PASS (6 cases).

---

## Task 9: `_make_completer` streaming branch (`runner.py`, TDD)

**Files:**
- Modify: `backend/valuz_agent/modules/genui/runner.py`
- Test: `backend/tests/modules/genui/test_runner.py`

**Interfaces:**
- Consumes: `kernel_client.subscribe_session_events(user_id, session_id) -> AsyncIterator[EventData]` (a sync function returning an async iterator), `kernel_client.emit_live_event(user_id, session_id, type, data)`.
- Produces: `_make_completer(*, user_id, runtime_provider, model, mp, calling_session_id=None, tool_use_id=None) -> Completer` (consumed by Task 10's handler). `text_delta` event: `event.data["text"]` is the token.

- [ ] **Step 1: Append the streaming tests to `test_runner.py`**

Append to `backend/tests/modules/genui/test_runner.py` (and extend the `patched` fixture with subscribe/emit stubs — see Step 1b):

Step 1a — append the cases:
```python
async def test_completer_streams_text_deltas_to_calling_session(patched):
    """When tool_use_id is set, subscribe to the ephemeral text_delta and forward
    it to the calling session as tool_output_delta (keyed by tool_use_id);
    run_turn's full text is still the return value."""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id="R1",
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"  # run_turn full text (canonical)
    forwarded = patched["forwarded"]  # [(calling_session, type, data), ...]
    assert forwarded == [
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "root "}),
        ("calling-sid", "tool_output_delta", {"id": "R1", "text": "= Stack()"}),
    ]
    assert patched["deleted"] == ["ephem-1"]  # cleanup still runs


async def test_completer_sync_when_no_tool_use_id(patched):
    """tool_use_id=None -> no subscribe, no forward, pure synchronous (same as Phase 1)."""
    completer = r._make_completer(
        user_id="u1",
        runtime_provider="claude_agent",
        model="claude-sonnet-4-6",
        mp=None,
        calling_session_id="calling-sid",
        tool_use_id=None,
    )
    out = await completer("PROMPT")
    assert out == "Chart\n  data: 1,2,3"
    assert patched["forwarded"] == []
    assert patched["subscribed"] == []  # did not subscribe
```

Step 1b — update the `patched` fixture to add subscribe + emit stubs (replacing the existing fixture's `_create`/`_run_turn`/`_delete` section so it pins the ephem id for assertions):
```python
@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Stub kernel_client + fs_registry so _make_completer runs without a kernel."""
    monkeypatch.setattr(r.fs_registry, "data_dir", lambda user_id: tmp_path / "app")

    captured: dict = {"forwarded": [], "subscribed": [], "deleted": []}

    async def _create(user_id, req):
        captured["req"] = req
        captured.setdefault("create_reqs", []).append(req)

    async def _run_turn(user_id, sid, prompt):
        captured["prompt"] = prompt
        return SimpleNamespace(assistant_message="Chart\n  data: 1,2,3")

    async def _delete(user_id, sid):
        captured["deleted"].append(sid)

    async def _gen():
        for d in ({"text": "root "}, {"text": "= Stack()"}):
            yield SimpleNamespace(type="text_delta", data=d)
        yield SimpleNamespace(type="assistant_message", data={"text": "Chart\n  data: 1,2,3"})

    def _subscribe(user_id, sid):
        captured["subscribed"].append(sid)
        return _gen()

    async def _emit(user_id, sid, type_, data):
        captured["forwarded"].append((sid, type_, data))

    monkeypatch.setattr(r.kernel_client, "create_session", _create)
    monkeypatch.setattr(r.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(r.kernel_client, "delete_session", _delete)
    monkeypatch.setattr(r.kernel_client, "subscribe_session_events", _subscribe)
    monkeypatch.setattr(r.kernel_client, "emit_live_event", _emit)
    return captured
```

> Note: the Phase-1 case `test_completer_builds_ephemeral_session_and_returns_text` also uses this fixture — it asserts `patched["req"]`, `patched["deleted"] == [req.id]`, and `patched["prompt"] == "PROMPT"`; the new fixture still provides those fields, so that case needs no change. (`captured.setdefault("create_reqs", []).append(req)` is kept so any future multi-create assertions still work.)

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_runner.py -v`
Expected: the new cases FAIL (`_make_completer() got an unexpected keyword argument 'calling_session_id'`).

- [ ] **Step 3: Modify `runner.py` — add imports + the streaming branch**

3a. Add `asyncio` and `contextlib` to the import block at the top of `runner.py` (existing: `import logging` + `from collections.abc import Awaitable, Callable` + `from typing import Any` + `from uuid import uuid4`):
```python
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4
```

3b. Replace the entire `_make_completer` function (and its inner `_complete`) with the version below (leave the existing `_resolve_provider_id` unchanged):
```python
def _make_completer(
    *,
    user_id: str,
    runtime_provider: Any,
    model: str,
    mp: Any,
    calling_session_id: str | None = None,
    tool_use_id: str | None = None,
) -> Completer:
    """Build the ``complete`` seam backed by a throwaway no-tools kernel session
    cloning the source's runtime/provider/model. Each call is a fresh ephemeral
    session (deleted after), sharing ONE fixed scratch cwd
    (``FsRegistry.generative_ui_cwd``).

    When ``calling_session_id`` + ``tool_use_id`` are set, the ephemeral
    session's ``text_delta`` stream is forwarded to the CALLING session as
    ``tool_output_delta`` (keyed by ``tool_use_id``) via the existing
    ``kernel_client.emit_live_event`` live-injection channel, so the frontend
    ``<Renderer isStreaming>`` paints progressively. ``run_turn`` still returns
    the full text as the canonical ToolResult. When either is None, behaves as
    the synchronous (non-streaming) version."""

    async def _forward_deltas(ephem_id: str) -> None:
        try:
            async for ev in kernel_client.subscribe_session_events(user_id, ephem_id):
                if getattr(ev, "type", None) != "text_delta":
                    continue
                text = (getattr(ev, "data", None) or {}).get("text")
                if not text:
                    continue
                await kernel_client.emit_live_event(
                    calling_session_id or "",
                    "tool_output_delta",
                    {"id": tool_use_id, "text": text},
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort; canonical full text still wins
            logger.debug("generative-ui: delta forwarding stopped", exc_info=True)

    async def _complete(prompt: str) -> str:
        from app.schemas import AgentConfigSchema, CreateSessionRequest, ModelProviderInputSchema

        mp_schema = (
            ModelProviderInputSchema(
                base_url=mp.base_url, api_key=mp.api_key, api_protocol=mp.api_protocol
            )
            if (mp is not None and getattr(mp, "api_key", None))
            else None
        )
        ephem_id = uuid4().hex
        gen_cwd = fs_registry.generative_ui_cwd(user_id)
        marker = {"valuz": {"ephemeral_generative_ui": True}}
        req = CreateSessionRequest(
            id=ephem_id,
            agent_config=AgentConfigSchema(
                name="generative-ui",
                model=model,
                runtime_provider=runtime_provider,
                instructions=GENERATIVE_UI_INSTRUCTIONS,
                metadata=marker,
            ),
            cwd=str(gen_cwd),
            runtime_provider=runtime_provider,
            model=model,
            model_provider=mp_schema,
            instructions=GENERATIVE_UI_INSTRUCTIONS,
            permission_mode="default",
            metadata=marker,
        )
        await kernel_client.create_session(user_id, req)
        stream_task: asyncio.Task[None] | None = None
        if calling_session_id and tool_use_id:
            # Subscribe before run_turn: text_delta is live-only and not
            # persisted, so the subscription must be attached before the turn
            # emits. ``sleep(0)`` lets the task begin attaching its tap.
            stream_task = asyncio.create_task(_forward_deltas(ephem_id))
            await asyncio.sleep(0)
        try:
            msg = await kernel_client.run_turn(user_id, ephem_id, prompt)
            return msg.assistant_message or ""
        finally:
            if stream_task is not None:
                stream_task.cancel()
                with contextlib.suppress(BaseException):
                    await stream_task
            try:
                await kernel_client.delete_session(user_id, ephem_id)
            except Exception:  # noqa: BLE001
                logger.debug("generative-ui: ephemeral session cleanup failed")

    return _complete
```

> This swaps the Phase-1 per-ephemeral cwd (`data_dir(...) / "generative-ui" / ephem_id` + `rmtree`) for a single shared scratch cwd (`fs_registry.generative_ui_cwd(user_id)`), so the per-call `mkdir`/`rmtree` are dropped. If `generative_ui_cwd` is not yet on `FsRegistry`, add it (a fixed subdir under `data_dir`) — the boundary checker still passes since `fs_registry` is an allowed import.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/test_runner.py -v`
Expected: all PASS (the 2 new streaming cases + the existing cases).

- [ ] **Step 5: ruff + mypy**

Run: `cd backend && uv run ruff check valuz_agent/modules/genui/ && uv run mypy valuz_agent/modules/genui/runner.py`
Expected: ruff clean; mypy reports no errors on `runner.py` (ignore existing follow-imports noise).

---

## Task 10: handler passes R through (`tools.py`, TDD)

**Files:**
- Modify: `backend/valuz_agent/modules/genui/tools.py`
- Test: `backend/tests/modules/genui/test_tools.py`

**Interfaces:**
- Consumes: Task 8's `resolve_tool_use_id`, Task 9's `_make_completer(..., calling_session_id=, tool_use_id=)`.

- [ ] **Step 1: Append the handler tests**

Append to `backend/tests/modules/genui/test_tools.py` (the top already has `import valuz_agent.modules.genui.tools as t`):
```python
async def test_handler_resolves_tool_use_id_and_streams(monkeypatch, patched):
    """The handler resolves R and passes calling_session_id + tool_use_id to the completer."""
    captured: dict = {}

    async def _resolve(*, user_id, session_id, arguments):
        captured["resolve_args"] = (user_id, session_id, arguments)
        return "R-FOUND"

    completer_calls: dict = {}

    async def _comp(prompt):
        completer_calls["prompt"] = prompt
        return "Chart"

    def _make(**kw):
        completer_calls["kw"] = kw
        return _comp

    monkeypatch.setattr(t, "resolve_tool_use_id", _resolve)
    monkeypatch.setattr(t, "_make_completer", _make)
    handler = build_generative_ui_tool_defs()[0].handler
    res = await handler({"request": "chart"}, _ctx())
    assert res.content == "Chart" and res.is_error is False
    assert completer_calls["kw"]["calling_session_id"] == _ctx().session_id  # "s1"
    assert completer_calls["kw"]["tool_use_id"] == "R-FOUND"


async def test_handler_falls_back_to_sync_when_no_R(monkeypatch, patched):
    async def _none(**kw):
        return None

    completer_calls: dict = {}

    def _make(**kw):
        completer_calls["kw"] = kw

        async def _comp(prompt):
            return "Chart"

        return _comp

    monkeypatch.setattr(t, "resolve_tool_use_id", _none)
    monkeypatch.setattr(t, "_make_completer", _make)
    handler = build_generative_ui_tool_defs()[0].handler
    await handler({"request": "chart"}, _ctx())
    assert completer_calls["kw"]["tool_use_id"] is None
    assert completer_calls["kw"]["calling_session_id"] is None
```

> `_ctx()` is the existing one (`ExecContext(session_id="s1")` + `ctx.user_id="u1"`). The `patched` fixture already monkeypatches `kernel_client.get_session` / `resolve_model_provider` / `_make_completer`; these cases additionally override `_make_completer` and `resolve_tool_use_id`.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd backend && uv run pytest tests/modules/genui/test_tools.py -v`
Expected: the new cases FAIL (`_make_completer` does not receive `calling_session_id` / `tool_use_id`, or `resolve_tool_use_id` does not exist).

- [ ] **Step 3: Modify `tools.py` — import + call**

3a. Add to the `tools.py` import block (Task 8's module):
```python
from valuz_agent.modules.genui.ids import resolve_tool_use_id
```
(Add this line at the end of the existing import section; `_make_completer`, `_resolve_provider_id` are already imported from runner.)

3b. Replace the block in `_generate_ui_handler` that builds the completer:
```python
    completer = _make_completer(
        user_id=user_id, runtime_provider=runtime_provider, model=model, mp=mp
    )
```
with:
```python
    tool_use_id = await resolve_tool_use_id(
        user_id=user_id, session_id=ctx.session_id, arguments=args
    )
    completer = _make_completer(
        user_id=user_id,
        runtime_provider=runtime_provider,
        model=model,
        mp=mp,
        calling_session_id=ctx.session_id if tool_use_id else None,
        tool_use_id=tool_use_id,
    )
```
(`args` is the handler's `args: dict[str, Any]` parameter — already the MCP arguments as-is; `resolve_tool_use_id` normalizes internally.)

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd backend && uv run pytest tests/modules/genui/ -v`
Expected: all PASS (test_ids + test_runner + test_tools + others).

- [ ] **Step 5: ruff + mypy + module boundaries**

Run: `cd backend && uv run ruff check valuz_agent/modules/genui/ && uv run mypy valuz_agent/modules/genui/ && uv run python scripts/check_module_boundaries.py`
Expected: ruff clean; genui mypy clean; the boundary check does not list genui as a violator.

---

## Task 11: Frontend — `isStreaming` + lift while running (TDD)

**Files:**
- Modify: `frontend/packages/ui/src/components/conversation/GenerativeUICard.tsx`
- Modify: `frontend/packages/app/src/pages/ConversationPage.tsx` (renderToolCall branch)
- Test: `frontend/packages/ui/src/components/conversation/GenerativeUICard.test.tsx`

**Interfaces:**
- Consumes: Phase-1 `GenerativeUICard` / `extractContentText` / the existing `renderToolCall` structure; the frontend's existing `tool.call.output_delta` accumulation (unchanged).
- Produces: `GenerativeUICard` uses `<Renderer isStreaming>` when `status="running"`, and is also lifted by `renderToolCall` while running (no fall-back to the default card).

- [ ] **Step 1: Write / update the tests**

1a. Update the existing mocked Renderer in `GenerativeUICard.test.tsx` to capture `isStreaming`:
```tsx
vi.mock("@openuidev/react-lang", () => ({
  Renderer: (props: { response: string; isStreaming?: boolean }) => (
    <div data-testid="renderer" data-streaming={props.isStreaming ? "true" : "false"}>
      {props.response}
    </div>
  ),
}));
```
1b. Append the cases:
```tsx
  it("renders in streaming mode while running", () => {
    render(<GenerativeUICard openui={"Chart\n  data: 1"} status="running" />);
    const r = screen.getByTestId("renderer");
    expect(r.getAttribute("data-streaming")).toBe("true");
    expect(r.textContent).toBe("Chart\n  data: 1");
  });

  it("renders non-streaming on success", () => {
    render(<GenerativeUICard openui={"Chart"} status="success" />);
    expect(screen.getByTestId("renderer").getAttribute("data-streaming")).toBe("false");
  });
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd frontend && pnpm exec vitest run packages/ui/src/components/conversation/GenerativeUICard.test.tsx`
Expected: the streaming case FAILS (`isStreaming` is currently always `false`).

- [ ] **Step 3: Modify GenerativeUICard — drive isStreaming from status**

Change `<Renderer library={openuiLibrary} response={body} isStreaming={false} />` to:
```tsx
          <ThemeProvider
            lightTheme={VALUZ_OPENUUI_THEME}
            cssSelector="[data-slot='generative-ui-card']"
          >
            <Renderer
              library={openuiLibrary}
              response={body}
              isStreaming={status === "running"}
            />
          </ThemeProvider>
```
(Everything else unchanged. While running and `body` is empty, the "generating" placeholder branch still applies — existing logic.)

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd frontend && pnpm exec vitest run packages/ui/src/components/conversation/GenerativeUICard.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Modify ConversationPage — lift while running too**

In `renderToolCall`, change the existing generate_ui branch:
```tsx
      if (isToolNamed(name, "generate_ui")) {
        if (tool.status === "error" || !tool.output) return null;
        return <GenerativeUICard openui={tool.output} status="success" />;
      }
```
to (lift while running too; only error falls back to the default card to show the failure):
```tsx
      if (isToolNamed(name, "generate_ui")) {
        if (tool.status === "error") return null;
        return (
          <GenerativeUICard
            openui={tool.output}
            status={tool.status === "running" ? "running" : "success"}
          />
        );
      }
```

- [ ] **Step 6: typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: all 8 packages PASS.

---

## Task 12: Full quality gate + manual browser-verify (Phase 2)

**Files:** none new.

- [ ] **Step 1: Backend full suite**

Run: `cd backend && uv run pytest tests/modules/genui/ -v && uv run ruff check valuz_agent/modules/genui/ && uv run mypy valuz_agent/modules/genui/`
Expected: all PASS.

- [ ] **Step 2: Repo-wide gate**

Run: `make test-all && make typecheck && make lint`
Expected: no new failures beyond pre-existing debt (genui mypy 0 errors, boundaries 0 new violations, genui tests all pass). **Note:** the repo has pre-existing mypy 344 errors, `files→projects` boundary violations, and skills/providers test-isolation failures — these are unrelated to this change.

- [ ] **Step 3: Start dev and verify streaming manually**

Run: `./scripts/dev.sh`
In a conversation, prompt the agent to trigger a rich UI (e.g. "show sample sales data as a chart") and confirm:
1. after `generate_ui` is called, **it is no longer just a spinner** — OpenUI Lang appears in the card as it is generated (`<Renderer isStreaming>` paints token by token);
2. on completion, the UI stabilizes with the canonical full text (consistent with, or more complete than, the stream);
3. after refreshing, the persisted `tool_result` full text still re-renders (history replay is consistent).

> Per CLAUDE.md: "Browser-verify any UI change".

- [ ] **Step 4: Hand back to the user**

Report: Phase 2 streaming complete, gates pass, browser-verified; **do not commit**. Let the user decide on branch / commit / PR.

---

## Self-Review (plan ↔ spec)

**Phase 1 coverage:**
- Spec §3 "generate-then-render" → Task 5/6 (`<Renderer isStreaming={false}>`). ✓
- Spec §3 "agent self-invocation" → Task 4 `TOOL_DESCRIPTION` (Task 2) teaches it; main prompt untouched. ✓
- Spec §3 "built-in genui-lib" → Task 1 generates the vendored prompt; Task 5 uses `openuiLibrary`. ✓
- Spec §3 "backend model call = memory completer" → Task 3 `_make_completer`. ✓
- Spec §3 "base toolset registration" → Task 4 Step 5-6. ✓
- Spec §3 "frontend renderToolCall override" → Task 6. ✓
- Spec §3 "frontend component home @valuz/ui" → Task 5. ✓
- Spec §3 "system-prompt source = vendoring + dev script" → Task 1. ✓
- Spec §7 error handling (no provider / LLM failure / empty output / recursion) → Task 4 handler branches + Task 3 marker. ✓
- Spec §9 testing → Tasks 1-6 are all TDD. ✓
- Spec §10 out-of-scope (streaming / custom library / composer force / settings switch / pin UI) → not touched by Phase 1 (streaming is Phase 2). ✓

**Phase 2 coverage:**
- Spec §2.2 emit-channel reuse → Task 9 uses `emit_live_event` (no new kernel code). ✓
- Spec §2.3 / §5.7 tool_use_id discovery by input + recency tiebreak → Task 8 `resolve_tool_use_id` + `normalize_input`, 6 tests (match / tiebreak / other tools / no session / failure). ✓
- Spec §4.2 data flow (handler → resolve R → completer streaming → canonical) → Task 9 + Task 10. ✓
- Spec §5.5 handler passes R → Task 10. ✓
- Spec §5.6 completer subscribe + concurrent run_turn + cleanup → Task 9 (`asyncio.create_task` + `cancel`/`suppress` + finally delete). ✓
- Spec §5 file table `ids.py` pure functions + unit tests → Task 8. ✓
- Spec §6.4 `isStreaming={running}` → Task 11 Step 3. ✓
- Spec §6.4 lift while running → Task 11 Step 5. ✓
- Spec §6.5 accumulation unchanged (frontend existing) → the plan does not modify `conversation-utils`, only reuses it. ✓
- Spec §8 http/remote: `emit_live_event` / `subscribe_session_events` both go through `_kernel_for`, kernel unchanged → Architecture section + Global Constraints. ✓
- Spec §7 degradation ladder: R=None → synchronous (Task 9 `if calling_session_id and tool_use_id`); subscribe/emit failure best-effort (Task 9 `_forward_deltas` except); canonical full-text fallback (Task 9 `run_turn` return). ✓
- Spec §9 testing → every task is TDD. ✓

**Placeholder scan:** no TBD/TODO; every step has full code or an exact command. `@openuidev/*` versions are annotated "trust the resolved version at install time" (not a placeholder). The Spinner import path has a "adjust to actual" fallback note (verifiable, not a placeholder). The Phase-2 `generative_ui_cwd` addition is called out explicitly (verifiable).

**Type consistency:** `_make_completer` / `_resolve_provider_id` / `build_openui_prompt` / `GENERATIVE_UI_INSTRUCTIONS` / `TOOL_DESCRIPTION` / `build_generative_ui_tool_defs` / `GENERATIVE_UI_TOOL_NAME` keep consistent signatures across tasks; `GenerativeUICard({ openui, status })` is defined in Task 5 and consumed in Task 6 / Task 11 with matching prop names; `isToolNamed(name, "generate_ui")` matches the existing repo helper. Phase 2: `resolve_tool_use_id(*, user_id, session_id, arguments) -> str | None` (Task 8) ↔ the handler call (Task 10) signature matches; `_make_completer(*, user_id, runtime_provider, model, mp, calling_session_id=None, tool_use_id=None)` (Task 9) ↔ the handler call (Task 10) kwargs match; `text_delta` field `data["text"]`, `tool_use` fields `data["name"/"input"/"id"]`, `EventWindowData.items`, `EventData.type/data` are consistent throughout. Runner-test filename normalized to `test_runner.py` across both phases.

**Scope:** a single subsystem (the generative-UI tool + its render), 12 tasks across two phases, each independently verifiable.
