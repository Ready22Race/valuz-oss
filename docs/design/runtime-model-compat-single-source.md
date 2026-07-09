# Runtime/Model Compatibility — one source, dumb-render pickers, execution-declared availability

> Status: Design (proposed).
>
> One-line direction: **"which runtime(s) can run this model" is derived in
> exactly one place (`runtimes_for`), materialized onto every model row
> (`LLMModel.runtimes`), and rendered verbatim by the frontend.** The frontend
> stops re-deriving compatibility from `protocol`/`provider_kind`. Separately,
> **"is this runtime actually runnable" is declared by the execution target**
> (the host that runs the kernel), not probed on the API host — because in a
> split control-plane/execution-plane deployment the runtime binary lives in the
> sandbox, not the API pod.

This is the OSS-side contract. A contributing overlay (an `LLMProvider` that adds
gateway/catalog channels) declares `runtimes` on its contributed rows and binds a
runtime-availability provider from its own execution target; OSS ships the
derivation rule, the single materialization point, the frontend plumbing, the
availability port, and the default local implementation.

---

## 1. Why

Runtime↔model compatibility is currently computed in **three** places that have
drifted apart:

| Impl | Where | Codex rule |
|---|---|---|
| **Authoritative** | `modules/settings/model_options.py:runtimes_for` | any non-subscription channel speaking `openai-response` → codex |
| Frontend re-derive #1 | `packages/core/src/hooks/use-composer-providers.ts` | non-subscription + `canDriveAny(["openai-response"])` → codex |
| Frontend re-derive #2 | `packages/core/src/api/runtime-compat.ts` | codex **only** for `provider_kind==="system"` or `codex-subscription` |

`runtimes_for` already implements the correct rule (a user-supplied
OpenAI-compatible Responses endpoint — e.g. Volcengine Ark — can drive codex via
the kernel's synthetic `[model_providers.harness]` block, `wire_api="responses"`;
see `backend/kernel/src/runtimes/codex/runtime.py`). It is surfaced verbatim on
`GET /v1/settings/model-options` (`ModelOption.runtimes`), and the default-config
picker (`ModelSection.tsx`) and onboarding (`ConnectStep.tsx`) already
dumb-render it.

But the composer, the project-agent picker, and the provider-list "可用于"
(available-for) badge do **not** read that field — they re-derive from
`compatible_protocols`/`provider_kind` through the two frontend impls above. The
two impls disagree with each other and with the backend, so:

- A tested, working custom `openai-response` provider shows **no "OpenAI Codex"
  badge** (`runtime-compat.ts` requires `provider_kind==="system"`), even though
  it can run codex.
- Contributed catalog channels that legitimately declare `runtimes=("codex",)`
  can be dropped or mislabeled depending on which impl a given surface uses.

Root cause: the "server-resolved model-options + dumb-render pickers" migration
(the `model-options` endpoint) was only wired into *some* pickers. The providers
list/detail endpoints still leave `LLMModel.runtimes` unset, forcing every other
surface to re-derive.

**A second, deployment-shaped gap:** `is_runtime_available` (`adapters/runtime_registry.py`)
probes the **API host's** PATH / bundled binary for the codex binary. In a
bundled desktop that host *is* where the turn runs, so it's correct. In a split
deployment where the kernel runs in a separate sandbox, the API pod's PATH is the
wrong thing to measure — the pod may lack codex while the sandbox ships it (or
vice-versa). `_runtimes_available` in `modules/system/service.py` already punts
and returns the static set, acknowledging this.

## 2. Current state (authority map)

- **Authoritative — model→runtime derivation:** `runtimes_for(protocols, provider_kind)`
  and `build_model_options` (`modules/settings/model_options.py`).
- **Authoritative — runtime↔protocol capability + host availability:**
  `RUNTIME_REGISTRY` and `is_runtime_available` (`adapters/runtime_registry.py`),
  mirroring kernel `src/runtimes/factory.py:ALLOWED_PROTOCOLS_BY_RUNTIME`.
- **Deliberate mirror (kept in lock-step):** `providers/service.py:_derive_compatible_protocols`
  ↔ `provider_resolver._resolve_api_protocol`; OSS registry ↔ kernel factory ↔
  frontend `runtime-protocols.ts`.
- **Dead in production:** `runtime_registry.supports_protocol` — only tests call
  it; the real runtime↔protocol gate is the kernel factory at session start.
- **Surfaces that already dumb-render `runtimes`:** `ModelSection.tsx` default-config
  picker; `onboarding/ConnectStep.tsx`.
- **Surfaces that re-derive (to be converted):** `AgentModelPicker.tsx`,
  `ConversationsHomePage.tsx`, `ProjectDetailPage.tsx`, `ConversationPage.tsx`
  (all via `useComposerProviders`); `ModelSection.tsx` runtime-switch guard and
  "可用于" badge (via `isProviderRuntimeCompatible`/`compatibleRuntimes`).

## 3. Design

### 3.1 Materialize `runtimes` on every model surface

`LLMModel.runtimes` is the wire field already declared in
`modules/providers/schemas.py` and `packages/shared/src/types/provider.ts`
(`string[] | null`). Today `_row_to_list_item` / `_row_to_detail`
(`modules/providers/service.py`) deliberately leave it `None` on user/builtin
rows and rely on the picker to derive. Change them to fill it from the one rule:

```python
compatible = _derive_compatible_protocols(row)
ch_runtimes = tuple(runtimes_for(compatible, provider_kind=row.provider_kind))
models = [
    LLMModel(id=m.id, label=m.label, runtimes=(m.runtimes or ch_runtimes))
    for m in _resolve_models(row)
]
```

- A per-model `runtimes` (declared by a contributor) still wins; only `None` is
  filled. So `build_model_options` is unchanged (`m.runtimes` is now non-`None`
  with the same value it would have derived), and `provider_resolver`'s
  contributed-row path is unchanged.
- `GET /v1/providers` (list + get) now carries authoritative `runtimes`, so every
  frontend surface can read it without a second endpoint.

This is **additive**: the field and its `null` semantics already exist; a client
that still reads the `null` branch keeps working.

### 3.2 Frontend: dumb-render `runtimes`, delete re-derivation

- `use-composer-providers.ts:useComposerProviders` — filter at the **model** level
  by `m.runtimes?.includes(runtimeFilter)`. Delete `canDriveAny` /
  `canDriveAnthropic` / `CODEX_PROTOCOLS` / `DEEPAGENTS_PROTOCOLS` and the
  subscription-kind runtime logic (subscription exclusion is already encoded by
  `runtimes_for`). Keep `providerHasUsableCredentials` (a credential gate, not a
  runtime gate). Attach the channel's runtimes to the `default_model` fallback
  row so credential-only anchors still filter correctly.
- `runtime-compat.ts` — reimplement `isProviderRuntimeCompatible` /
  `compatibleRuntimes` as the union of `provider.models[].runtimes`; widen
  `CompatProvider` to include `models`. Delete `speaksAnyProtocolFrom` and the
  compatibility use of `ALLOWED_PROTOCOLS_BY_RUNTIME`. Consumers
  (`ModelSection.tsx` runtime-switch guard + badge) keep their call signatures.
- `runtime-protocols.ts` — **retained** for the New-Session / Edit-Capabilities
  **protocol-selection dropdowns** (`defaultProtocolFor` / `isProtocolAllowed`),
  which choose which wire to *configure*. It is no longer referenced by
  compatibility filtering.

Result: composer, agent picker, badge, default-config, and onboarding all read
the same backend field; there is one place (`runtimes_for`) to change when a
runtime is added.

### 3.3 Runtime availability declared by the execution target

Add a port so the environment that actually runs the kernel declares which
runtimes it can launch:

```python
# ports/runtime_availability.py
class RuntimeAvailabilityPort(Protocol):
    def available_runtimes(self) -> set[str] | None:
        """Runtimes launchable in the execution environment.
        ``None`` → fall back to the local host probe (bundled desktop)."""
```

- `ports/extensions.py` gains `ext.runtime_availability: RuntimeAvailabilityPort | None = None`.
- `is_runtime_available(runtime_id)` consults it first: if `available_runtimes()`
  returns a set, availability = membership (skip the binary probe); if it returns
  `None` (or `ext` unbound), keep the existing PATH / bundled / `CODEX_BIN_OVERRIDE`
  probe. `GET /v1/runtimes` and `tools_agent_proposal` follow automatically.
- **Default (OSS single-run / bundled desktop):** unbound → local probe →
  identical to today.

The authoritative capability is the execution image's manifest; the provider is
that manifest declared to the control plane (e.g. build-time, keyed by image
digest), not a live per-session probe on the picker hot path.

## 4. Contract impact (`contracts/COMPATIBILITY.md`)

| Change | Class | Note |
|---|---|---|
| `GET /v1/providers` list+get now populate `LLMModel.runtimes` | evolving (additive) | field + `null` semantics pre-exist; old clients read the `null` branch |
| new `ext.runtime_availability` port + `RuntimeAvailabilityPort` | new / stable | optional; unbound = current local-probe behavior |
| `LLMProvider.list/resolve`, `RUNTIME_REGISTRY`, kernel `ALLOWED_PROTOCOLS_BY_RUNTIME` | unchanged | — |

`supports_protocol` may be deleted or marked deprecated (no production caller).

## 5. Change list

Backend:
- `modules/providers/service.py` — fill `runtimes` in `_row_to_list_item` / `_row_to_detail`.
- `ports/runtime_availability.py` (new) + `ports/extensions.py` — the port + `ext` slot.
- `adapters/runtime_registry.py` — `is_runtime_available` consults `ext.runtime_availability`.

Frontend:
- `packages/core/src/hooks/use-composer-providers.ts` — model-level `runtimes` filter; drop protocol re-derivation.
- `packages/core/src/api/runtime-compat.ts` — union of `models[].runtimes`; drop protocol re-derivation.
- (`packages/core/src/api/runtime-protocols.ts` — unchanged; scope narrowed to protocol dropdowns.)

## 6. Migration / sequencing

1. Land 3.1 + 3.2 together (backend fill + frontend consume) — self-consistent,
   additive on the wire.
2. Land 3.3 (port + default local impl) — no behavior change until an overlay
   binds it.
3. Contract regression (`make test-contract`) green before publishing.

3.2 depends on 3.1 (frontend needs the populated field). 3.3 is independent.

## 7. Testing

- `_row_to_list_item` / `_row_to_detail` populate `runtimes` for the
  `anthropic` / `openai-completion` / `openai-response` / dual-protocol / and both
  subscription kinds (codex-subscription → `["codex"]`, no deepagents).
- `is_runtime_available` under a bound `ext.runtime_availability`: membership hit
  → available; miss → unavailable-with-reason; unbound → local probe unchanged.
- `useComposerProviders` filters by `m.runtimes` only; subscription exclusion
  still holds (driven by backend runtimes).
- `runtime-compat.compatibleRuntimes` = union of `models[].runtimes`; a tested
  custom `openai-response` provider surfaces the codex badge.

## 8. Downstream (overlay) responsibilities

Kept out of OSS; documented here so the seam's intent is unambiguous:

- A contributing `LLMProvider` that adds gateway/catalog channels declares
  `LLMModel.runtimes` on its rows. In particular, an `openai-response` card that
  is meant to drive codex declares `runtimes=("codex",)` and
  `compatible_protocols=["openai-response"]` (mirroring how a system-gateway
  Responses card is contributed today). OSS consumes these verbatim.
- An overlay whose kernel runs in a separate sandbox binds
  `ext.runtime_availability` from its execution target (the sandbox image's
  declared runtime set), so codex is reported available iff the sandbox image
  ships it — regardless of the API host's PATH.

## 9. Non-goals

- No change to the kernel factory as the final runtime↔protocol gate at session
  start.
- No change to `web_search` being force-disabled for non-subscription codex keys
  (kernel-side).
- The protocol-selection UI (`runtime-protocols.ts`) stays a frontend concern; it
  is a *configuration* aid, not a compatibility source.
