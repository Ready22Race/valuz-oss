"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon, isKnownIcon } from "../lib/icon";
import { readText } from "../lib/props";
import { toneBorder, toneSurface, toneText } from "../lib/tone";
import { ErrorStateSchema } from "./schema";

export { ErrorStateSchema } from "./schema";

export const ErrorState = defineComponent({
  name: "ErrorState",
  props: ErrorStateSchema,
  description:
    "Something went wrong: a failure panel in the danger tone, with room for the technical line underneath. " +
    "title is the plain-language failure (\"Could not load the filing\"), description is an optional sentence of context a reader can act on, and detail is the raw technical line — an exception, a status code, a stack trace — rendered in code type and left selectable so it can be copied into a bug report. " +
    "icon is any lucide-react icon name and defaults to circle-alert; never an emoji. " +
    "Use EmptyState instead when nothing went wrong and there is simply nothing to show. " +
    "This block cannot retry, reload or report anything — it has no button and no handler behind it, so never tell the reader to press one.",
  component: ({ props }) => {
    // The renderer does not validate props against the schema, so every
    // optional field can arrive as `null`, `""` or a number. `readText`
    // collapses those to "" and the guards below drop the row entirely rather
    // than leaving an empty line in the panel.
    const raw = props as unknown as Record<string, unknown>;
    const title = readText(raw.title).trim();
    const description = readText(raw.description).trim();
    // Only the ends are trimmed: `detail` is often a stack trace whose interior
    // indentation is what makes it readable.
    const detail = readText(raw.detail).replace(/^\s+|\s+$/g, "");
    // Unlike every other icon prop in the package, this one falls back rather
    // than rendering nothing: the mark is what makes a failure panel legible at
    // a glance, and the model invents icon names often enough that "whatever it
    // said, else circle-alert" is the only spelling that always draws one.
    const named = readText(raw.icon).trim();
    const icon = isKnownIcon(named) ? named : "circle-alert";

    return (
      <div
        className="vgb-state vgb-state-panel"
        data-slot="vgb-error-state"
        // Fixed danger tone, taken from the same table every other block reads
        // so a failure panel matches a danger-tone tag beside it.
        style={{ backgroundColor: toneSurface("danger"), borderColor: toneBorder("danger") }}
      >
        <div className="vgb-state-heading">
          <span className="vgb-state-icon-slot" style={{ color: toneText("danger") }}>
            <BlockIcon name={icon} className="vgb-state-icon" />
          </span>
          {title ? (
            <p className="vgb-state-title" style={{ color: toneText("danger") }}>
              {title}
            </p>
          ) : null}
        </div>
        {description ? <p className="vgb-state-text">{description}</p> : null}
        {detail ? (
          // A `<pre>` rather than a styled span: `detail` carries newlines and
          // leading indentation that are part of reading a trace. It wraps and
          // scrolls inside its own box (see `.vgb-state-detail`) so a 4,000
          // character stack never widens the column it sits in.
          <pre className="vgb-state-detail" data-slot="vgb-error-state-detail">
            <code>{detail}</code>
          </pre>
        ) : null}
      </div>
    );
  },
});
