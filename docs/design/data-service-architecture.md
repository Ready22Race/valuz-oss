# DataService Architecture

> The **DataService** is the single CRUD layer for the kernel's three tables
> (`sessions` / `messages` / `events`). Every read and write of kernel data
> flows through it. This document is the source of truth for its architecture,
> its interaction flows, and how the deployment **forms** (local, sandboxed,
> remote-synced, SaaS) are all the *same mechanism* with two swappable knobs.
>
> Companion docs: [architecture.md](../architecture.md) (system topology),
> [kernel-sandbox-deployment.md](kernel-sandbox-deployment.md) (sandbox
> provisioning).

---

## 1. Principle

**One data layer, always in the path.** The kernel never talks to a database
driver for its three tables directly in production wiring — it talks to the
**DataService**, a small set of CRUD operations (the `StorePort` surface)
exposed as a **FastAPI router mounted on the host app** (`POST /rpc/{op}`, one
op per StorePort method). There is **no separate DataService process**; it is a
host sub-router.

The DataService has a **swappable backend** and is reached over a **swappable
transport**. Everything else — "local", "sandboxed", "remote PG", "SaaS" — is a
combination of those two knobs. There is no separate code path per form.

```
        kernel (in-process OR in a sandbox)
                     │  StorePort
                     ▼
        ┌─────────────────────────────┐
        │  DataService  (host router)  │     ← the ONE data layer
        │  POST /rpc/{op}              │
        └──────────────┬──────────────┘
                       ▼  backend (swappable)
         ┌─────────────┴──────────────┐
         │ host sqlite (default)       │  OR  remote PG (when "remote sync" on)
         └─────────────────────────────┘
```

---

## 2. Two orthogonal knobs

| Knob | Values | Chosen by | Effect |
|------|--------|-----------|--------|
| **Execution location** | in-process kernel · seatbelt sandbox · (future) cloud sandbox | deployment / `VALUZ_SANDBOX_DRIVER` | *where the agent loop runs* and therefore the **transport** to the DataService (in-process call vs HTTP) |
| **DataService backend** | host sqlite (default) · remote PG | the **OSS settings page** ("Data Service" → remote sync) | *where kernel data is durably stored*; remote PG turns on the **JWT auth boundary** |

These are **independent**. Sandboxing does not imply remote PG; remote PG does
not imply a sandbox. Any combination is valid.

---

## 3. Deployment forms (the knob matrix)

| # | Execution | Backend | Transport to DataService | Notes |
|---|-----------|---------|--------------------------|-------|
| 1 | in-process kernel (no sandbox) | host sqlite | **in-process** call | OSS default. Kernel's 3 tables land in the host-managed sqlite *through* the DataService. |
| 2 | seatbelt sandbox | host sqlite | **HTTP** (sandbox → host callback URL, JWT) | Behaviour identical to #1 from the user's view. Sandbox also writes its own local sqlite (buffer); the outbox guarantees the host sqlite converges. |
| 3 | in-process **or** sandbox | **remote PG** | in-process or HTTP | "Remote sync" configured. Data additionally lands in the remote PG via the DataService. With a sandbox the **JWT boundary** ensures the **PG credentials never enter the sandbox**. |
| 4 (SaaS) | cloud sandbox | remote PG | HTTP, JWT | The same as #3 with an ephemeral cloud sandbox + central PG — **config-and-go** because the mechanism is identical. |

The point: forms 1→4 are one implementation with the two knobs flipped. SaaS is
not a fork — it is form 3 with a cloud sandbox driver and a PG backend.

---

## 4. Write path — dual-write + outbox consistency

A write does **two** things:

1. **Local sqlite** (the kernel's execution-local store — sandbox-local, or the
   host's when in-process). Fast, always-available, survives DataService blips.
2. **DataService** (→ host sqlite or remote PG). The durable / shared copy that
   reads are served from.

To guarantee the DataService copy is **maximally consistent** even across a
transient DataService/PG unavailability, a DataService write that fails is
recorded in a **`durable_outbox`** (in the local sqlite) and **re-pushed** by a
background drainer until it lands. Replay is idempotent (`event_uid` for events,
UUID PKs for sessions/messages), so at-least-once redelivery is safe. This is
the role `durable_outbox` was built for: **eventual consistency of the
dual-write into the DataService.**

**Collapse optimization.** When the execution-local sqlite and the DataService's
backend resolve to the **same file** (pure in-process + sqlite backend, form 1),
the dual-write collapses to a **single write** and reads are a direct call — no
self-mirroring, no outbox needed.

**Event seq.** Each physical store owns its own `events` autoincrement; the
sequences are **independent** and bridged by `event_uid`. The seq a reader sees
is the **DataService backend's** seq (reads come from there). Never force one
store's seq onto another's PK (it collides with pre-existing ids and drops rows).

---

## 5. Read path — always via the DataService

Reads (history reconstruction: `get_events` / `get_events_window` / session &
message fetches) are served by the **DataService backend** — never from the
execution-local sqlite. Rationale: a sandbox (especially a cloud sandbox) is
**ephemeral**; its local sqlite may be gone, so it cannot be the read source.

- **Form 1** (in-process + sqlite): the host reads via the **in-process**
  DataService → host sqlite. No HTTP.
- **Forms 2–4** (sandbox and/or PG): the host reads via the DataService router
  → backend. Because the DataService lives on the **host**, history reads
  succeed **even when the sandbox kernel is gone**. Live, non-persisted deltas
  (`text_delta` / `tool_output_delta`, etc.) still stream from the kernel's live
  bus while the sandbox is alive; once it is gone, the stream degrades to
  history-only.

---

## 6. Auth & isolation boundary

The DataService derives the **owner** for every request from a **verified
bearer token** (HS256 JWT today; the `TokenVerifier` port allows RS256/JWKS for
SaaS), never from the request body. Consequences:

- A **sandbox holds only a short-lived JWT** + the DataService URL. It never
  receives a DB DSN, driver, or PG credential — the credential lives only on the
  host (the DataService's backend config).
- On a **remote PG** backend, **Row-Level Security** is the DB-side backstop:
  the DataService stamps `app.current_user_id` per transaction (`SET LOCAL`) from
  the verified token, and connects as a **non-owner role** so the RLS policy is
  enforced even if an app-layer filter is ever missed.
- The owner-from-token rule means a compromised sandbox cannot read or write
  another owner's data.

---

## 7. Transport

The DataService client surface is identical regardless of transport; only the
binding differs:

| Execution | Binding | Wire |
|-----------|---------|------|
| in-process kernel | direct call into the host DataService router (or its store) | none |
| sandbox kernel | HTTP `POST /rpc/{op}` to the host callback URL | JSON rows + `Bearer <jwt>` |

The host's own consumers (SSE adapter, etc.) use the in-process binding; only a
sandboxed kernel crosses the HTTP boundary.

---

## 8. Interaction flows

### 8.1 Write (sandbox kernel, remote PG backend — form 3/4)

```
agent turn → kernel.append_event
   ├─ write sandbox-local sqlite            (buffer; fast)
   └─ POST /rpc/append_event  ─HTTP+JWT─▶  host DataService
                                              ├─ verify JWT → owner
                                              ├─ SET LOCAL app.current_user_id
                                              └─ INSERT … RETURNING seq → PG
        on HTTP/PG failure ▶ enqueue durable_outbox(local) ▶ drainer re-pushes
```

### 8.2 Read history (host, sandbox already destroyed)

```
client opens session → host SSE adapter
   └─ DataService (host router) → PG: get_events_window / get_events_after
        → translated to legacy SSE frames → client            (no kernel needed)
   live deltas: subscribe kernel SSE → sandbox gone → history-only (graceful)
```

### 8.3 Default (in-process + sqlite — form 1, collapsed)

```
kernel.append_event → DataService (in-process) → host sqlite   (single write)
reads → DataService (in-process) → host sqlite
```

---

## 9. Control plane

**All behaviour is controlled from the OSS settings page** — there are no
bespoke launch scripts per form.

- **Settings → Data Service** (revealed by the hidden 9-tap on "About"):
  - **Mode / backend**: default (host sqlite) vs **remote sync** (PG DSN), and
    (when sandboxed/remote) the DataService URL + token.
  - **Health**: a live health indicator for the DataService + its backend.
  - **OpenAPI**: surface the DataService's OpenAPI (the `/rpc/*` contract) so the
    schema is inspectable.
- **No `make dev-remote`.** It conflated "start PG", "start a data service", and
  "run a sandbox" into one script. Replace it with a thin **`make pg` / PG-podman
  helper** that only brings up a local Postgres; everything else (turn on remote
  sync, point at the PG, sandbox or not) is driven from the settings page. This
  decouples infra from behaviour.

---

## 10. SaaS extension

SaaS is **form 4 with no new code paths**: a cloud sandbox driver (execution
knob) + a central PG backend (backend knob), both already abstracted. Because
the DataService + JWT boundary are identical to the local forms, the cloud
sandbox and the centralized PG are **config-and-go**: the SaaS overlay binds a
cloud `SandboxDriver` and points the DataService backend at the managed PG;
nothing in the kernel or the data layer changes.

---

## 11. Delta from the current implementation

> This section is the bridge to the implementation work that follows this doc.

**Already in place:** the `/rpc/{op}` DataService app + StorePort surface
(`kernel/app/data_service.py`), the `store_wire` codec, JWT signer/verifier +
`TokenVerifier` port, RLS migration, `event_uid` idempotency, the
`durable_outbox` table + `DurableOutbox` drainer, the host `DataServiceReadClient`
+ SSE read-routing, the in-process PG `WriteThroughStore`, and the settings page
+ `/v1/settings/data-service` config.

**Must change to match this doc:**

1. **DataService is always the data layer, mounted as a host router.** Today
   `local` mode binds a direct `SQLAlchemyStore` (bypassing the DataService) and
   the DataService app is only used standalone for `remote`. Mount the
   DataService router on the host FastAPI and route the in-process kernel through
   it (with the collapse optimization for the same-file case).
2. **Re-frame the knobs.** Replace the `local | pg | remote` *store-mode* with
   two independent settings: **backend** (host sqlite | remote PG DSN) and the
   sandbox execution choice. "Remote sync" = backend is PG; it composes with or
   without a sandbox.
3. **Restore dual-write + outbox as the write consistency mechanism** for the
   sandbox/HTTP path (the local-authority + `durable_outbox` machinery that was
   recently parked is the right tool here — un-park it, now driven by "is there a
   DataService hop?", not by a `pg` tier).
4. **Reads always via the DataService** (host router), in every form — including
   form 1 (in-process), where it is a direct in-process call.
5. **Settings page**: fix the 9-tap reveal (currently not surfacing the section),
   and add **health status** + **OpenAPI** surfacing to the Data Service panel.
6. **Drop `make dev-remote`**; add a minimal **PG-podman** helper; drive remote
   sync from the settings page.

Each item lands incrementally behind the contract tests
(`test_data_service_contract.py` pins route↔client↔StorePort) and the full
suite.
