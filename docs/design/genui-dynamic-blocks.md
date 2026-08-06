# Dynamic block injection

> How the commercial edition adds generative-UI components without a fork, and
> how the prompt is assembled so the model is told about exactly the components
> that can render.

## What makes this hard

A block has **two halves that must not drift**:

| Half | Produced by | Consumed by |
|---|---|---|
| the React implementation | frontend | the renderer, when a payload names it |
| the spec — name, props, description | the prompt | the model, when deciding what to emit |

Ship the implementation without the spec and the model never emits it: dead
code. Ship the spec without the implementation and the model emits it and the
renderer draws nothing — silently. That second failure has bitten this codebase
four times already, always presenting as "the section is blank".

Today both halves come from one source at **build time**:

```
blocks.ts ─▶ catalog.ts ─▶ gen_genui_catalog.mjs ─▶ a2ui_block_catalog.txt
                                                     ↓
                                       backend package resource,
                                       read once at import
```

`A2UI_COMPONENT_CATALOG` is a module-level constant, so a rewritten asset does
nothing until the process restarts. That is the one link the design has to
break — and only that one.

## Which mechanism, and why not the obvious one

The commercial edition is a **separate repository that vendors OSS into its
workspace** (`vendor/valuz-oss/`), not a host that downloads plugin bundles:

```
valuz/
├── vendor/valuz-oss/            ← the whole OSS tree, in the pnpm workspace
├── frontend/packages/commercial/
│   └── renderer.ts              ← installs everything at startup
├── backend/valuz_commercial/    ← binds ports via valuz_agent.ports.extensions
└── editions/{finance,team}/     ← vertical overlays, front and back
```

So a runtime plugin loader would be the wrong tool. The commercial build can
simply `import` from `@valuz/genui-blocks`, and its backend already replaces OSS
behaviour through `ext` at startup. **The design should use the seam that
exists**, not add a second one:

- Frontend: an install function called from `commercial/renderer.ts`, alongside
  `registerLocaleNamespace()` and `mergeEditionProfile()`.
- Backend: a registry attribute on `ext`, in the shape
  `CitationQualityPolicyRegistry` already established — OSS owns the baseline
  and the merge contract, overlays contribute additively in fixed layers.

"Dynamic" here means *not fixed at OSS build time* — resolved when the process
starts and when an edition is active. It does not mean loading untrusted code.

## Frontend: one registry, spec derived from implementation

`@valuz/genui-blocks` gains a runtime overlay beside its built-in set:

```ts
registerBlocks(source, blocks, { reserved, mode }): RegisterResult
unregisterBlocks(source): void
subscribeBlocks(listener): () => void      // for useSyncExternalStore
effectiveBlocks(): BlockComponent[]        // what is actually live
```

`A2UIRenderer` resolves names through `effectiveBlocks()` on every render and
subscribes with `useSyncExternalStore`, which is how this repo already handles
module-level stores — a conversation already on screen picks up an edition that
registered at startup.

Two rules carry the weight:

**The spec is derived, never authored.** `describeBlock()` reads the same zod
schema and description the build-time generator reads. There is no second place
to keep in sync, so the halves cannot drift.

**Collisions are refused, not merged.** A registered name may not take an
OpenUI component's or a built-in block's. Merge order would decide it silently,
and a plugin shadowing `Card` breaks every document. Refused names come back in
`RegisterResult.rejected` so the caller can fail loudly.

### Two modes: append, or the edition's set alone

`mode: "append"` (the default) puts the edition's blocks beside the built-in
ninety-nine. `mode: "replace"` gives the edition the whole vocabulary.

Replace exists because prompt budget is per call and menu length costs
accuracy: a finance edition with thirty curated components does not want a
hundred and fifty general ones described alongside, both because the model
picks worse from a longer menu and because every one of them is tokens on
every `generate_ui`.

| Layer | Under `replace` | Why |
|---|---|---|
| the root (`Stack`) | **kept** | `createLibrary` throws without it, and a document with no resolvable root renders nothing |
| OpenUI's other components (53) | dropped | general vocabulary; a vertical brings its own |
| Valuz blocks (99) | dropped | product vocabulary — exactly what a vertical wants to own |

So the root is the only name `replace` still refuses; every other name is the
edition's to take, including ones a built-in used to hold. The root is still
described to the model — it is the one component every document begins with,
and leaving its arguments undocumented would make the model guess at them.

Only one source may hold `replace`; a second registration is refused wholesale,
because "which edition's set is live" must have exactly one answer.
Unregistering the replacing source restores the built-ins.

Suppression has to reach **every** consumer or a replaced component leaks back
through whichever path was missed — A2UI's name resolution and the block
package's own render harness both derive from the registry rather than from
`blockComponents` or the hand-listed OpenUI names, and a test pins it.

### Narrowing per call: the `components` argument

Replace is a startup decision. The same question comes up per generation, and
`generate_ui` answers it with a `components` argument:

| value | offered | prompt (OSS) |
|---|---|---|
| `all` (default) | everything | ~64k chars |
| `atoms` | everything this repo ships — OpenUI's primitives **and** the built-in blocks | ~64k |
| `edition` | only what an edition registered from outside this repo | widens to `all` |

The split follows **where a component comes from**, not what it is made of.
That is the only line that survives contact with the repo boundary: an edition
is a separate build that vendors this one, so "this repo's set" and "the
edition's set" are the two things a caller can meaningfully ask for.

The consequence in OSS is that the argument does nothing: with no edition
registered, all three values resolve to the same catalog. It becomes a lever
only once an edition is installed — which is what it is for. An earlier draft
made `atoms` mean OpenUI's primitives alone, which cut the prompt twenty-fold,
but that line does not exist outside this repo: to an edition, the built-in
blocks and the primitives are equally "the general vocabulary they did not
write".

Two properties keep it safe:

**Narrowing is prompt-side only.** The renderer keeps accepting every component
it ever accepted, so a narrow prompt can never produce a payload the client
cannot draw. The dangerous direction — describing something that cannot render
— stays closed.

**A scope withholds consistently.** The "if the data has no chart series, fall
back to…" advice names components per scope, so it never points at something
the catalog did not show. Advice to reach for a component the model was never
given is worse than no advice. Catalog and instructions resolve the scope
through one function so they cannot disagree about which set is live.

**An empty scope widens.** `edition` with no edition registered would offer the
root and nothing else — that does not make a smaller answer, it makes none.
Widening is the only safe direction when a scope turns out empty.

The catalog is assembled per call from one generated block asset plus a
hand-written primitive list, because only the block half has a build step to
hang a variant on.

## Backend: a registry port, and a catalog assembled per call

```python
# valuz_agent/ports/genui_blocks.py  (OSS owns baseline + merge)
class GenUIBlockRegistry:
    def register(self, layer: Literal["commercial", "distribution"],
                 specs: Sequence[BlockSpec]) -> None: ...
    def catalog_text(self) -> str: ...        # built-in ⧺ registered, in layer order

# valuz_agent/ports/extensions.py
self.genui_blocks = GenUIBlockRegistry()
```

Then the two prompt builders take the registry's text instead of a constant:

```python
-A2UI_COMPONENT_CATALOG = f"""{_HAND_WRITTEN}\n{_load_block_catalog()}"""
+def build_component_catalog() -> str:
+    return f"{_HAND_WRITTEN}\n{ext.genui_blocks.catalog_text()}"
```

That single change is what makes the prompt dynamic. Everything else — the
generated OSS asset and the group structure — stays.

**The commercial backend registers at startup**, from an asset its own build
generates the same way OSS generates its own. Same generator, same
`describeBlock()`, different package.

## Keeping the two sides honest

The frontend renders and the backend prompts, so nothing structurally stops an
edition from registering different sets on each side. Two cheap guards:

1. **One asset, both sides.** The edition's build emits its catalog once; the
   frontend install and the backend registration read that same file. A
   mismatch becomes a build error rather than a blank section at runtime.
2. **A boot assertion.** On startup, compare the registered spec names against
   the names the renderer will accept, and log loudly on a difference. Cheap,
   and it catches the case where one side shipped and the other did not.

## What this does not change

- **Prompt cost scales with what is registered.** An edition adding forty
  components makes every `generate_ui` call bigger for its users. The catalog is
  assembled per group, so trimming means dropping groups.
- **Trust.** Registered descriptions become prompt content, so an edition can
  steer the model. That is fine for code inside the commercial build, and is the
  reason this design does **not** grow a remote loader: the moment blocks arrive
  from outside the build, prompt content arrives with them.
- **Protocol reach.** A2UI v0.9 is the only wire protocol; the OpenUI Lang
  generation path was removed rather than maintained beside it. `A2UIRenderer`
  resolves names through the registry, so registered blocks reach it
  automatically; its hand-written OpenUI primitive list is untouched.

## Failure modes

| Failure | Caught by |
|---|---|
| implementation registered, spec never reaches the prompt | boot assertion; otherwise invisible |
| spec registered, implementation missing | boot assertion; otherwise a blank section |
| edition shadows `Card` | refused at registration, name returned |
| two editions register one name | later layer refused; earlier wins, deterministically |
| two editions both claim `replace` | second refused wholesale, with the holder named |
| replaced block still reaches the renderer | every consumer reads `effectiveBlocks()`; pinned by test |
| edition unloads | `unregisterBlocks(source)` clears both halves |

## Open decisions

1. **Does the backend registry need per-owner scope?** A single-tenant desktop
   build does not. A multi-tenant deployment where one org has finance blocks
   and another does not would need the catalog keyed by owner, which is a
   larger change to prompt assembly.
2. **Layer order.** `CitationQualityPolicyRegistry` fixes `oss → commercial →
   distribution` and lets later layers tighten but not replace. The frontend
   now answers half of this: a layer may replace the *OSS block set* wholesale,
   but never another layer's blocks — the second `replace` is refused rather
   than won by order. The backend registry should mirror that rule when it is
   built, so the two sides cannot disagree about which set is live.
