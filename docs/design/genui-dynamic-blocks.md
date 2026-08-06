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
registerBlocks(source, blocks, { reserved, mode }): RegisterResult
unregisterBlocks(source): void
subscribeBlocks(listener): () => void      // for useSyncExternalStore
effectiveBlocks(): BlockComponent[]        // what is actually live
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
edition's to take, including ones a built-in used to hold. The root keeps a
prompt group narrowed to itself — grouping is what puts a component into the
signature section, and an undocumented root leaves the model guessing at the
one component every document begins with. Its notes are filtered to drop any
that explain a removed component, because a prompt describing a `Modal` that no
longer exists is worse than one that says nothing.

Only one source may hold `replace`; a second registration is refused wholesale,
because "which edition's set is live" must have exactly one answer.
Unregistering the replacing source restores the built-ins.

Suppression has to reach **every** consumer or a replaced component leaks back
through whichever path was missed — the library's components, its prompt
groups, and A2UI's name resolution all derive from the registry rather than
from `blockComponents` or the hand-listed OpenUI names. A2UI is where this is
easiest to forget, since it is the second protocol, so a test pins it.

### Narrowing per call: the `components` argument

Replace is a startup decision. The same question comes up per generation, and
`generate_ui` answers it with a `components` argument:

| value | offered | OpenUI Lang prompt |
|---|---|---|
| `all` (default) | everything | ~90k chars |
| `edition` | root + the active edition's blocks | ~70k |
| `atoms` | root + OpenUI's primitives | ~20k |

The lever is worth having because the catalog *is* the prompt: a request the
agent already knows will be a form, or a plain table, pays for a hundred and
fifty component signatures it will not use. A shorter menu also makes the model
choose better.

Two properties keep it safe:

**Narrowing is prompt-side only.** The renderer keeps accepting every component
it ever accepted, so a narrow prompt can never produce a payload the client
cannot draw. The dangerous direction — describing something that cannot render
— stays closed.

**A scope can only narrow, never widen.** Under a `replace` edition the
primitives are gone from the renderer as well, so `atoms` and `all` both
collapse onto what is live (`resolveScope`). Asking for a layer that no longer
exists must not resurrect it in the prompt.

Everything the scope withholds is withheld consistently: group notes, examples,
and the "if the data has no chart series, fall back to…" advice are all filtered
against the offered set. An example is the strongest signal in a prompt, so one
calling an absent component is the strongest way to teach the wrong thing.

The three OpenUI Lang prompts are generated as three assets by the same
generator run, rather than filtered at runtime — the generator already holds
the group and signature structure, and re-deriving it in Python would be
invisible when subtly wrong.

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
| two editions both claim `replace` | second refused wholesale, with the holder named |
| replaced block still reaches one protocol | every consumer reads `effectiveBlocks()`; pinned by test |
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
