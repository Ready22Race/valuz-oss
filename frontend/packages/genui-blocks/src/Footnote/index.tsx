"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readText, readTextFromKeys } from "../lib/props";
import { FootnoteListSchema, FootnoteSchema } from "./schema";

export { FootnoteListSchema, FootnoteSchema } from "./schema";

export const Footnote = defineComponent({
  name: "Footnote",
  props: FootnoteSchema,
  description:
    "One numbered aside of your own: a caveat, an assumption, a unit convention, a definition too small to interrupt the sentence with. " +
    "index is the number the marker in the prose carries, and text the note itself — keep it to a sentence or two. " +
    "This is not a Citation: a Citation points at somebody else's source and belongs with a SourceList, while a Footnote is the answer's own remark about its own reasoning. " +
    "Always place Footnotes inside a FootnoteList.",
  component: ({ props }) => {
    const record = props as unknown as Record<string, unknown>;
    const index = readTextFromKeys(record, ["index", "number", "n"]) || readText(props.index);
    const text = readTextFromKeys(record, ["text", "note", "content"]);

    if (!text && !index) return null;

    return (
      <li className="vgb-footnote" data-slot="vgb-footnote">
        {/* The number is written out rather than left to the <ol> counter: the
            markers in the prose decide it, and a list that renumbers itself
            from 1 would silently disagree with them. */}
        <span className="vgb-footnote-index">{index}</span>
        <span className="vgb-footnote-text">{text}</span>
      </li>
    );
  },
});

export const FootnoteList = defineComponent({
  name: "FootnoteList",
  props: FootnoteListSchema,
  description:
    "The block of notes at the foot of an answer. children is an array of Footnote, in index order. " +
    "Close a long or heavily-qualified answer with one of these rather than parenthesising every caveat inline — the argument stays readable and the qualifications stay available. " +
    "Sources go in a SourceList or CondensedSources instead; this list is for the answer's own remarks.",
  component: ({ props, renderNode }) => {
    const children = props.children ?? [];
    // No notes is no list. An empty bordered strip reads as a rendering fault.
    if (children.length === 0) return null;
    return (
      <ol className="vgb-footnote-list" data-slot="vgb-footnote-list">
        {renderNode(children)}
      </ol>
    );
  },
});
