"use client";

import { defineComponent } from "@openuidev/react-lang";
import type { ReactNode } from "react";

import { readText, toArray } from "../lib/props";
import { cssLength, cssRatio, gapSpace, justifyContent, spacerSpace } from "./css";
import {
  AspectRatioSchema,
  ClusterSchema,
  CollapsibleSchema,
  DashboardGridSchema,
  DividerSchema,
  InlineSchema,
  PageFooterSchema,
  PageHeaderSchema,
  PageSchema,
  ScrollAreaSchema,
  SpacerSchema,
} from "./schema";

export {
  AspectRatioSchema,
  ClusterSchema,
  CollapsibleSchema,
  DashboardGridSchema,
  DividerSchema,
  InlineSchema,
  PageFooterSchema,
  PageHeaderSchema,
  PageSchema,
  ScrollAreaSchema,
  ScrollAxisSchema,
  SpacerSchema,
} from "./schema";
export type { ScrollAxis } from "./schema";

/*
 * The layout family. No block here is called `Layout`, `Col`, `Content`,
 * `Separator` or `Card` — OpenUI already ships those names, and a block that
 * reuses one silently replaces the OpenUI component for every document.
 *
 * Three invariants run through every component below. They are the ones that
 * fail without an error, so they are restated at each site rather than trusted
 * to the reader's memory:
 *
 *  1. **Nothing to show means nothing rendered.** These blocks carry no content
 *     of their own; an empty one would paint as a padded frame around nothing,
 *     and the model emits empty containers routinely — a Page whose sections
 *     all turned out empty, a Cluster of tags for an entity that had none.
 *  2. **Optional text is missing, empty *or* null.** Props are handed to the
 *     renderer unvalidated, so `""` and `null` both arrive; both must read as
 *     absent, or a Page with no subtitle keeps the gap where one would have been.
 *  3. **A child never widens its parent.** Every container either wraps, or
 *     scrolls, or clips inside its own box. The page must never scroll sideways.
 */

/* ── Shared heading area ──────────────────────────────────────────── */

interface HeadingText {
  title: string;
  subtitle: string;
  meta: string;
}

function readHeading(props: {
  title?: unknown;
  subtitle?: unknown;
  meta?: unknown;
}): HeadingText {
  return {
    title: readText(props.title).trim(),
    subtitle: readText(props.subtitle).trim(),
    meta: readText(props.meta).trim(),
  };
}

/**
 * The title block shared by Page and PageHeader.
 *
 * Each line is emitted only when it has text — an element rendered for an
 * absent subtitle still contributes its line-height and the gap above it, which
 * is how a heading area ends up with a hole in it for a prop nobody set.
 */
function HeadingLines({ title, subtitle, meta }: HeadingText): ReactNode {
  if (!title && !subtitle && !meta) return null;
  return (
    <div className="vgb-page-head-text">
      {title ? <h2 className="vgb-page-title">{title}</h2> : null}
      {subtitle ? <p className="vgb-page-subtitle">{subtitle}</p> : null}
      {meta ? <p className="vgb-page-meta">{meta}</p> : null}
    </div>
  );
}

/* ── Document frame ───────────────────────────────────────────────── */

export const Page = defineComponent({
  name: "Page",
  props: PageSchema,
  description:
    "The document frame: a padded column with an optional heading area, and the right root for a laid-out answer that has a title and several sections. " +
    "children is the body — stack the answer's blocks in it (DashboardGrid, MiniCardBlock, TextContent, charts, Divider between movements). title is the answer's heading, subtitle a one-line framing, meta the date or scope line under it; omit the ones you have nothing for rather than passing an empty string. " +
    "It is a frame, not a window: it never scrolls and never fixes its own height, so put a long table inside a ScrollArea rather than expecting the Page to contain it. Use ReportDocument instead when the deliverable is a multi-page printable report.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    const heading = readHeading(props);
    const hasHeading = Boolean(heading.title || heading.subtitle || heading.meta);
    if (!hasHeading && children.length === 0) return null;
    return (
      <section className="vgb-page" data-slot="vgb-page">
        {hasHeading ? (
          <header className="vgb-page-head">
            <HeadingLines {...heading} />
          </header>
        ) : null}
        {children.length > 0 ? (
          <div className="vgb-page-body">{renderNode(children)}</div>
        ) : null}
      </section>
    );
  },
});

export const PageHeader = defineComponent({
  name: "PageHeader",
  props: PageHeaderSchema,
  description:
    "The heading area on its own, for when the title needs to sit inside a Page's body rather than at its top — a second movement in a long answer, or a header that carries a status tag beside it. " +
    "Arguments are positional and the title comes first: PageHeader(\"Q3 Review\", \"Group revenue and margin\", \"As of 30 Jun 2026\"). children is an optional trailing slot for a small block beside the title (an IconTag, a Cluster of tags) — leave it out unless there is one. " +
    "A Page given a title already renders this; do not add both.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    const heading = readHeading(props);
    const hasHeading = Boolean(heading.title || heading.subtitle || heading.meta);
    if (!hasHeading && children.length === 0) return null;
    return (
      <header className="vgb-page-head" data-slot="vgb-page-header">
        <HeadingLines {...heading} />
        {children.length > 0 ? (
          <div className="vgb-page-head-slot">{renderNode(children)}</div>
        ) : null}
      </header>
    );
  },
});

export const PageFooter = defineComponent({
  name: "PageFooter",
  props: PageFooterSchema,
  description:
    "The closing line of a Page: the source, the as-of date, the caveat. Arguments are positional with the child slot first, so the common call passes an empty array — PageFooter([], \"Source: exchange filings, as of 30 Jun 2026\"). " +
    "note is that line; children is for the rarer case where the footer holds blocks (a CondensedSources, a row of IconTags). " +
    "Keep it to one sentence — a paragraph of caveats belongs in the body, and per-claim attribution belongs on a Citation.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    const note = readText(props.note).trim();
    if (!note && children.length === 0) return null;
    return (
      <footer className="vgb-page-foot" data-slot="vgb-page-footer">
        {children.length > 0 ? (
          <div className="vgb-page-foot-slot">{renderNode(children)}</div>
        ) : null}
        {note ? <p className="vgb-page-note">{note}</p> : null}
      </footer>
    );
  },
});

/* ── Runs ─────────────────────────────────────────────────────────── */

export const Inline = defineComponent({
  name: "Inline",
  props: InlineSchema,
  description:
    "Lays its children on one line with a gap between them, wrapping onto the next line only when they no longer fit. Use it for a heading beside a tag, a figure beside its label, a row of two or three blocks that belong together. " +
    "gap is small | medium | large (default medium) and align is left | center | right, placing the run on its line. " +
    "Cluster is the version for many small items such as tags; DashboardGrid is the one for equal-width columns of cards.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    if (children.length === 0) return null;
    return (
      <div
        className="vgb-inline"
        data-slot="vgb-inline"
        style={{
          gap: gapSpace(props.gap, "medium"),
          justifyContent: justifyContent(props.align),
        }}
      >
        {renderNode(children)}
      </div>
    );
  },
});

export const Cluster = defineComponent({
  name: "Cluster",
  props: ClusterSchema,
  description:
    "A wrapping run of many small items — tags, chips, IconTags, short links — set tighter than Inline and always allowed to wrap. Reach for it whenever the count is open-ended and each item is a word or two. " +
    "gap is small | medium | large and defaults to small; children is the array of items. " +
    "Use Inline instead for two or three larger blocks that share a line.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    if (children.length === 0) return null;
    return (
      <div
        className="vgb-cluster"
        data-slot="vgb-cluster"
        style={{ gap: gapSpace(props.gap, "small") }}
      >
        {renderNode(children)}
      </div>
    );
  },
});

export const DashboardGrid = defineComponent({
  name: "DashboardGrid",
  props: DashboardGridSchema,
  description:
    "An auto-fitting grid: children take equal-width columns, and the number of columns follows the width available rather than a count you choose. This is the layout for a dashboard — cards, charts and metric tiles that should sit side by side on a wide surface and stack on a narrow one. " +
    "children is the array of blocks; minColumnWidth is the narrowest a column may get before the grid drops one, as a CSS length such as \"16rem\" or \"240px\" (default 16rem — raise it for cards carrying a chart, lower it for bare figures). " +
    "Never state a column count and never nest a grid inside a grid; give the children more room by raising minColumnWidth instead.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    if (children.length === 0) return null;
    const min = cssLength(props.minColumnWidth, "16rem");
    return (
      <div
        className="vgb-grid"
        data-slot="vgb-dashboard-grid"
        style={{
          // `min(100%, …)` is the whole reason this survives a narrow column.
          // A bare `minmax(16rem, 1fr)` floor does not shrink its container, it
          // overflows it — the grid paints past the column and the page scrolls
          // sideways. Nothing errors; the layout is just wrong.
          gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, ${min}), 1fr))`,
        }}
      >
        {renderNode(children)}
      </div>
    );
  },
});

/* ── Furniture ────────────────────────────────────────────────────── */

export const Divider = defineComponent({
  name: "Divider",
  props: DividerSchema,
  description:
    "A horizontal rule between two movements of an answer, with an optional label centred in it — Divider(\"Assumptions\") reads as a section break, Divider() as a quiet one. " +
    "label should be one or two words; a longer heading belongs in a PageHeader. " +
    "Use it to separate sections that are already whole; do not put one between every pair of blocks, that is what a container's gap is for.",
  component: ({ props }) => {
    const label = readText(props.label).trim();
    if (!label) return <hr className="vgb-divider" data-slot="vgb-divider" />;
    return (
      <div className="vgb-divider vgb-divider-labelled" data-slot="vgb-divider" role="separator">
        <span className="vgb-divider-rule" aria-hidden="true" />
        <span className="vgb-divider-label">{label}</span>
        <span className="vgb-divider-rule" aria-hidden="true" />
      </div>
    );
  },
});

export const Spacer = defineComponent({
  name: "Spacer",
  props: SpacerSchema,
  description:
    "Blank vertical space, size small | medium | large (default medium). " +
    "Prefer a container's own gap: Page, Inline, Cluster and DashboardGrid already space their children evenly, and a Spacer between every pair fights that rhythm rather than setting it. " +
    "Reach for this only when one break in a sequence genuinely differs from the rest — the pause before a conclusion, the room above a footnote.",
  component: ({ props }) => (
    <div
      className="vgb-spacer"
      data-slot="vgb-spacer"
      aria-hidden="true"
      style={{ height: spacerSpace(props.size) }}
    />
  ),
});

export const AspectRatio = defineComponent({
  name: "AspectRatio",
  props: AspectRatioSchema,
  description:
    "Holds a fixed width-to-height ratio for whatever it wraps, so an image, a chart or an embed reserves its space before it loads instead of shoving the rest of the answer down when it arrives. " +
    "children is the single block to hold; ratio is written \"16/9\" (the default), \"4/3\", \"1/1\". " +
    "Use it for media whose size is not known yet — a block that already knows its own height does not need one.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    if (children.length === 0) return null;
    return (
      <div
        className="vgb-aspect"
        data-slot="vgb-aspect-ratio"
        style={{ aspectRatio: cssRatio(props.ratio) }}
      >
        {renderNode(children)}
      </div>
    );
  },
});

export const ScrollArea = defineComponent({
  name: "ScrollArea",
  props: ScrollAreaSchema,
  description:
    "Puts a scrollbar around its own children instead of letting them stretch the answer: a long list gets a height cap, a wide table gets sideways scrolling inside its box. The page itself must never scroll sideways, so anything wider than the column goes in one of these. " +
    "children is the content; maxHeight is a CSS length such as \"20rem\" (default 20rem, and it applies only when the content scrolls vertically); axis is vertical (default), horizontal for a wide table or chart, or both. " +
    "Do not wrap a Page in one — a Page is a frame and is meant to grow.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    if (children.length === 0) return null;
    const axis = props.axis === "horizontal" || props.axis === "both" ? props.axis : "vertical";
    const scrollsVertically = axis !== "horizontal";
    return (
      <div
        className="vgb-scroll"
        data-slot="vgb-scroll-area"
        data-axis={axis}
        style={scrollsVertically ? { maxHeight: cssLength(props.maxHeight, "20rem") } : undefined}
      >
        {renderNode(children)}
      </div>
    );
  },
});

/*
 * The one block here the reader can operate — and the reason it is allowed
 * where an interactive block would not be: the interaction is the browser's,
 * not ours. `<details>` needs no state, no handler and no wiring, so it cannot
 * promise a behaviour these blocks are unable to honour. That is the line: a
 * disclosure whose open/closed state the user agent owns is presentation with a
 * fold in it; anything that needs a click handler behind it is not a block.
 */
export const Collapsible = defineComponent({
  name: "Collapsible",
  props: CollapsibleSchema,
  description:
    "A titled section the reader can fold away: methodology, raw figures behind a summary, a long quotation, anything that supports the answer without being the answer. Arguments are positional with the content first — Collapsible([table], \"Full holdings\") — and defaultOpen (true) starts it expanded. " +
    "title is what the reader sees when it is closed, so make it say what is inside (\"Methodology\", not \"More\"). " +
    "Keep the answer's actual conclusion outside it; a reader who never opens it must still have read the answer.",
  component: ({ props, renderNode }) => {
    const children = toArray(props.children);
    const title = readText(props.title).trim();
    if (children.length === 0 && !title) return null;
    // A summary with no text is a focusable control with no accessible name, so
    // an untitled Collapsible degrades to its contents rather than hiding them
    // behind a nameless toggle.
    if (!title) return <div className="vgb-collapsible-body">{renderNode(children)}</div>;
    return (
      // Native `<details>`: the browser owns the open/closed state, which keeps
      // this block stateless, keyboard-operable and printable. Anything built
      // out of a click handler would be none of the three, and these blocks
      // render model output — nothing here is wired to a real interaction.
      <details
        className="vgb-collapsible"
        data-slot="vgb-collapsible"
        {...(props.defaultOpen === true ? { open: true } : {})}
      >
        <summary className="vgb-collapsible-summary">
          <span className="vgb-collapsible-title">{title}</span>
        </summary>
        {children.length > 0 ? (
          <div className="vgb-collapsible-body">{renderNode(children)}</div>
        ) : null}
      </details>
    );
  },
});
