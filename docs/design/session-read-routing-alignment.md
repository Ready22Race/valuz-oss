# Session read routing — aligning the implementation to the DataService design

> Companion to [data-service-architecture.md](data-service-architecture.md). That
> doc (§5) specifies that **session / message / event reads always go through the
> DataService**, and only live non-persisted deltas come from the kernel's live
> bus. The event read path was implemented (`event_sse_adapter._history_reader`);
> the **session read path was specified but never implemented**. This document is
> the change list to close that gap.

## 1. Why

Design rule (`data-service-architecture.md` §5):

> Reads (history reconstruction: `get_events` / `get_events_window` / **session &
> message fetches**) are served by the **DataService backend** — never from the
> execution-local sqlite … a sandbox is **ephemeral**; its local sqlite may be
> gone, so it cannot be the read source.

Current deviations:

1. **Session detail/list reads still go through `kernel_client`.**
   `modules/sessions/service.py` `get_session` / `list_sessions` (and ~20 other
   read points) call `kernel_client.get_session/list_sessions`. In `remote` mode
   `kernel_client` is the `HttpKernelClient` → the **ephemeral sandbox**. When
   the sandbox is gone these reads fail — the exact failure DataService removed
   for events but not for sessions. The two host readers
   (`LocalDataServiceReader`, `DataServiceReadClient`) expose only `get_events*`.

2. **session → project / task / kind is resolved via a kernel round-trip.**
   Handlers and several resolvers call `kernel_client.get_session(...)` purely to
   read `metadata.valuz.project_id` (and `task_id` / `run_kind`). These are
   **host facts** already stored in `valuz_project_session` /
   `valuz_task_session`; `project_index.project_of(session_id)` already exists.
   The kernel round-trip is unnecessary and breaks in `remote` when the sandbox
   is gone.

Symptom already observed: switching an install to `pg` makes 65/74 pre-existing
local-only sessions invisible, because reads come from the durable (PG) and the
old sessions were never written there.

## 2. Feasibility (confirmed)

The DataService backend is ready — only the host reader wrappers need methods:

- `kernel/app/data_service.py` already serves `/rpc/get_session` and
  `/rpc/list_sessions`.
- `StorePort` has `get_session` / `list_sessions`.
- `store_wire` has `session_to_row` / `row_to_session`.
- Missing: `get_session` / `list_sessions` on `LocalDataServiceReader` and
  `DataServiceReadClient`.

## 3. Field ownership (the bucketing rule)

| Field | Source | Bucket |
|-------|--------|--------|
| `project_id`, `kind` (run_kind), `origin` | host `valuz_project_session` | **A — host-side** |
| `task_id` (session→task) | host `valuz_task_session` | **A — host-side** |
| `status`, `stop_reason`, `mode`, `model`, `model_settings`, `runtime_provider`, `permission_mode`, `todos`, `instructions`, `created_at` | kernel session detail | **B — via DataService** |
| `metadata.valuz`: `name`, `agent_slug`, `locked_provider_id`, `last_user_message_text`, `extra_skill_ids`, `trigger_meta` | kernel session detail | **B — via DataService** |

`project_id/kind/task_id` carry a redundant host copy written at session
creation, so they can be resolved host-side; everything else lives only on the
kernel session and must come through the DataService.

## 4. Part 1 — extend the reader seam (enabler, do first)

| File | Change |
|------|--------|
| `adapters/data_service_local.py` | `LocalDataServiceReader`: add `get_session(user_id, session_id)` / `list_sessions(user_id, ids=…, limit=…)`, delegating to the wrapped `store` |
| `adapters/data_service_client.py` | `DataServiceReadClient`: add the same two, POSTing `/rpc/get_session` + `/rpc/list_sessions`, decoding via `store_wire.row_to_session` |
| `adapters/event_sse_adapter.py` (or new `data_service_read.py`) | add `_session_reader()` accessor, symmetric to `_history_reader()` (local reader if bound, else client) |
| `tests/.../test_data_service_contract.py` | extend route↔client↔StorePort parity to `get_session` / `list_sessions` |

## 5. Part 2 — route session detail/list reads through the reader (Bucket B)

| File:line | Function | Change |
|-----------|----------|--------|
| `modules/sessions/service.py:368` | `list_sessions` | `kernel_client.list_sessions` → `_session_reader().list_sessions` |
| `service.py:378` | `get_session` | → reader |
| `service.py:314,333` | `get_project_last_pick` | both `list_sessions` → reader |
| `service.py` `send_message:1195`, `send_message_sync:1280,1359`, `interrupt:1450`, `cancel:1690`, `enqueue:1566`, `regenerate:1709`, `rename_session:1721`, `get/set_extra_skills:1751,1763`, `set_permission_mode:1797`, `set_session_effort:1833` | the **read step** of each control method | read → reader; the **control action** (interrupt / update_session / set_mode / run / cancel / delete) stays `kernel_client` |
| `service.py:397,433` | `list_events` / `list_events_window` existence check | reader.get_session |
| `service.py:1886` | `submit_action` existence check | reader.get_session (control stays kernel) |
| `api/routes/sessions.py:687,709,971,1068,1131` | attachment/artifact route existence checks | reader.get_session |
| `modules/tasks/tools/handlers.py:753` | `_list_members` → `_bound_agent_member` reads `agent_config` | reader.get_session |
| `integrations/automations_mcp_server.py:119` | `_resolve_session_context` reads `project_id` + `agent_slug` + `user_id` | `project_id` via Part 3; `agent_slug` via reader.get_session |
| `modules/tasks/actor_runner.py:247,484` | post-turn `status` / `stop_reason` | reader.get_session ⚠️ §7 |
| `modules/tasks/coordination.py:260` | `_heartbeat_pending` `status` / `stop_reason` | reader.get_session ⚠️ §7 |
| `modules/tasks/lifecycle.py:834` | `_auto_finalize_lead_task` `stop_reason` | reader.get_session ⚠️ §7 |
| `modules/tasks/lifecycle.py:1227` | `finish_task` `mode` | read → reader; `set_mode` stays kernel |

## 6. Part 3 — resolve project/task/kind host-side (Bucket A, drops kernel round-trips)

| File:line | Function | Reads today | Becomes |
|-----------|----------|-------------|---------|
| `tasks/tools/handlers.py:109` | `_check_lead_gate` | run_kind/task_id/project_id | `project_index.project_of` + `valuz_project_session.kind` + `valuz_task_session` |
| `handlers.py:153,177` | `_resolve_plan_writer_task` (+`_check_plan_writer_gate`) | task_id/project_id/run_kind/id | as above; id = `ctx.session_id` |
| `handlers.py:192` | `_resolve_plan_reader_task` | task_id/project_id | as above |
| `handlers.py:284` | `_check_orchestration_gate` | run_kind/project_id/user_id | host tables; user_id = `ctx.user_id` |
| `handlers.py:566` | `_inject_into_task` | project_id/id | `project_of` |
| `handlers.py:622` | `_resume_task` | project_id/id | `project_of` |
| `tasks/actor_runner.py:212` | `run_session_to_idle` | project_id | `project_of` |
| `integrations/docs_mcp_server.py:76` | `_resolve_project_id` | project_id | `project_of` |
| `integrations/tools_agent_proposal.py:972` | `_resolve_project_id` | project_id | `project_of` |
| `api/routes/agents.py:426` | `_resolve_session_project_id` | project_id | `project_of` |

New helper needed: `task_of(session_id)` over `valuz_task_session`
(`session_id` column already exists). `project_of` already exists.

## 7. Explicitly NOT moved (execution plane, stays kernel_client)

`run` / `submit_message` / `interrupt` / `submit_action` / `set_mode` /
`update_session` / `delete_session` (the delete op itself) / live
`subscribe_session_events` (the live delta stream). The design keeps these on
the execution plane.

## 8. Risks / caveats

1. **status/stop_reason freshness.** These mutate during a turn. In
   `pg`/in-process the durable is immediately consistent; in `remote` the durable
   reflects the last persisted write and may lag the sandbox's in-memory state by
   one beat. The Part 2 status reads mostly happen **after** a turn completes
   (already flushed), so they are safe — but each must be confirmed not to depend
   on "is it running *right now*". `coordination._heartbeat_pending` is the one to
   verify hardest.
2. **Existence checks read the durable.** After the switch, the ~7
   `get_session(...) is None` checks resolve against the durable. Pre-`pg`
   local-only sessions would read as "missing" — see caveat 3.
3. **Backfill of legacy local-only sessions.** Sessions created before `pg` was
   enabled are not in the durable. Provide a one-time idempotent backfill
   (sessions + messages + events) or accept them as legacy. Recommended to ship
   alongside this refactor.
4. **task_id reverse lookup.** Confirm `valuz_task_session.session_id` is indexed
   (the column exists at `tasks/models.py:137`) and add `task_of()`.

## 9. Status

- **Part 1 — done, then hardened into a typed port.** `LocalDataServiceReader`
  gained `get_session` / `list_sessions` (projected to `SessionData` via
  `session_to_data`). The rpc backend already had `load_session` / `list_sessions`
  (contract-tested in `EXPECTED_OPS`), so no backend change. The accessor was then
  elevated to a **`DataReader` Protocol** (`adapters/data_reader.py`) bound at the
  composition root via `bind_data_reader` — OSS binds `LocalDataServiceReader`; a
  SaaS build embedding OSS as a submodule binds its own implementation with zero
  call-site edits. The events accessor (`_history_reader`) and the session accessor
  collapsed into this one seam (`data_reader()`); the kernel-seam fallback is the
  typed `_KernelClientReader` default (local-only / sandbox, no host durable).
  `DataServiceReadClient` remains **deferred** — never instantiated; its session
  methods land when a split-host SaaS topology actually wires it.
- **Part 3 — done (the clean subset).** The three pure-`project_id` resolvers
  (`docs_mcp_server`, `tools_agent_proposal`, `agents` route) now use
  `project_index.project_of()` — no kernel round-trip.
- **Part 2 — done.** All session detail/list reads route through `session_reader()`:
  `sessions/service.py` (23 sites), `sessions.py` routes (5), `tasks` handlers (7),
  `actor_runner` / `lifecycle` / `coordination`, and `automations_mcp_server`.
  The harness gate handlers were aligned by **routing the read through the durable
  DataService** (identical `SessionData`, sandbox-agnostic) rather than re-deriving
  gate facts from host tables — lower risk for auth-sensitive code; full host-table
  re-derivation (`kind`→run_kind, `task_of()`) remains a possible follow-up.
- **Backfill — done.** `scripts/backfill_durable_sessions.py` copies pre-`pg`
  local-only sessions (+messages +events) into the durable; idempotent at session
  granularity, insert-only. Run with the app's `VALUZ_DURABLE_DATABASE_URL`.
- Full backend suite is regression-free against the post-merge baseline (11
  pre-existing upstream task-stub failures unchanged).

## 10. Suggested sequencing

1. Part 1 (reader extension + contract test) — the enabler.
2. Part 3 (host-side resolution) — pure win, lowest risk.
3. Part 2 (session read routing) — verify status freshness per call site (§8.1).
4. Backfill script.
5. `make test-all` / `make typecheck` / `make lint`.
