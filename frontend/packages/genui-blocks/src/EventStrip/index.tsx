"use client";

import { defineComponent } from "@openuidev/react-lang";

import { asPct } from "../lib/chart";
import { readItems, readTone } from "../lib/collections";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneText } from "../lib/tone";
import { EventStripSchema } from "./schema";

export { EventStripEventSchema, EventStripSchema } from "./schema";

/*
 * ── Results, never controls ───────────────────────────────────────
 *
 * The interactive version of this is a scrubber: a handle you drag, a range you
 * brush, a zoom. There is no handle here, nothing to grab at either end of the
 * band, and no marker that can be picked. The band is a drawn statement about
 * where events sit inside a range someone else chose.
 *
 * ── And nothing is dropped ────────────────────────────────────────
 *
 * Two things go wrong with real model output, and both are stated rather than
 * hidden. An event outside the range is drawn at the edge it exceeded — a mark
 * pinned to the end reads as "at or beyond here", which is true — and counted
 * in the note. An `at` that cannot be read against the range keeps its place in
 * the list with no mark at all, and is counted too. Silently dropping either
 * would leave a strip that looks complete and is not.
 */

/**
 * A point on the axis, from a date or a number.
 *
 * The ISO guard matters: `Date.parse` accepts far more than it should, and a
 * bare `"2026"` parsed as a date and compared against a range expressed in
 * quarters would land somewhere meaningless. So a value only becomes a
 * timestamp when it really looks like a date, and otherwise it is a plain
 * number — which is what a range of revenue, of basis points, or of trading
 * days needs anyway.
 */
const ISO_DATE = /^\d{4}-\d{1,2}(-\d{1,2})?([T ]\d{1,2}:\d{2}(:\d{2})?)?/;

function readPoint(text: string): number | undefined {
  const value = text.trim();
  if (!value) return undefined;
  if (ISO_DATE.test(value)) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return readLooseNumber(value);
}

interface StripEvent {
  at: string;
  label: string;
  /** Position along the band as a percentage, or undefined when unplaceable. */
  offset: number | undefined;
  outside: boolean;
  tone: Tone | undefined;
}

export const EventStrip = defineComponent({
  name: "EventStrip",
  props: EventStripSchema,
  description:
    "Events placed along a band that spans a range you state: announcements across a quarter, incidents across a year, milestones between two figures. " +
    "start and end are the ends of the range and are what every position is measured against — never leave them to be guessed from the events, since the same three events read very differently across a week and across five years. " +
    "events is {at, label, tone?} where at is a date (\"2026-03-14\") or a plain number on the same scale as start and end. unit names that scale when it is not a date (\"trading days\", \"USD m\"). " +
    "An event outside the range is drawn at the edge and said so in a note, never dropped. The band is a picture: there is no handle to drag, no range to brush, and no marker that can be selected. Use Timeline when the entries are a dated sequence to read one by one rather than a distribution to see at once.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const events = readItems(raw.events ?? raw.items ?? raw.marks, "label").map((record) => ({
      at: readTextFromKeys(record, ["at", "date", "time", "value", "x"]),
      label: readTextFromKeys(record, ["label", "title", "name", "text"]),
      tone: readTone(record.tone ?? record.kind ?? record.status),
    }));
    const rows = events.filter((event) => event.at || event.label);
    // Nothing to show means nothing rendered: an empty band reads as data that
    // failed to load rather than as a quiet range.
    if (!rows.length) return null;

    const startText = readTextFromKeys(raw, ["start", "from", "begin"]);
    const endText = readTextFromKeys(raw, ["end", "to", "until"]);
    const start = readPoint(startText);
    const end = readPoint(endText);
    // A range that cannot be read, or one with no width, positions nothing. The
    // events still render as a list — losing them over an unreadable axis would
    // be the worse failure.
    const span = start !== undefined && end !== undefined && end > start ? { start, end } : undefined;

    let clamped = 0;
    let unplaced = 0;
    const placed: StripEvent[] = rows.map((event) => {
      if (!span) return { ...event, offset: undefined, outside: false };
      const point = readPoint(event.at);
      if (point === undefined) {
        unplaced += 1;
        return { ...event, offset: undefined, outside: false };
      }
      const ratio = ((point - span.start) / (span.end - span.start)) * 100;
      const outside = ratio < 0 || ratio > 100;
      if (outside) clamped += 1;
      return {
        ...event,
        offset: Math.max(0, Math.min(100, ratio)),
        outside,
      };
    });

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const note = [
      span ? "" : "The range could not be read, so the events below are listed in the order given.",
      clamped
        ? `${clamped} ${clamped === 1 ? "event falls" : "events fall"} outside ${startText} – ${endText} and ${clamped === 1 ? "is" : "are"} drawn at the edge.`
        : "",
      unplaced
        ? `${unplaced} ${unplaced === 1 ? "event has" : "events have"} no position on this scale and ${unplaced === 1 ? "is" : "are"} listed without a mark.`
        : "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <section
        className="vgb-collection vgb-strip"
        data-slot="vgb-event-strip"
        data-a2ui-component="event-strip"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-strip-range">
          <span className="vgb-strip-bound">{startText}</span>
          {unit ? <span className="vgb-strip-unit">{unit}</span> : null}
          <span className="vgb-strip-bound">{endText}</span>
        </div>
        {span ? (
          <div className="vgb-strip-track">
            {placed.map((event, index) =>
              event.offset === undefined ? null : (
                <span
                  className="vgb-strip-mark"
                  data-outside={event.outside ? "true" : undefined}
                  key={`${event.label}-${index}`}
                  style={{
                    backgroundColor: toneText(event.tone),
                    insetInlineStart: asPct(event.offset),
                  }}
                >
                  {/* The number is the only thing tying a mark to its line
                      below. Colour alone would not survive print, a monochrome
                      theme, or a reader who cannot separate the tones. */}
                  <span className="vgb-strip-mark-index">{index + 1}</span>
                </span>
              ),
            )}
          </div>
        ) : null}
        {/* An ordered list, not a legend: the band shows the distribution, and
            this is where the events are actually readable at any width. */}
        <ol className="vgb-strip-list">
          {placed.map((event, index) => (
            <li className="vgb-strip-row" data-slot="vgb-event-strip-item" key={`${event.label}-${index}`}>
              <span className="vgb-strip-index" style={{ color: toneText(event.tone) }}>
                {index + 1}
              </span>
              {event.at ? <span className="vgb-strip-at">{event.at}</span> : null}
              <span className="vgb-strip-label">{event.label}</span>
            </li>
          ))}
        </ol>
        {note ? (
          <p className="vgb-collection-note" data-slot="vgb-event-strip-note">
            {note}
          </p>
        ) : null}
      </section>
    );
  },
});
