"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readTextFromKeys } from "../lib/props";
import { toneText } from "../lib/tone";
import { KeyValueGroupSchema, KeyValueSchema } from "./schema";

export { KeyValueGroupSchema, KeyValueSchema } from "./schema";

export const KeyValue = defineComponent({
  name: "KeyValue",
  props: KeyValueSchema,
  description:
    "One labelled value: the field name on the left, the figure on the right. Use it for specification sheets, filing summaries, parameter tables — anywhere a reader scans for a named field rather than comparing magnitudes. " +
    "label is the field name, value the formatted figure, and unit the trailing unit kept out of value (\"4.2\" + \"M\", \"12.4\" + \"%\") so a column of figures still lines up; tone colours the value and should be left unset unless the value itself is good or bad news. " +
    "Always place KeyValues inside a KeyValueGroup — alone, one stretches to the full width. For a figure that is the point of the section use StatsCard, and for a term that needs a sentence of explanation use DescriptionList.",
  component: ({ props }) => {
    /*
     * Read through the alias readers rather than off `props` directly: nothing
     * validates props before they reach a block, so a field the model wrote as
     * `null`, as a number, or under a near-miss key arrives exactly as written.
     * `readTextFromKeys` turns all three into a string or an empty string.
     */
    const record = props as unknown as Record<string, unknown>;
    const label = readTextFromKeys(record, ["label", "term", "name", "key"]);
    const value = readTextFromKeys(record, ["value", "text"]);
    const unit = readTextFromKeys(record, ["unit", "suffix"]);

    // A pair with neither side is not an empty row, it is no row at all.
    if (!label && !value && !unit) return null;

    return (
      <div className="vgb-kv" data-slot="vgb-key-value">
        <span className="vgb-kv-label">{label}</span>
        <span
          className="vgb-kv-value"
          style={props.tone ? { color: toneText(props.tone) } : undefined}
        >
          {value}
          {unit ? <span className="vgb-kv-unit">{unit}</span> : null}
        </span>
      </div>
    );
  },
});

export const KeyValueGroup = defineComponent({
  name: "KeyValueGroup",
  props: KeyValueGroupSchema,
  description:
    "Two-column grid of KeyValue pairs that collapses to one column in a narrow container. Reach for it whenever you are about to list four or more named fields — it is denser and faster to scan than a Table when every row is one label and one figure. " +
    "children is an array of KeyValue. Keep related fields adjacent: the grid fills left-to-right, so the reading order is the order you write them in.",
  component: ({ props, renderNode }) => {
    const children = props.children ?? [];
    // Nothing to lay out is not a reason to draw a frame around nothing.
    if (children.length === 0) return null;
    return (
      /*
       * Two elements, not one. An element carrying `container-type` cannot
       * answer a query about itself, so the shell measures and the grid inside
       * it responds — collapsing the columns from a rule written on the shell
       * would silently resolve against whatever container sits further up.
       */
      <div className="vgb-kv-group" data-slot="vgb-key-value-group">
        <div className="vgb-kv-grid">{renderNode(children)}</div>
      </div>
    );
  },
});
