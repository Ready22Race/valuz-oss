"use client";

import { defineComponent } from "@openuidev/react-lang";

import { ContextCardSchema } from "./schema";

export { ContextCardSchema } from "./schema";

export const ContextCard = defineComponent({
  name: "ContextCard",
  props: ContextCardSchema,
  description:
    "Explanatory context to set beside a chart or a table: a title, the body text, and an optional source line for attribution (\"Source: Q3 filings, 2026\"). " +
    "Use it for the caveat, the methodology, or the definition a reader needs to trust the figure next to it — not for the finding itself. " +
    "source renders quietly at the foot of the card; leave it out when the data is the user's own.",
  component: ({ props }) => (
    <div className="vgb-card vgb-context-card" data-slot="vgb-context-card">
      <h3 className="vgb-title vgb-card-title">{props.title}</h3>
      <p className="vgb-body">{props.body}</p>
      {props.source ? <p className="vgb-card-source">{props.source}</p> : null}
    </div>
  ),
});
