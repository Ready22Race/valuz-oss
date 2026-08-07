/**
 * The edition seam for live data (M2). The renderer knows *when* a surface
 * declares data refs and *where* pushed updates go; only an edition knows how
 * to fetch a source. An edition registers one factory at boot — the same
 * moment it registers its blocks — and the renderer calls it per surface
 * that carries `/refs` declarations.
 *
 * The factory returns null to decline (no refs it recognises), or a handle
 * whose `stop()` the renderer calls on unmount. `refs` is handed over
 * unparsed: the slot grammar lives in the edition's own channel utilities,
 * and the renderer has no opinion about it.
 */

export interface GenUIDataHostHandle {
  stop: () => void;
}

export interface GenUIDataHostInput {
  surfaceId: string;
  /** The surface's `/refs` value, verbatim; the edition parses it. */
  refs: unknown;
  /** Push one A2UI message (typically updateDataModel) into the render stream. */
  push: (message: Record<string, unknown>) => void;
}

export type GenUIDataHostFactory = (
  input: GenUIDataHostInput,
) => GenUIDataHostHandle | null;

let factory: GenUIDataHostFactory | undefined;

export function registerGenUIDataHost(next: GenUIDataHostFactory): void {
  factory = next;
}

export function unregisterGenUIDataHost(): void {
  factory = undefined;
}

export function getGenUIDataHost(): GenUIDataHostFactory | undefined {
  return factory;
}
