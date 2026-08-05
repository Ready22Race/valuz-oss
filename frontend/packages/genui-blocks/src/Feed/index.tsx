"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems } from "../lib/collections";
import { readTextFromKeys } from "../lib/props";
// Shared with the report family rather than re-derived: an image URL out of
// model output is untrusted input this package hands straight to the DOM, and
// there must be exactly one statement of which schemes may reach an `src`.
import { safeImageUrl } from "../Report/safe-url";
import { FeedSchema } from "./schema";

export { FeedItemSchema, FeedSchema } from "./schema";

interface FeedRow {
  body: string;
  imageUrl: string | undefined;
  source: string;
  time: string;
  title: string;
}

function readFeedRow(value: Record<string, unknown>): FeedRow {
  return {
    body: readTextFromKeys(value, ["body", "summary", "description", "text"]),
    imageUrl: safeImageUrl(
      readTextFromKeys(value, ["imageUrl", "image_url", "image", "thumbnail"]) ||
        undefined,
    ),
    source: readTextFromKeys(value, ["source", "publisher", "author", "from"]),
    time: readTextFromKeys(value, ["time", "date", "published", "when"]),
    title: readTextFromKeys(value, ["title", "headline", "label", "name"]),
  };
}

function FeedEntry({ row }: { row: FeedRow }) {
  const meta = [row.source, row.time].filter(Boolean);
  return (
    <article className="vgb-feed-entry" data-slot="vgb-feed-item" role="listitem">
      {/*
       * Text before picture in the DOM whatever the visual order: the title is
       * what the entry is, and a screen reader should reach it first. The image
       * is decorative — `alt=""` rather than a caption the model did not write.
       */}
      <span className="vgb-feed-body">
        <span className="vgb-feed-title">{row.title}</span>
        {row.body ? <span className="vgb-feed-text">{row.body}</span> : null}
        {meta.length ? (
          <span className="vgb-feed-meta">
            {meta.map((part, index) => (
              <span className="vgb-feed-meta-part" key={`${part}-${index}`}>
                {index ? <span aria-hidden="true">·</span> : null}
                {part}
              </span>
            ))}
          </span>
        ) : null}
      </span>
      {row.imageUrl ? (
        <img className="vgb-feed-media" src={row.imageUrl} alt="" loading="lazy" />
      ) : null}
    </article>
  );
}

export const Feed = defineComponent({
  name: "Feed",
  props: FeedSchema,
  description:
    "A stream of short entries, each a headline with an optional line of detail and a thumbnail: news digests, announcement round-ups, a list of filings. " +
    "items is {title, body?, time?, imageUrl?, source?} — title is the headline, body one or two sentences at most, source who published it, time the already-formatted stamp, and imageUrl a complete http(s) URL to a real picture (leave it out rather than inventing one; anything else is dropped). " +
    "Reach for ActivityFeed when the entries are actions someone took, and DataList when every entry is a name with a figure. Entries are not links — nothing here navigates.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.entries ?? raw.news, "title")
      .map(readFeedRow)
      .filter((row) => row.title || row.body);
    // Nothing to show means nothing rendered: an empty frame reads as data that
    // failed to load.
    if (!rows.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section className="vgb-collection vgb-feed" data-slot="vgb-feed" data-a2ui-component="feed">
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-feed-rows" role="list">
          {rows.map((row, index) => (
            <FeedEntry key={`${row.title}-${index}`} row={row} />
          ))}
        </div>
      </section>
    );
  },
});
