# OpenUI Generative UI — Design

> **Status:** Design confirmed; pending implementation.
> **Dates:** Phase 1 (synchronous) 2026-07-13 · Phase 2 (streaming) 2026-07-14
> **Scope:** Let in-conversation agents generate rich UI on demand (charts / tables / forms / dashboards) and render it inline as interactive components in the message stream. Phase 1 ships generate-then-render; Phase 2 upgrades it to token-by-token streaming. Built on the OpenUI [`genui-lib`](https://www.openui.com/docs/openui-lang) component library and `<Renderer>` runtime.
> **Implementation plan:** `docs/superpowers/plans/2026-07-13-openui-generative-ui.md`

---

## 1. Background & Goals

Today an agent's in-conversation output has only two forms: natural-language text
(markdown, rendered via Streamdown) and read-only tool-call cards (`ToolCallCard`,
which shows only input/output text). When an agent wants to deliver a chart, a
fillable form, a set of KPIs, or a filterable table, it can only degrade to a
markdown table or ASCII — neither interactive nor attractive.

### Phase 1 goal (synchronous)

Give the agent a **generative-UI capability**. When the agent judges that a rich UI
would help more than prose, it calls a built-in MCP tool; the backend runs one LLM
call with the OpenUI component-library system prompt to produce OpenUI Lang; the
frontend renders it inline with OpenUI's official `<Renderer>` as an interactive
component.

Three committed directions (see §3 decision table):

1. **Generate-then-render** — the tool returns the complete OpenUI Lang in one shot;
   the frontend renders with `<Renderer isStreaming={false}>`. No streaming in v1.
2. **Agent self-invocation** — teach the agent *when* to call purely through the tool
   description; do not change the main system prompt; add no composer force-entry.
3. **Built-in `genui-lib`** — use the default library shipped with
   `@openuidev/react-ui` directly; build no Valuz custom library.

### Phase 2 goal (streaming)

Upgrade `generate_ui` from "render after generation" to "render while generating".
The ephemeral session's `text_delta` is forwarded in real time into the calling
session as `tool_output_delta`, and the frontend `<Renderer isStreaming>` paints the
UI token by token. The Phase-1 canonical fallback is unchanged: completion /
reconnect / replay still treat the final full text as the source of truth.

---

## 2. Core Insights

### 2.1 The memory pattern (Phase 1)

The OpenUI docs' "standard usage" is to inject the component library's generated
system prompt into the main agent and let the main agent stream the DSL itself.
**We deliberately do not take that path.** Instead we use an isolated built-in MCP
tool + a one-shot LLM call, for two reasons: (1) it makes "generate UI" a reusable
atomic capability the main agent can call at any time; (2) it isolates DSL
generation inside a one-shot ephemeral call so it never pollutes the main
conversation's system prompt or cache.

**This structure is already proven in this repo — it is the `memory` module:**

- `backend/valuz_agent/modules/memory/tools.py` registers a `memory` tool into the
  host toolkit MCP's `base` toolset — runtime-agnostic (claude / codex / deepagents
  all use it).
- `backend/valuz_agent/modules/memory/runner.py::_make_completer` (L76-129) is the
  "call the LLM" seam: it opens an **ephemeral kernel session**, clones the source
  session's `runtime_provider` / `provider` / `model`, and runs
  `kernel_client.run_turn(...)` to get back `msg.assistant_message`. Pure
  request/response, no streaming, best-effort, fully isolated.
- `backend/valuz_agent/modules/memory/extraction.py` makes prompt construction /
  parsing into pure functions; `complete` is injected as a
  `Callable[[str], Awaitable[str]]`, so model/provider selection is swappable.

**Therefore the OpenUI integration is almost a copy of the memory pattern:** a
`generate_ui` tool → `_make_completer` one-shot model call (with the OpenUI
component-library system prompt) → returns the OpenUI Lang string as
`ToolResult.content` → the frontend mounts `<Renderer>` through the existing
`renderToolCall` override seam.

**Key constraint: do not touch the main system prompt, add no new event type, add
no new storage.** OpenUI Lang is just a tool-result string and reuses the entire
existing tool-call chain (`tool.call.completed` event → `PrototypeToolCall.output`).

Rejected alternatives:

- **B. Inject into the main system prompt** (let the main agent emit the DSL
  directly) — conflicts with "agent self-invokes a tool; main prompt untouched".
- **C. Main agent emits the DSL as message text**, MarkdownContent parses a
  ```` ```openui ```` fence — conflicts with "backend MCP tool calls the model" (the
  main agent has no genui-lib prompt).

### 2.2 The host→kernel injection channel already exists (Phase 2)

`kernel_client.emit_live_event(user_id, session_id, type, data)`:

- Protocol `adapters/kernel_client.py:181-183`; inprocess `:384-399`; http
  `adapters/kernel_client_http.py:258-267`
  (`POST /api/v1/sessions/{id}/events?live_only=true`); kernel route
  `kernel/app/routes/sessions.py:366-404`; module facade `kernel_client.py:730-731`
  (routed through `_kernel_for(user_id)`, fleet/allocator-aware).
- **Broadcasts only to SSE subscribers; not persisted** (no seq). `tool_output_delta`
  is already translated by `event_sse_adapter._translate_kernel_event` into
  `tool.call.output_delta` (`:357-368`): `data["id"] → tool_use_id`,
  `data["text"] → text`. **The frontend receives it with zero changes.**
- live-only (seq=None) is already handled in SSE (`event_sse_adapter.py:698-715`).
- Tests: `tests/adapters/test_kernel_client_subprocess.py`,
  `tests/adapters/test_events_stream_dedup.py`,
  `tests/adapters/test_kernel_client_contract.py`. Existing callers:
  `modules/tasks/actor_runner.py:290`, `modules/sessions/service.py:1626`.

**→ The injection infrastructure is ready-made. The handler simply calls
`emit_live_event(calling_session, "tool_output_delta", {"id": R, "text": delta})`.
Zero changes to kernel / runtime / MCP server.**

### 2.3 tool_use_id is not in the MCP request → self-discover via input fingerprint (Phase 2)

- The MCP `@server.call_tool()` decorator drops `_meta` / `progressToken`, so
  `_call_tool(tool_name, arguments)` gets only name + arguments
  (`integrations/toolkit_mcp_server.py:152-160`). None of the three runtimes
  forwards tool_use_id into the MCP request (codex is itself an MCP client; the host
  cannot inject a header).
- **But the tool_use event carries its own input:** `event_sse_adapter.py:203-212`
  shows the kernel `tool_use` event data = `{id, name, input}`, and it is
  **already persisted** (the runtime emits tool_use before invoking the tool).
- The handler side receives the **same arguments** (the MCP input).
  `kernel_client.get_events(user_id, calling_session_id)` (`:185` / `:401`) reads
  historical events.
- **→ R = the id of the generate_ui tool_use on the calling session whose input
  matches the handler arguments.** Match on a content fingerprint, not on timing —
  **deterministic under concurrency** (see §5.6).

---

## 3. Design Decisions

| Dimension | Decision | Rationale |
|---|---|---|
| Render timing (P1) | Generate-then-render, `<Renderer isStreaming={false}>` | Faithfully reproduces memory's synchronous completer; streaming would require bridging an in-process tool's LLM output into the calling session's event stream — costly, deferred to Phase 2 |
| Render timing (P2) | Stream deltas, `<Renderer isStreaming>` | Real-time feedback; canonical full text still wins on completion |
| Trigger | Agent self-invocation, taught only via the tool description | Leaves the main system prompt untouched; keeps prompt-cache stable |
| Component library | Built-in `genui-lib` (`@openuidev/react-ui`) | Fastest path; backend vendors the prompt this library generates |
| Backend · model call | Reuse memory's `_make_completer` ephemeral-session pattern | Runtime-agnostic; OAuth subscription channels (`mp is None`) work natively; best-effort |
| Backend · registration | host toolkit MCP `base` toolset, resident at boot | Sibling to memory / submit_skill, available on every ordinary session |
| Frontend · carrier | OpenUI Lang as a tool result, via the existing `renderToolCall` override | No new event type / storage; reuses the AskUserQuestion / submit_skill pattern of "lifting a specific tool to a custom UI" |
| Frontend · component home | `@valuz/ui` (`packages/ui`) | Conversation product components live in `@valuz/ui`, shared by desktop + webui (repo rule) |
| system-prompt source | Backend vendors the generated prompt text; a dev-only script regenerates it | No runtime Node dependency; avoids hand-written drift |
| Streaming channel (P2) | Reuse the existing `emit_live_event` (host→kernel live injection) | Spike confirms it exists, is tested, and works in both modes; zero kernel changes |
| delta type (P2) | `tool_output_delta` → frontend's existing `tool.call.output_delta` accumulation | Translation/accumulation already in place; near-zero component-side change |
| tool_use_id source (P2) | Self-discovery: match the calling session's tool_use by input (arguments) fingerprint | MCP does not forward the id; input is present on both sides; deterministic under concurrency |
| Canonical fallback (P2) | `run_turn`'s full text still returned as `ToolResult` | Reconnect / replay / streaming failure all use the final full text; behaves like Phase 1 |
| Deployment modes (P2) | inprocess / http / remote all supported | Both `emit_live_event` and `subscribe_session_events` go through `_kernel_for`; kernel stays the sole event authority |
| Degradation (P2) | R discovery fails / subscribe fails → silent fallback to synchronous render | best-effort; never affects the originating turn |

---

## 4. Data Flow

### 4.1 Phase 1 (synchronous)

```
user turn → main agent runs
  └─ agent decides a rich UI helps → calls mcp__harness__generate_ui({ request, data? })
        │  (host toolkit MCP, "base" toolset, in-process, runtime-agnostic)
        ▼
     handler (modules/genui/tools.py):
       1. Parse the caller's runtime_provider / locked_provider_id / model from
          session.metadata.valuz (same seam as memory: kernel_client.get_session +
          runner._lead_provider_id pattern)
       2. prompt = VENDORED genui-lib prompt + <request> + (optional data JSON)
       3. openui_lang = await _make_completer(...)(prompt)   (ephemeral session, run_turn)
       4. return ToolResult(content=openui_lang)             (failure → is_error=True)
        ▼
     tool result → existing tool.call.completed event → frontend (no new event type)
        ▼
  ConversationPage renderToolCall override:
     match generate_ui → <GenerativeUICard openui={tc.output} />
                         └─ <Renderer library={openuiLibrary} response={tc.output} isStreaming={false} />
```

### 4.2 Phase 2 (streaming)

```
agent calls generate_ui(request, data)
  → runtime emits tool_use(generate_ui, id=R, input={request,data}) to the calling
    session (already persisted)
  → runtime invokes the host generate_ui handler via MCP (arguments={request,data}, ctx)

handler(ctx, args):
  1. R = resolve_tool_use_id(user_id, ctx.session_id, args)        # §5.6 self-discovery
     (not found → take the synchronous path, no streaming)
  2. create the ephemeral session (clone r/p/m, same as Phase 1)
  3. subscribe_session_events(ephem)                               # get live text_delta
  4. concurrently:
       - background: for text_delta in subscription:
             emit_live_event(ctx.session_id, "tool_output_delta",
                             {"id": R, "text": delta.text})
       - main:       openui_full = await run_turn(ephem, prompt)
  5. return ToolResult(content=openui_full)                        # canonical fallback

frontend (calling session's SSE):
  tool.call.output_delta(id=R, text=…) → conversation-utils accumulates into tool(R).output
  → <GenerativeUICard> reads streaming.output, isStreaming=true, paints as it grows
  tool.call.completed(id=R, content=full text) → replaced by canonical full text
    (consistent on replay / reconnect)
```

---

## 5. Backend Design

A new module `backend/valuz_agent/modules/genui/` (layout mirrors `modules/memory/`):

| File | Phase | Responsibility |
|---|---|---|
| `tools.py` | P1 | `build_generative_ui_tool_defs() -> tuple[ToolDef, ...]`, tool name `generate_ui`. Params: `request: str` (required, the natural-language intent of the UI to generate), `data?: object` (optional structured values to render). The handler parses the source session's r/p/m, builds the prompt, calls the completer, returns `ToolResult(content=openui_lang)`. All failure paths set `is_error=True` — best-effort, never affects the originating turn. |
| `runner.py` | P1 | `_make_completer(*, user_id, runtime_provider, model, mp)` — a near-verbatim copy of `memory/runner.py:76-129`; `instructions` becomes "You generate UI in OpenUI Lang. Output ONLY OpenUI Lang, no explanatory text.", the marker becomes `metadata.valuz.ephemeral_generative_ui = True`, with a recursion guard. Reuses `resolve_model_provider_for_user`; OAuth channels (`mp is None`) are expected (ephemeral session self-authenticates). |
| `prompts.py` | P1 | `TOOL_DESCRIPTION` (teach the main agent *when* to call — charts/tables/forms/dashboards scenarios) and `build_openui_prompt(request, data)` (splice the vendored library prompt + request + optional data). |
| `openui_genui_lib_prompt.txt` | P1 | The vendored output of `openuiLibrary.prompt(openuiPromptOptions)`. Read once at module load. |
| `ids.py` | P2 | `resolve_tool_use_id` + `normalize_input` (pure functions; self-discover the tool_use_id from the calling session's tool_use events by input match). |

### 5.1 Tool schema (Phase 1, draft)

```jsonc
{
  "type": "object",
  "properties": {
    "request": { "type": "string", "description": "Natural-language description of the UI to generate (intent, layout, what to show)." },
    "data":    { "type": "object", "description": "Optional structured values, spliced into the prompt as JSON for the UI to render directly.", "additionalProperties": true }
  },
  "required": ["request"]
}
```

The tool description (`TOOL_DESCRIPTION`) must be explicit: call **only when a rich
UI (chart/table/form/dashboard/KPI) is more helpful than text**; do not call for
ordinary Q&A; after calling, treat the rendered result as the display to the user —
do not repeat the same content as text.

### 5.2 Boot registration (Phase 1)

`backend/valuz_agent/boot/steps.py` existing assembly (L282-324):

```python
base = (
    <orchestration launchers>
    + build_memory_tool_defs()        # L302
    + build_submit_skill_tool_defs() # L304
)
install_toolkit_toolsets(base=base, lead=base + build_task_tool_defs(...))
```

Add one line `+ build_generative_ui_tool_defs()` to the `base` tuple. `base` is
attached to every ordinary session across all runtimes, so claude / codex /
deepagents are all covered.

### 5.3 system-prompt source (the only real trade-off)

The `genui-lib` prompt is generated by JS (`openuiLibrary.prompt(openuiPromptOptions)`),
but the model-calling code is Python. Decision: **vendor the generated prompt text
at build time; a dev-only Node script regenerates it.**

- `modules/genui/openui_genui_lib_prompt.txt` — the generated full text, committed.
- `scripts/gen_openui_prompt.mjs` (dev-only):

  ```js
  import { openuiLibrary, openuiPromptOptions } from "@openuidev/react-ui/genui-lib";
  import { writeFileSync } from "node:fs";
  writeFileSync("backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt",
                openuiLibrary.prompt(openuiPromptOptions));
  ```

  Run it manually when the OpenUI version is bumped and commit the result. Document
  this flow in the module README.
- **Do not** use runtime Node (keep the backend pure Python, same isolation
  principle as the browser-engine sidecar); **do not** hand-port (the genui-lib
  prompt is large and version-coupled — vendoring the real output avoids drift).

### 5.4 provider / model resolution

Copied verbatim from `memory/runner.py`:

- chat session: `provider_id` from `session.metadata.valuz.locked_provider_id`,
  `model` from `session.model`.
- task lead session: via `runner._lead_provider_id` (first `valuz.locked_provider_id`,
  falling back to `agent_config.metadata.provider_id`).
- `resolve_model_provider_for_user(...)` re-resolves credentials (the wired
  `SessionData` carries no key); `mp is None` is expected for OAuth subscription
  channels (Codex/Claude login) — the ephemeral session is created with
  `model_provider=None` and the runtime self-authenticates.
- quick chat with no locked provider/model → `is_error`, the agent degrades to text.

### 5.5 handler passes R through (Phase 2)

- Current state: resolves r/p/m → `_make_completer(...)` → `completer(prompt)` → ToolResult.
- Change: before calling the completer, run
  `R = await resolve_tool_use_id(user_id, ctx.session_id, args)`; pass `R` and
  `calling_session_id` (`ctx.session_id`) to the completer. When R is None, still
  take the completer (synchronous, no streaming).
- best-effort: if `resolve_tool_use_id` raises, log at debug, set R=None, continue.

### 5.6 runner streaming branch (Phase 2)

- Signature gains `*, calling_session_id: str | None, tool_use_id: str | None`.
- `tool_use_id is None`: keep current behavior (synchronous, do not forward deltas).
- `tool_use_id` non-empty: after `create_session(ephem)` and before `run_turn`,
  `subscribe_session_events(user_id, ephem_id)`; a concurrent task consumes the
  subscription and, for each `text_delta` event, calls
  `await kernel_client.emit_live_event(calling_session_id, "tool_output_delta",
  {"id": tool_use_id, "text": <delta>})`; the main task `await run_turn(...)`;
  in `finally`, cancel the subscription task and delete the ephem session.
- Coalescing: `text_delta` is high-frequency; a light merge (accumulate N chars or
  M ms before emitting) can reduce the `emit_live_event` call rate. Implement direct
  forwarding first; add coalescing only if a perf issue appears (the kernel-side
  `delta_coalescing_sink` also buffers).
- `subscribe_session_events` is HTTP SSE in http mode and **must** connect before
  `run_turn` (text_delta is live-only, not persisted — missed deltas cannot be
  recovered).

### 5.7 tool_use_id self-discovery (Phase 2, concurrency-safe)

`resolve_tool_use_id(user_id, session_id, args) -> str | None`:

1. `events = await kernel_client.get_events(user_id, session_id)` (or
   `get_events_window` for the recent batch).
2. Filter: `type == "tool_use"` and `data.name == "generate_ui"`.
3. Among candidates, find the one whose `normalize(data.input) == normalize(args)`;
   if several, take the **most recent by timestamp** (the latest before the handler
   started).
4. Return its `data.id`; return `None` if no match (→ degrade to synchronous).

**Concurrency safety:**

- Two concurrent `generate_ui` calls in the same turn → two tool_use events with
  **different inputs** → each matches its own R exactly, zero ambiguity.
- Two concurrent calls with **identical inputs** → input matches both → timestamp
  tiebreak takes the latest; and identical input → identical UI → which card it
  sticks to is visually indistinguishable.
- `normalize`: compare both sides' JSON with sorted keys, tolerating key-order
  differences. A mismatch → degrade to synchronous (no streaming, no error).

**Timing guarantee:** the runtime persists tool_use before invoking the tool (via
`PersistThenBroadcastSink`), so by the time the handler runs the tool_use is already
in the store and `get_events` is guaranteed to read this invocation's row.

---

## 6. Frontend Design

### 6.1 Dependencies (Phase 1)

`packages/ui`'s `package.json` gains:

```jsonc
"@openuidev/react-lang": "<latest>",
"@openuidev/react-ui":   "<latest>"
```

Placed in `@valuz/ui` rather than `packages/app`: conversation product components
live in `@valuz/ui` (shared by desktop + webui; see `frontend/CLAUDE.md`). Peer
deps React 19 + zustand 5 — already satisfied (`packages/ui` react ^19.2, zustand
^5.0.8).

### 6.2 GenerativeUICard (Phase 1)

`packages/ui/src/components/conversation/GenerativeUICard.tsx`:

```tsx
import { Renderer } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

export function GenerativeUICard({ openui, streaming }: { openui?: string; streaming?: boolean }) {
  // openui = the tool result (OpenUI Lang string)
  if (!openui?.trim()) return <GenerativeUIEmpty />;   // loading / empty
  return (
    <div className="generative-ui-card /* same shell as ToolCallCard */">
      <header>… {t("genui.cardTitle")} …</header>
      <Renderer library={openuiLibrary} response={openui} isStreaming={!!streaming} />
    </div>
  );
}
```

- v1 renders synchronously, `streaming=false`.
- Shell matches `ToolCallCard` (title + collapsible + loading/error/empty states).
- All visible strings go through `t()` (new i18n namespace `genui.*`, zh-CN / en-US
  updated together — see `frontend/CLAUDE.md`).
- Edge cases: tool not finished (no output) → loading; `status === "error"` → fall
  back to the default `ToolCallCard` so the failure stays visible; malformed OpenUI
  Lang → `<Renderer>` parses line by line and renders tolerantly.

### 6.3 renderToolCall override (Phase 1)

`packages/app/src/pages/ConversationPage.tsx`'s `renderToolCall` (defined ~L2044,
wired ~L5778) gains a branch: a `generate_ui` tool call → render
`<GenerativeUICard openui={tc.output} />` instead of the default tool card. Same
"lift-out" path as AskUserQuestion / submit_skill (`tool-overridden` DisplayBlock,
`ConversationTurnList.tsx:212/359/415`).

> **Plan-time open (confirm at implementation):** `PrototypeToolCall`
> (`frontend/packages/shared/src/types/conversation.ts:8`) has only
> `{ id, kind, title, subtitle?, status, input?, output? }`; `kind` is a fixed enum
> `kb|fetch|skill|file|bash` with **no raw MCP tool name**. So how to reliably
> identify `generate_ui` (and how submit_skill / AskUserQuestion identify today)
> must be checked at implementation time — most likely a naming convention on
> `title` / `subtitle` or a derived `kind`. The plan lists this as the first
> investigation step.

### 6.4 isStreaming + lift during running (Phase 2)

`GenerativeUICard`: currently `isStreaming={false}`, renders only when output is
present. Change: drive `isStreaming={status === "running"}`;
`response={extractContentText(openui)}` (`openui` is the tool's `output`, fed during
running by the frontend-accumulated `streaming.output`, painted as it grows; replaced
by the canonical full text on completion). Empty + running still shows the
"generating" placeholder.

`ConversationPage`'s `renderToolCall` override: currently lifts `generate_ui` to
`GenerativeUICard` only when `tool.output` is non-empty and not an error, otherwise
`return null` (falls back to the default card). Change: **also lift while running**
(`status === "running"` and name=generate_ui →
`<GenerativeUICard openui={tool.output} status="running" />`), so the streamed
output renders token-by-token in the card. error / no output and not running still
`return null` (default card shows the error).

### 6.5 delta accumulation (Phase 2, unchanged)

`conversation-utils`'s existing `tool.call.output_delta` (id=R) accumulation is
**not touched** — it already concatenates deltas into
`activeToolCalls.get(R).output`, and that tool card is exactly the `GenerativeUICard`
lifted by R.

---

## 7. Error Handling

| Case | Behavior |
|---|---|
| Cannot resolve provider/model (quick chat with no locked channel) | `ToolResult(is_error=True, content="generate_ui: cannot resolve a model channel…")`, agent degrades to text |
| LLM call fails / times out | ephemeral session cleaned in `finally` (same as memory), `is_error=True`, best-effort, does not affect the originating turn |
| Empty / whitespace-only OpenUI Lang returned | Soft error: `is_error=True`, note the model produced no usable UI |
| Recursion | ephemeral session marked `ephemeral_generative_ui=True`, and it is a no-tools review-style session — structurally cannot call `generate_ui` again |
| Malformed OpenUI Lang | `<Renderer>` parses line-by-line tolerantly, partial render; the frontend does not crash |
| `resolve_tool_use_id` cannot find R (no matching tool_use / event not yet arrived) (P2) | R=None, take the synchronous generate-then-render path (no streaming), no error |
| `resolve_tool_use_id` raises (P2) | Log at debug, R=None, continue synchronously |
| ephemeral `subscribe` fails / `emit_live_event` fails (P2) | best-effort swallowed (a single lost delta is harmless); `run_turn`'s full text still returned as the ToolResult |
| `run_turn` fails / returns empty (P2) | Same as Phase 1: `ToolResult(is_error=True)` |
| R mismatched (same-session concurrent identical input) (P2) | Streaming may stick to another card; `completed`'s canonical full text lands correctly under the real R |

**Canonical invariant (P2):** whether streaming works or is complete, `run_turn`'s
final full text is always returned as the `ToolResult` → the `tool_result` event is
persisted under the real R → reconnect / replay / the frontend's final state all use
the full text. Streaming is purely a "visual effect during generation" and never
affects correctness.

---

## 8. Deployment Modes (Phase 2)

| Mode | Behavior |
|---|---|
| `inprocess` | `subscribe_session_events` (ephem) and `emit_live_event` (calling) both in-process, via `SessionEventBus`. Fastest. |
| `http` (kernel as a separate process) | `subscribe_session_events` = HTTP SSE reading the ephem's live deltas; `emit_live_event` = `POST /sessions/{id}/events?live_only=true` injecting into the calling session. The kernel stays the sole event authority; the host's SSE funnel keeps reading the calling session from the kernel → injected events reach the frontend automatically. |
| `remote` (SaaS sandbox) | Both go through `_kernel_for(user_id)` to the user's sandbox; if the sandbox is alive live deltas flow normally, if it is down they stop (same as existing live deltas); `tool_output_delta` is not persisted and does not enter DataService history — replay relies on the canonical full text (Phase 1's synchronous fallback). |

**Key invariant: the kernel stays the sole event authority; the frontend reads only
one stream, from the kernel (via the host).** Streaming just adds live deltas onto
that stream — it does not change the architecture.

---

## 9. Testing

**Backend pytest** (mirror memory tests, monkeypatch the completer):

- `test_generative_ui_tools.py` — handler: argument validation; when the completer
  returns canned OpenUI Lang, `ToolResult.content` is correct; error paths
  (missing provider, empty output).
- `test_runner.py` — `_make_completer` builds the correct ephemeral
  `CreateSessionRequest` (monkeypatch `kernel_client`); recursion guard; cleanup.
  Phase 2: the streaming branch — when `tool_use_id` is set, stub
  `subscribe_session_events` to produce a `text_delta` sequence and stub
  `emit_live_event` to capture → assert each delta is emitted to the calling session
  as `{id: R, text}`; `run_turn` returns the full text; `tool_use_id=None` stays
  synchronous (no emit); `finally` cancels the subscription and deletes the ephem.
- `test_generative_ui_prompts.py` — `build_openui_prompt` splices request+data
  correctly; the vendored prompt resource loads and is non-empty.
- `test_ids.py` (P2) — `resolve_tool_use_id` pure logic: mock `get_events` with
  different tool_use candidates (different inputs / same-name other tools /
  timestamp order) → assert exact input match, recency tiebreak, no-match returns
  None; `normalize_input` tolerates key order.

**Frontend vitest:**

- `GenerativeUICard.test.tsx` — renders `<Renderer>` with output; empty/error
  states. Phase 2: `status="running"` + partial text → `<Renderer isStreaming>`,
  text grows; mock the Renderer to capture `isStreaming` and `response`.
- `ConversationPage` tool-override test — a `generate_ui` tool call routes to
  `GenerativeUICard` (including while running, Phase 2).

**Quality gates** (CLAUDE.md): `make test-all`, `make typecheck`, `make lint` all
pass. Phase 2 must add no new mypy / boundary debt beyond pre-existing ones.
**No** OpenAPI change, **no** `make generate-types` — the tool is discovered via the
MCP tool list and its result is a string.

---

## 10. Scope / YAGNI

**In scope:**

- Phase 1: one `generate_ui` tool + built-in `genui-lib` + synchronous
  generate-then-render + agent self-invocation + vendored prompt + `@valuz/ui` card
  + `renderToolCall` override.
- Phase 2: streaming render of `generate_ui`; input-fingerprint tool_use_id
  self-discovery (concurrency-safe); inprocess/http/remote support; canonical
  full-text fallback; lift to GenerativeUICard while running.

**Explicitly out of scope (revisit later):**

- A generic "any host MCP tool streaming" framework (only `generate_ui`).
- Exact card-sticking for same-session concurrent **identical-input** generate_ui
  (tiebreak is enough — visually indistinguishable).
- Changing the MCP decorator to forward `_meta`, changing the runtime, changing the
  kernel (none needed).
- Persisting `tool_output_delta` (deliberately live-only; replay uses the canonical
  full text).
- Valuz custom component library (Q2-B: map to `@valuz/ui` chart/table/form
  primitives).
- A composer "force-render UI" toggle / @-command (Q3-B/C).
- A settings master switch (v1 is resident; if needed, add
  `get_generative_ui_enabled` mirroring `get_memory_enabled`).
- Persisting a generated UI as a reusable artifact (currently lives only in the tool
  result; "pin UI" is a future feature).

---

## 11. References & Anchors

- OpenUI official: <https://www.openui.com/docs/openui-lang> · Renderer:
  <https://www.openui.com/docs/openui-lang/renderer> · repo:
  <https://github.com/thesysdev/openui>
- memory template: `backend/valuz_agent/modules/memory/{tools.py,runner.py,extraction.py}`
- Frontend tool lift-out precedents: `AskUserQuestion` / `submit_skill` via
  `renderToolCall` (`ConversationTurnList.tsx:212/359/415`,
  `ConversationPage.tsx:~2044/~5778`)
- Existing reusable `@valuz/ui` primitives: `ui/chart.tsx` (recharts),
  `ui/table.tsx`, `ui/form.tsx` — unused in v1, for the custom library later.
- Emit channel (P2): `adapters/kernel_client.py:181-183,384-399,730-731`,
  `adapters/kernel_client_http.py:258-267`, `kernel/app/routes/sessions.py:366-404`
- SSE translation (P2): `adapters/event_sse_adapter.py:203-212` (tool_use),
  `:357-368` (tool_output_delta), `:698-715` (live seq=None)
- Read events (P2): `adapters/kernel_client.py:185,401` (get_events)
- tool_use_id unavailability rationale (P2):
  `integrations/toolkit_mcp_server.py:152-160` (MCP decorator drops `_meta`)
- Frontend accumulation (P2): `packages/core/src/conversation/conversation-utils.ts`
  (`tool.call.output_delta`)
