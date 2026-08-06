"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";
import { readText } from "../lib/props";
import { EmptyStateSchema } from "./schema";

export { EmptyStateSchema } from "./schema";

export const EmptyState = defineComponent({
  name: "EmptyState",
  props: EmptyStateSchema,
  description:
    "A quiet, centred notice that there is nothing to show — an empty result set, a filter that matched nothing, a period with no filings. " +
    "Use it instead of writing \"no data\" as a sentence, and only when the absence is the answer; if something went wrong, use ErrorState. " +
    "title is one short line naming what is missing (\"No filings in this period\"), description is an optional sentence saying why or what would change it, and icon is any lucide-react icon name (\"inbox\", \"search-x\") — never an emoji. " +
    "It is presentational only: it carries no button and no way to add, refresh or retry anything, so do not describe one.",
  component: ({ props }) => {
    // Props arrive from a model, not from code: the renderer does not validate
    // against the schema, so an optional field can turn up as `null` or an
    // empty string. Reading through `readText` collapses all three to "",
    // which the guards below drop — an EmptyState without a description must
    // close up rather than leave a gap where one would have been.
    const raw = props as unknown as Record<string, unknown>;
    const title = readText(raw.title).trim();
    const description = readText(raw.description).trim();

    return (
      <div className="vgb-state vgb-empty-state" data-slot="vgb-empty-state">
        {/* Renders nothing when `icon` is absent or names an icon lucide does
            not ship, so the flex gap above the title collapses with it. */}
        <BlockIcon name={readText(raw.icon) || undefined} className="vgb-state-icon" size="1.5em" />
        {title ? <p className="vgb-state-title">{title}</p> : null}
        {description ? <p className="vgb-state-text">{description}</p> : null}
      </div>
    );
  },
});
