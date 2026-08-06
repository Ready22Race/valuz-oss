"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readTextFromKeys } from "../lib/props";
import { alignStyle, typeScale } from "../lib/tone";
import { RichTextSchema } from "./schema";

export { RichTextSchema } from "./schema";

/**
 * A paragraph, set.
 *
 * The defining property is what it does *not* do: it never interprets its own
 * text. No HTML, no Markdown, no template syntax — `text` reaches the DOM as a
 * React text child, which the renderer escapes, so `<img onerror=…>` in model
 * output is five words about an image tag rather than an image tag.
 *
 * That is a security boundary, not a simplification. This text is
 * attacker-influenced whenever the model has read attacker-controlled input (a
 * fetched page, a pasted document, a tool result), and every HTML path in a
 * generated document is a path an injected string can walk. OpenUI already
 * ships exactly one such path — `MarkDownRenderer`, which sanitises — and one
 * hardened renderer is defensible where two are not: the second one is the one
 * nobody remembers to audit.
 *
 * So the emphasis here is typographic and applies to the whole run: alignment
 * and a step on the type scale. Anything that needs bold inside a sentence, a
 * heading, a list or a link is a `MarkDownRenderer`, and the description says
 * so in the words the model will read.
 */
export const RichText = defineComponent({
  name: "RichText",
  props: RichTextSchema,
  description:
    "A paragraph of plain prose, set at a chosen size and alignment. Use it for a standfirst, a caption, a pull sentence, or any single run of text that wants to read a step larger or centred. " +
    "text is rendered literally and is never interpreted: HTML tags and Markdown syntax appear as the characters you typed, so writing \"**bold**\" or \"<b>bold</b>\" here shows the asterisks and the angle brackets. Line breaks in text are kept. " +
    "align is left|center|right and size is small|medium|large. " +
    "For anything that actually needs formatting — bold or italic inside a sentence, headings, lists, tables, links, code — use MarkDownRenderer, which is the one component that parses markup. Do not reach for this block and hope the markup renders.",
  component: ({ props }) => {
    const record = props as unknown as Record<string, unknown>;
    const text = readTextFromKeys(record, ["text", "content", "body", "value"]);

    if (!text) return null;

    return (
      <p
        className="vgb-richtext"
        data-slot="vgb-rich-text"
        style={{ ...alignStyle(props.align), fontSize: typeScale(props.size) }}
      >
        {text}
      </p>
    );
  },
});
