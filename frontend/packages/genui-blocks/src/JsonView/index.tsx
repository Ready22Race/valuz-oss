"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readTextFromKeys } from "../lib/props";
import { formatJson } from "./format";
import { JsonViewSchema } from "./schema";

export { JsonViewSchema } from "./schema";

export const JsonView = defineComponent({
  name: "JsonView",
  props: JsonViewSchema,
  description:
    "Structured data as indented, read-only text — an API response, a tool result, a config object, the raw shape behind a figure. Nothing here is interactive: there is no expanding, collapsing or copying, so use it when the shape of the data is the point, and a Table or DataList when its contents are. " +
    "value is the data (an object or array; a JSON string is parsed and shown as data). title labels the block. " +
    "collapsedDepth caps how deep the nesting is printed, 3 by default — anything below the cap shows as { … }, and long strings, wide levels and very large objects are truncated with the same marker, so what you see is never mistaken for all there is. " +
    "Raise it only when the answer turns on something deeper; a large object printed in full is unreadable, not more informative.",
  component: ({ props }) => {
    const record = props as unknown as Record<string, unknown>;
    const title = readTextFromKeys(record, ["title", "label"]);
    /*
     * Formatting happens on every render and is deliberately not memoised: the
     * caps in `format.ts` bound the work to a few hundred lines, which is
     * cheaper than the identity comparison a memo would need on a prop that is
     * a fresh object every time the document re-parses.
     */
    const text = formatJson(record.value ?? record.data ?? record.json, record.collapsedDepth);

    return (
      <figure className="vgb-json" data-slot="vgb-json-view">
        {title ? <figcaption className="vgb-json-title">{title}</figcaption> : null}
        {/* Long lines scroll inside this box. Without it the whole chat column
            scrolls sideways, which takes the composer off screen. */}
        <div className="vgb-scroll-x">
          {/*
           * Text, always. The formatter returns a string and React escapes it,
           * so a value containing "<script>" renders as those characters — the
           * block has no HTML path, and no `eval`, `Function` or dynamic import
           * anywhere behind it.
           */}
          <pre className="vgb-json-body">
            <code>{text}</code>
          </pre>
        </div>
      </figure>
    );
  },
});
