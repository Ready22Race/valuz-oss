import { builtInBlocksSuppressed } from "./registry";

/**
 * Which layer of the vocabulary a single generation is offered.
 *
 * The library has three layers — the root, OpenUI's primitives, and the
 * semantic blocks — and a given request rarely needs all of them. Narrowing is
 * worth a parameter because the catalog is the bulk of every `generate_ui`
 * prompt: offering fewer components costs fewer tokens *and* makes the model
 * choose better, since a shorter menu is an easier menu.
 *
 * - `all` — everything. The default, and right when the shape of the answer is
 *   not known up front.
 * - `edition` — the root plus the semantic blocks the active edition ships.
 *   The curated look, at a fraction of the catalog.
 * - `atoms` — the root plus OpenUI's primitives. For generic UI (forms,
 *   plain charts, tables) that no semantic block covers.
 */
export type ComponentScope = "all" | "edition" | "atoms";

export const COMPONENT_SCOPES: readonly ComponentScope[] = ["all", "edition", "atoms"];

/**
 * The scope actually available, which is not always the one asked for.
 *
 * An edition holding `replace` has removed OpenUI's primitives, so `atoms` and
 * `all` have nothing left to widen to — both collapse onto what is live. Asking
 * for a layer that no longer exists must narrow the offer, never resurrect it:
 * a component named in the prompt but absent from the renderer is the silent
 * failure this whole seam exists to prevent.
 */
export function resolveScope(scope: ComponentScope): ComponentScope {
  return builtInBlocksSuppressed() ? "edition" : scope;
}
