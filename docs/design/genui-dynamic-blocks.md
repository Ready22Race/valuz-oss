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
blocks.ts ─▶ catalog.ts ─▶ gen_openui_prompt.mjs ─▶ two .txt assets
                                                     ↓
                                       backend package resources,
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
registerBlocks(source, blocks, { reserved }): RegisterResult
unregisterBlocks(source): void
subscribeBlocks(listener): () => void      // for useSyncExternalStore
```

`createValuzLibrary()` stops being a module constant and rebuilds when the
registry version changes; both renderers read it through `useSyncExternalStore`,
which is how this repo already handles module-level stores.

Two rules carry the weight:

**The spec is derived, never authored.** `describeBlock()` reads the same zod
schema and description the build-time generator reads. There is no second place
to keep in sync, so the halves cannot drift.

**Collisions are refused, not merged.** A registered name may not take an
OpenUI component's or a built-in block's. Merge order would decide it silently,
and a plugin shadowing `Card` breaks every document. Refused names come back in
`RegisterResult.rejected` so the caller can fail loudly.

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
generated OSS asset, the per-protocol split, the group structure — stays.

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
- **Protocol reach.** `A2UIRenderer` resolves names through `blockNames`, so
  registered blocks reach A2UI automatically. Its hand-written OpenUI component
  list is untouched.

## Failure modes

| Failure | Caught by |
|---|---|
| implementation registered, spec never reaches the prompt | boot assertion; otherwise invisible |
| spec registered, implementation missing | boot assertion; otherwise a blank section |
| edition shadows `Card` | refused at registration, name returned |
| two editions register one name | later layer refused; earlier wins, deterministically |
| edition unloads | `unregisterBlocks(source)` clears both halves |

## Open decisions

1. **Does the backend registry need per-owner scope?** A single-tenant desktop
   build does not. A multi-tenant deployment where one org has finance blocks
   and another does not would need the catalog keyed by owner, which is a
   larger change to prompt assembly.
2. **Layer order.** `CitationQualityPolicyRegistry` fixes `oss → commercial →
   distribution` and lets later layers tighten but not replace. Blocks are
   additive rather than restrictive, so the same order works, but "can a
   distribution layer replace a commercial block?" needs an answer before
   someone assumes one.
