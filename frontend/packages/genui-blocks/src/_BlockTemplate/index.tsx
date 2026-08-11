"use client";

import { defineComponent } from "@openuidev/react-lang";

import { toneSurface, toneText } from "../lib/tone";
import { BlockTemplateSchema } from "./schema";

export { BlockTemplateSchema };

/**
 * Template block — copy this file and its schema into src/<YourBlock>/.
 * The leading underscore keeps the directory out of the block registry.
 *
 * Walk through the comments once, then delete them in the real block.
 * AUTHORING.md is the source of truth for everything below.
 */
export const BlockTemplate = defineComponent({
  // Must match the exported const and be unique across the package AND
  // OpenUI's own components (Card, Stack, Table, Tabs, Steps, ...).
  name: "BlockTemplate",
  props: BlockTemplateSchema,
  // `description` is PROMPT TEXT, fed verbatim to the model — not a code
  // comment. Say when to reach for this block, what each prop expects, and
  // name sibling blocks it composes with.
  description:
    "Template block for new components. Describe when the model should use it, what each prop expects, and a good example value. Replace this before registering.",
  component: ({ props, renderNode }) => {
    const title = typeof props.title === "string" ? props.title : "";

    return (
      // data-slot gives tests and host stylesheets a stable hook.
      <div
        data-slot="vgb-block-template"
        className="vgb-block-template"
        style={{
          // Inline style only for data-driven values (a tone from a prop),
          // never for static design — those go in styles/<family>.css.
          backgroundColor: toneSurface(props.tone),
          color: toneText(props.tone),
        }}
      >
        <span className="vgb-block-template-title">{title}</span>
        {/* Children render through renderNode — never map over them yourself. */}
        {renderNode(props.children)}
      </div>
    );
  },
});
