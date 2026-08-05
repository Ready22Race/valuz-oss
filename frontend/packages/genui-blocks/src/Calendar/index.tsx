"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems, readTone } from "../lib/collections";
import { readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneSurface, toneText } from "../lib/tone";
import { CalendarSchema } from "./schema";

export { CalendarEventSchema, CalendarSchema, WeekStartSchema } from "./schema";

/*
 * ── Results, never controls ───────────────────────────────────────
 *
 * A calendar is the most control-shaped thing in this family: month arrows, a
 * clickable day, a "today" button, a popover per event. None of it exists here,
 * and none of it may be implied.
 *
 *  - One month is drawn — the one named in `month`. There is no arrow to a
 *    neighbouring month, because there is no data for one and nothing to fetch
 *    it with.
 *  - Days outside the month are blank cells, not muted neighbours a reader
 *    would try to press.
 *  - Nothing is highlighted as today unless `today` says which day that is. The
 *    block cannot know the date; inventing one from the machine clock would
 *    mark a day the answer never mentioned.
 *  - An event is a printed chip. No hover card, no expansion, no link.
 *
 * ── And nothing is dropped ────────────────────────────────────────
 *
 * An event dated outside the month cannot be placed on a one-month grid, so it
 * is listed underneath with its date rather than silently discarded — the same
 * rule the rest of the family follows.
 */

/** Chips a day cell shows before it starts counting instead. */
const MAX_CHIPS = 3;

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

interface CalendarMonth {
  days: number;
  /** `getUTCDay()` of the 1st: 0 is Sunday. */
  firstWeekday: number;
  month: number;
  year: number;
}

/** The month named by `"2026-08"`, or undefined when it cannot be read. */
function readMonth(text: string): CalendarMonth | undefined {
  const match = /^(\d{4})-(\d{1,2})$/.exec(text.trim());
  if (!match) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) return undefined;
  return {
    // Day 0 of the next month is the last day of this one.
    days: new Date(Date.UTC(year, month, 0)).getUTCDate(),
    firstWeekday: new Date(Date.UTC(year, month - 1, 1)).getUTCDay(),
    month,
    year,
  };
}

/**
 * The day of the month an event falls on, or undefined when it falls outside.
 *
 * Four spellings arrive in practice — `2026-08-14`, `08-14`, `8/14`, and a bare
 * `14`. A fully qualified date belonging to another month returns undefined on
 * purpose: that is not a day of this grid, and forcing it onto one would move
 * the event.
 */
function readDay(text: string, month: CalendarMonth): number | undefined {
  const value = text.trim();
  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(value);
  if (iso) {
    if (Number(iso[1]) !== month.year || Number(iso[2]) !== month.month) return undefined;
    return inMonth(Number(iso[3]), month);
  }
  const partial = /^(\d{1,2})[-/](\d{1,2})$/.exec(value);
  if (partial) {
    if (Number(partial[1]) !== month.month) return undefined;
    return inMonth(Number(partial[2]), month);
  }
  const bare = /^(\d{1,2})$/.exec(value);
  if (bare) return inMonth(Number(bare[1]), month);
  return undefined;
}

function inMonth(day: number, month: CalendarMonth): number | undefined {
  return day >= 1 && day <= month.days ? day : undefined;
}

interface CalendarEvent {
  date: string;
  label: string;
  tone: Tone | undefined;
}

export const Calendar = defineComponent({
  name: "Calendar",
  props: CalendarSchema,
  description:
    "One month laid out as a grid, with dated events marked on their days: an earnings calendar, a release schedule, the deadlines in a plan. " +
    "month is the month itself as \"2026-08\" and is the whole scope of the block — there is no way to reach another month, so build a second Calendar instead. events is {date, label, tone?} where date is \"2026-08-14\" (a bare day number also works) and tone marks the kind of day, not its importance. " +
    "weekStart is \"mon\" or \"sun\" and defaults to Monday. today takes a date inside the month and is the only thing that marks a day as current — omit it and no day is highlighted, because the block cannot know what day it is. " +
    "Nothing here can be navigated, opened or selected: days outside the month are blank, and an event dated outside it is listed under the grid rather than moved onto it.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const events: CalendarEvent[] = readItems(raw.events ?? raw.items ?? raw.dates, "label")
      .map((record) => ({
        date: readTextFromKeys(record, ["date", "day", "at", "on"]),
        label: readTextFromKeys(record, ["label", "title", "name", "text"]),
        tone: readTone(record.tone ?? record.kind ?? record.status),
      }))
      .filter((event) => event.date || event.label);
    // Nothing to show means nothing rendered: an empty grid is a month-shaped
    // frame that reads as data that failed to load.
    if (!events.length) return null;

    const monthText = readTextFromKeys(raw, ["month", "period"]);
    const month = readMonth(monthText);
    // Without a readable month there is no grid to draw, and guessing one would
    // put every event on a day the answer never named.
    if (!month) return null;

    const weekStart =
      readTextFromKeys(raw, ["weekStart", "week_start"]).trim().toLowerCase() === "sun"
        ? "sun"
        : "mon";
    const offset =
      weekStart === "sun" ? month.firstWeekday : (month.firstWeekday + 6) % 7;
    const headings = Array.from(
      { length: 7 },
      (_, index) => DAY_NAMES[(index + (weekStart === "sun" ? 0 : 1)) % 7] ?? "",
    );
    const weeks = Math.ceil((offset + month.days) / 7);

    const byDay = new Map<number, CalendarEvent[]>();
    const outside: CalendarEvent[] = [];
    for (const event of events) {
      const day = readDay(event.date, month);
      if (day === undefined) {
        outside.push(event);
        continue;
      }
      const bucket = byDay.get(day);
      if (bucket) bucket.push(event);
      else byDay.set(day, [event]);
    }

    const todayText = readTextFromKeys(raw, ["today", "current"]);
    const today = todayText ? readDay(todayText, month) : undefined;
    // The heading prints `month` exactly as it arrived. Rendering it as
    // "August 2026" would pick a language and a format the answer did not.
    const title = readTextFromKeys(raw, ["title", "label"]) || monthText;

    return (
      <section
        className="vgb-collection vgb-calendar"
        data-slot="vgb-calendar"
        data-a2ui-component="calendar"
      >
        <div className="vgb-collection-title">{title}</div>
        {/* The grid keeps a legible minimum width and scrolls inside its own
            box rather than squeezing seven columns into a narrow chat column. */}
        <div className="vgb-scroll-x">
          <table className="vgb-calendar-grid">
            <thead>
              <tr>
                {headings.map((heading) => (
                  <th className="vgb-calendar-weekday" key={heading} scope="col">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: weeks }, (_, week) => (
                <tr key={week}>
                  {Array.from({ length: 7 }, (_, weekday) => {
                    const day = week * 7 + weekday - offset + 1;
                    if (day < 1 || day > month.days) {
                      // Blank, not a muted neighbouring date: a date drawn here
                      // belongs to a month this block is not showing.
                      return (
                        <td
                          className="vgb-calendar-cell"
                          data-outside="true"
                          key={weekday}
                        />
                      );
                    }
                    const dayEvents = byDay.get(day) ?? [];
                    const shown = dayEvents.slice(0, MAX_CHIPS);
                    const hidden = dayEvents.length - shown.length;
                    return (
                      <td
                        className="vgb-calendar-cell"
                        data-slot="vgb-calendar-day"
                        data-today={day === today ? "true" : undefined}
                        key={weekday}
                      >
                        <span className="vgb-calendar-date">{day}</span>
                        {shown.map((event, index) => (
                          <span
                            className="vgb-calendar-chip"
                            key={`${event.label}-${index}`}
                            style={{
                              backgroundColor: toneSurface(event.tone),
                              color: toneText(event.tone),
                            }}
                          >
                            {event.label}
                          </span>
                        ))}
                        {hidden > 0 ? (
                          <span className="vgb-calendar-more">{`+${hidden}`}</span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {outside.length ? (
          <p className="vgb-collection-note" data-slot="vgb-calendar-outside">
            {`Outside ${monthText}: ${outside
              .map((event) => `${event.date} ${event.label}`.trim())
              .join("; ")}`}
          </p>
        ) : null}
      </section>
    );
  },
});
