# Authoring a block

Read this before adding a component. `src/MiniCard/` is the reference
implementation — when this document and that directory disagree, the directory
wins.

## File layout

```
src/<ComponentName>/
  schema.ts   # zod/v4 props schema
  index.tsx   # defineComponent(...) — one file may define several related blocks
src/styles/<family>.css   # this family's styles, imported from src/styles.css
```

A family (`MiniCard` + `MiniCardBlock`, `Citation` + `CitationList`) shares one
directory and one stylesheet.

## schema.ts

```ts
import { z } from "zod/v4";
import { ToneSchema, TrendSchema } from "../lib/schema";

export const ThingSchema = z.object({
  title: z.string(),
  body: z.string().optional(),
  tone: ToneSchema.optional(),
  children: z.array(z.unknown()),   // child slot — see below
});
```

- Import `z` from `"zod/v4"`, never `"zod"`. `@openuidev/react-lang` is built
  against the v4 surface, and a mismatched import makes the schema types
  structurally incompatible in a way TypeScript reports far from the cause.
- Reuse `ToneSchema` / `TrendSchema` / `AlignSchema` / `SizeSchema` /
  `ImagePositionSchema` from `../lib/schema` instead of writing new enums. Each
  enum member is copied into the LLM prompt once per block that uses it, so
  synonyms cost prompt budget for no gain.
- Child slots are `z.array(z.unknown())`. A `.ref` union would be more precise,
  but OpenUI's refs are only exported for OpenUI's own components; a
  `z.unknown()` slot accepts both those and other blocks.
- Keep props flat and few. Every prop is prompt surface: if the model would
  have to guess when to set it, it does not belong.
- **Key order is load-bearing in the tests, and getting it wrong fails
  silently.** A2UI passes props by name, so the wire does not care; the render
  harness (`createValuzLibrary`, see below) speaks OpenUI Lang, which is
  positional and binds in zod key order. Declaring `{ label?, children }` makes
  `Thing([a, b])` assign the array to `label` and leave `children` empty — no
  parse error, no type error, just an empty block. Put required props first,
  `children` before optional scalars, and match the order a human would write
  the call in; OpenUI's own `Card(children, variant?)` is the pattern.

## index.tsx

```tsx
"use client";
import { defineComponent } from "@openuidev/react-lang";
import { ThingSchema } from "./schema";
export { ThingSchema } from "./schema";

export const Thing = defineComponent({
  name: "Thing",
  props: ThingSchema,
  description: "...",
  component: ({ props, renderNode }) => (
    <div className="vgb-thing">{renderNode(props.children)}</div>
  ),
});
```

- `name` must match the exported const and be unique across the package **and**
  across OpenUI's own components (`Card`, `Stack`, `Table`, `Tabs`, `Steps`,
  `Callout`, `TextContent`, `MarkDownRenderer`, `Image`, `Form`, …). A
  collision silently shadows the OpenUI component for every document.
- **`description` is prompt text, not a code comment.** It is fed verbatim to
  the model. Write it as instructions: when to reach for this block, what each
  prop expects, what a good value looks like. Name sibling blocks it composes
  with. Aim for two or three sentences — this is the only thing standing
  between the model and a wrong choice.
- Children render through `renderNode(props.children)`. Never map over them
  yourself.
- Use `../lib/tone` helpers (`toneText`, `toneSurface`, `toneBorder`,
  `trendTone`, `trendGlyph`, `typeScale`, `alignStyle`) rather than re-deriving
  token names.
- Set `data-slot="vgb-<kebab-name>"` on the root element so tests and host
  stylesheets have a stable hook.

## Styling

Write rules in `src/styles/<family>.css` and add one `@import` line to
`src/styles.css`. Never add rules to `src/styles.css` itself.

- Prefix every class `.vgb-`.
- Colour, spacing, radius, type: `--openui-*` custom properties only. No hex,
  no `rgb()`, no Tailwind classes. Verified names include
  `--openui-space-{3xs,2xs,xs,s,sm,m,ml,l,xl,2xl,3xl}`,
  `--openui-radius-{none,xs,s,m,l,xl,2xl,3xl,full}`,
  `--openui-font-size-{2xs,xs,sm,md,lg,xl,2xl,3xl,4xl,5xl}`,
  `--openui-font-{body,heading,label,numbers,code}`,
  `--openui-font-weight-{regular,medium,bold,heavy}`,
  `--openui-text-neutral-{primary,secondary,tertiary}`,
  `--openui-{background,foreground,highlight,highlight-subtle,border-default}`,
  and the tone families wrapped by `lib/tone.ts`.
- Responsive behaviour uses `@container vgb (max-width: …)`, never
  `@media`. Blocks live in a chat column whose width has nothing to do with
  the viewport's. `.vgb-root` establishes the container; the two conventional
  breakpoints are `48rem` (two-up) and `30rem` (one-up).
- Wide content (tables, charts) scrolls inside its own box — reuse
  `.vgb-scroll-x`. The page body must never scroll sideways.

- **Two components must never share one schema object.** The library keys
  registration off the schema, so a second `defineComponent` given the same
  object silently replaces the first: one name renders the other's component,
  with both names still present in the library and nothing reported anywhere.
  If two blocks want the same props, give each its own schema from a factory
  (`waterfallProps()`, `categoryBarProps()`) rather than sharing a const.

## Layout rules learned the hard way

Each of these cost a screenshot and a debugging session. They fail silently —
nothing errors, the page is just wrong.

- **A width floor must concede to its container.** Write
  `min-width: min(320px, 100%)`, never `min-width: 320px`. A floor wider than
  the container does not shrink the container, it overflows and paints over
  whatever is beside it.
- **A wrapping block is its own query container.** Put
  `container-type: inline-size; container-name: vgb;` on the block that wraps
  children, not only on an outer root. A query resolved against the whole
  document tells a tile in a half-width column that it has the full width.
- **Numbers never break.** `white-space: nowrap` on any figure. The host
  stylesheet sets `overflow-wrap: anywhere` on every span in scope, which is
  right for prose and wrong for a value — "26,58 / 4" reads as a different
  number, not a squeezed one.
- **Composite typography tokens must be mapped.** `--openui-text-heading-lg`
  and friends are not derived from the primitives; unmapped they keep OpenUI's
  Inter defaults. A test asserts every one is mapped.

## Icons

`icon` props take a lucide-react icon name and render through
`BlockIcon` from `../lib/icon`. Both the component spelling (`TrendingUp`) and
the id (`trending-up`) resolve; an unknown name renders nothing. Never accept
an emoji as an icon, and say so in the description — a block without an `icon`
prop is a block meant to have no icon.

## Constraints

- **No `@valuz/*` imports.** This package sits below `@valuz/ui`; importing
  upward creates a cycle. Only `@openuidev/*`, `react`, `zod` are available.
- **Do not edit `src/blocks.ts`, `src/index.ts`, or `src/styles.css` rules.**
  Registration is assembled centrally to avoid concurrent edits; report your
  component names and suggested group instead.
- Blocks must render from props alone — no data fetching, no timers, no
  `useEffect` that touches anything outside the component.
- Prefer one component with a variant prop over near-duplicate components. If
  two layouts differ only in density or alignment, that is a prop.

## Registering blocks from an edition

An edition adds blocks without forking this package by calling `registerBlocks`
at startup. Two modes, chosen per registration:

```ts
registerBlocks("finance", financeBlocks, {
  mode: "append",           // default — sits alongside the built-in blocks
  groupName: "Finance",     // a block in no group is never described to the model
  reserved: openuiNames,    // the host's OpenUI component names
});

registerBlocks("finance", financeBlocks, { mode: "replace" });
```

- **`append`** — the edition's blocks join the built-in set. A name already
  taken by a built-in or by another source is refused (returned in
  `rejected`), never merged, because merge order would decide it silently.
- **`replace`** — the edition owns the whole vocabulary: the built-in blocks
  go, and so does every OpenUI component **except the root**. A vertical with a
  curated set then pays prompt budget for its own components only. Only one
  source may hold `replace`; a second registration is refused wholesale.

The root is the one name `replace` still refuses. `createLibrary` throws when
its root is missing from the component list, and a document that cannot resolve
its root renders nothing at all — so `Stack` survives every mode while
everything above it is the edition's to define, including names like `Card` or
`MiniCard` that a built-in used to hold.

The root also keeps its prompt group (narrowed to itself), because grouping is
what puts a component into the prompt's signature section; its notes are
filtered to drop any that explain a component `replace` removed.

Both halves move together: `unregisterBlocks(source)` takes the implementation
and its prompt group away, and the built-ins come back.

## The catalog

A block reaches the model through one generated asset — the A2UI block catalog,
built from every block's name, zod schema and `description`. Regenerate it after
adding or changing a block:

```bash
pnpm --filter @valuz/ui gen:genui-catalog
```

Forgetting this is the quiet failure: the block renders when named, but nothing
ever tells the model it exists, so it is never named.

`generate_ui` takes a `components` argument that narrows what a single
generation is offered — `all` (default), `edition` (root + blocks) or `atoms`
(OpenUI primitives). It is assembled backend-side, so nothing here changes; what
it means for you is that your `description` is the block's entire pitch under
`edition`, where the primitives are not there to fall back on.

## Verifying

```bash
cd frontend
pnpm exec tsc --noEmit -p packages/genui-blocks/tsconfig.json
pnpm exec vitest run --config vitest.config.ts packages/genui-blocks
```

### The render harness

Tests render through `createValuzLibrary()` and OpenUI's `<Renderer>`, not
through A2UI. A2UI is the product's only wire protocol, but its renderer lives
in `@valuz/ui` — above this package, so unreachable from here. The library
drives the identical component objects and zod schemas the A2UI adapter drives,
so it is the closest proof available from inside. The one place the two differ
is argument binding, which is why key order matters above.
