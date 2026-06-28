import type { SkillView } from "@valuz/core";

/** A composer `/`-picker item (structurally matches `@valuz/ui`'s
 *  `SkillSearchItem`, kept dependency-free here). */
export interface AgentSkillItem {
  id: string;
  name: string;
  slug?: string;
  description?: string;
}

/**
 * Resolve an agent's stored skill entries to composer `/`-picker items.
 *
 * Agents persist skills as either a slug (`"sector-overview"`) or an absolute
 * path (`"/Users/.../skills/weather-query-v2"`) — the directory basename is the
 * slug. Each entry is matched against the provided skill catalogs (first match
 * wins, so pass higher-priority catalogs first) to recover a display
 * name/description; an entry the catalogs don't know still resolves to its bare
 * slug so nothing silently disappears. Deduped by slug, order preserved.
 */
export function resolveAgentSkillItems(
  entries: readonly string[] | null | undefined,
  catalogs: readonly (readonly SkillView[])[],
): AgentSkillItem[] {
  if (!entries?.length) return [];
  const bySlug = new Map<string, SkillView>();
  for (const cat of catalogs) {
    for (const s of cat) {
      if (s.slug && !bySlug.has(s.slug)) bySlug.set(s.slug, s);
    }
  }
  const items: AgentSkillItem[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const slug = entry.includes("/")
      ? (entry.split("/").filter(Boolean).pop() ?? entry)
      : entry;
    if (!slug || seen.has(slug)) continue;
    seen.add(slug);
    const meta = bySlug.get(slug);
    items.push(
      meta
        ? {
            id: meta.id,
            name: meta.name,
            slug: meta.slug,
            description: meta.description,
          }
        : { id: slug, name: slug, slug },
    );
  }
  return items;
}
