"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems, readTone } from "../lib/collections";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneBorder, toneText } from "../lib/tone";
import { KanbanSchema } from "./schema";

export { KanbanColumnSchema, KanbanItemSchema, KanbanSchema } from "./schema";

/*
 * ── Results, never controls ───────────────────────────────────────
 *
 * Everything a board normally is — drag a card, drop it in the next column,
 * add one, open one — is absent, and absent on purpose. What is left is the
 * arrangement itself, which is the part that carries the answer: how much sits
 * where.
 *
 *  - No `draggable`, no drop zone, no ghost card, no placeholder slot. A column
 *    with nothing in it shows nothing; a dashed "drop here" rectangle is an
 *    invitation to an action that does not exist.
 *  - No card is focusable, none carries a button role, and none opens anything.
 *  - `limit` is reported, never enforced. A column over its limit says so in
 *    words beside the count; it does not grey out, refuse, or hide a card.
 *
 * An empty column still renders. A board whose "Done" column is empty is a fact
 * about the board, and deleting the column would change what the reader sees.
 */

interface BoardItem {
  meta: string;
  title: string;
  tone: Tone | undefined;
}

interface BoardColumn {
  items: BoardItem[];
  label: string;
  limit: number | undefined;
}

function readColumn(record: Record<string, unknown>): BoardColumn {
  const limit = readLooseNumber(record.limit ?? record.wip ?? record.cap);
  return {
    items: readItems(record.items ?? record.cards ?? record.tasks, "title").map((item) => ({
      meta: readTextFromKeys(item, ["meta", "detail", "note", "owner", "subtitle"]),
      title: readTextFromKeys(item, ["title", "label", "name", "text"]),
      tone: readTone(item.tone ?? item.status ?? item.kind),
    })),
    label: readTextFromKeys(record, ["label", "title", "name", "stage"]),
    limit: limit !== undefined && limit > 0 ? limit : undefined,
  };
}

export const Kanban = defineComponent({
  name: "Kanban",
  props: KanbanSchema,
  description:
    "A board seen as a picture: grouped lists side by side, so the shape of the work in each stage reads at a glance. " +
    "columns is {label, items[], limit?} where each item is {title, meta?, tone?} — title is the piece of work, meta a one-line qualifier such as the owner or the due date, tone the kind of card. " +
    "limit is a work-in-progress limit the column is measured against: the block prints the count against it and says when it is exceeded, and enforces nothing. " +
    "Every column keeps the order it was given and an empty column still renders, because an empty stage is a fact about the board. Nothing can be dragged, dropped, opened or added — there are no drop zones and no ghost cards — so never present this as a board the reader can work.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const columns = readItems(raw.columns ?? raw.lanes ?? raw.stages, "label")
      .map(readColumn)
      .filter((column) => column.label || column.items.length);
    // Nothing to show means nothing rendered: an empty board reads as data that
    // failed to load.
    if (!columns.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-kanban"
        data-slot="vgb-kanban"
        data-a2ui-component="kanban"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        {/* The wrapping row is its own query container: a board inside a
            half-width column has to ask about its own width, not the
            document's, or it keeps a floor it cannot honour and overflows. */}
        <div className="vgb-kanban-columns">
          {columns.map((column, index) => {
            const count = column.items.length;
            const over = column.limit !== undefined && count > column.limit;
            return (
              <section
                className="vgb-kanban-column"
                data-slot="vgb-kanban-column"
                key={`${column.label}-${index}`}
              >
                <div className="vgb-kanban-head">
                  <span className="vgb-kanban-label">{column.label}</span>
                  <span className="vgb-kanban-count" data-over={over ? "true" : undefined}>
                    {column.limit === undefined ? count : `${count} / ${column.limit}`}
                  </span>
                </div>
                {over ? (
                  <p className="vgb-kanban-over" data-slot="vgb-kanban-over">
                    {`${count - (column.limit ?? 0)} over the limit of ${column.limit}`}
                  </p>
                ) : null}
                <div className="vgb-kanban-cards" role="list">
                  {column.items.map((item, itemIndex) => (
                    <div
                      className="vgb-kanban-card"
                      data-slot="vgb-kanban-card"
                      key={`${item.title}-${itemIndex}`}
                      role="listitem"
                      style={item.tone ? { borderColor: toneBorder(item.tone) } : undefined}
                    >
                      <span className="vgb-kanban-card-title">{item.title}</span>
                      {item.meta ? (
                        <span
                          className="vgb-kanban-card-meta"
                          style={item.tone ? { color: toneText(item.tone) } : undefined}
                        >
                          {item.meta}
                        </span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </section>
    );
  },
});
