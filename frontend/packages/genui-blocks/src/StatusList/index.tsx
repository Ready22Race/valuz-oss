"use client";

import { defineComponent } from "@openuidev/react-lang";

import { clampPercent, formatPercent, readItems, readPercent } from "../lib/collections";
import { readRecord, readTextFromKeys, toArray } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneSurface, toneText } from "../lib/tone";
import type { Status } from "./schema";
import {
  ProgressListSchema,
  StatusItemSchema,
  StatusListSchema,
  StatusSchema,
} from "./schema";

export {
  ProgressItemSchema,
  ProgressListSchema,
  StatusItemSchema,
  StatusListSchema,
  StatusSchema,
} from "./schema";

/**
 * Run state → shared tone.
 *
 * The mapping lives here so a status never names a colour: `lib/tone` owns
 * which token a tone resolves to, and a host that restyles danger restyles
 * every error in the package with it. `running` is deliberately `info` and not
 * an animation — see the note on the bar in ProgressList.
 */
const STATUS_TONE: Record<Status, Tone> = {
  pending: "neutral",
  running: "info",
  success: "success",
  error: "danger",
  blocked: "warning",
};

const STATUSES = new Set<string>(StatusSchema.options);

interface StatusRow {
  detail: string;
  label: string;
  /** The word as it arrived, so an unrecognised state is shown, not swallowed. */
  status: string;
  tone: Tone;
}

function readStatusRow(value: unknown): StatusRow {
  const record = readRecord(value);
  const status = readTextFromKeys(record, ["status", "state", "result"]).trim();
  const key = status.toLowerCase();
  return {
    detail: readTextFromKeys(record, ["detail", "description", "note", "message"]),
    label: readTextFromKeys(record, ["label", "title", "name", "step", "task"]),
    status,
    tone: STATUSES.has(key) ? STATUS_TONE[key as Status] : "neutral",
  };
}

function StatusEntry({ row }: { row: StatusRow }) {
  return (
    <div
      className="vgb-status-row"
      data-slot="vgb-status-item"
      data-status={row.status.toLowerCase() || undefined}
      role="listitem"
    >
      {/* The dot alone never carries the state — the word beside it does, so the
          block still reads in monochrome and to a screen reader. */}
      <span className="vgb-status-dot" style={{ backgroundColor: toneText(row.tone) }} />
      <span className="vgb-status-body">
        <span className="vgb-status-label">{row.label}</span>
        {row.detail ? <span className="vgb-status-detail">{row.detail}</span> : null}
      </span>
      {row.status ? (
        <span
          className="vgb-status-tag"
          style={{ backgroundColor: toneSurface(row.tone), color: toneText(row.tone) }}
        >
          {row.status}
        </span>
      ) : null}
    </div>
  );
}

export const StatusList = defineComponent({
  name: "StatusList",
  props: StatusListSchema,
  description:
    "Labelled things and the state each is in: preflight checks, pipeline stages, a checklist of requirements. " +
    "items is {label, status, detail?} where status is exactly one of pending | running | success | error | blocked — the word is printed beside the dot, so the state reads without relying on colour. " +
    "detail is the one-line reason or note. Use ProgressList instead when each entry has a completion figure rather than a state. " +
    "This is a static picture of the resting state: nothing polls, nothing animates, and no row can be selected or expanded — do not describe it as live.",
  component: ({ props, renderNode }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.statuses ?? raw.checks, "label")
      .map(readStatusRow)
      .filter((row) => row.label || row.status || row.detail);
    const children = toArray(raw.children);
    // Nothing to show means nothing rendered: an empty frame reads as data that
    // failed to load.
    if (!rows.length && !children.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-status-list"
        data-slot="vgb-status-list"
        data-a2ui-component="status-list"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-status-rows" role="list">
          {rows.length
            ? rows.map((row, index) => (
                <StatusEntry key={`${row.label}-${index}`} row={row} />
              ))
            : renderNode(children)}
        </div>
      </section>
    );
  },
});

export const StatusItem = defineComponent({
  name: "StatusItem",
  props: StatusItemSchema,
  description:
    "One labelled state inside a StatusList: a dot, the label with an optional note under it, and the state word itself. " +
    "status is one of pending | running | success | error | blocked; anything else is shown as written, in neutral. " +
    "Only use this inside a StatusList; a list built from items renders the same rows without it.",
  component: ({ props }) => <StatusEntry row={readStatusRow(props)} />,
});

interface ProgressRow {
  detail: string;
  label: string;
  percent: number | undefined;
}

function readProgressRow(value: Record<string, unknown>): ProgressRow {
  return {
    detail: readTextFromKeys(value, ["detail", "description", "note", "status"]),
    label: readTextFromKeys(value, ["label", "title", "name", "task"]),
    percent: readPercent(value.percent ?? value.progress ?? value.value ?? value.pct),
  };
}

function ProgressEntry({ row }: { row: ProgressRow }) {
  const percent = row.percent;
  return (
    <div className="vgb-progress-row" data-slot="vgb-progress-item" role="listitem">
      <span className="vgb-progress-heading">
        <span className="vgb-progress-label">{row.label}</span>
        {percent === undefined ? null : (
          <span className="vgb-progress-percent">{formatPercent(percent)}%</span>
        )}
      </span>
      {percent === undefined ? null : (
        /*
         * The fill width is the datum, so it is the one thing that has to be
         * inline — a stylesheet cannot express a bar whose width comes from the
         * data. The bar carries no transition and no animation on purpose:
         * this renders a completed answer, and a bar that keeps moving promises
         * a progress update that will never arrive. `aria-hidden` because the
         * figure beside it already says the same thing.
         */
        <span className="vgb-progress-track" aria-hidden="true">
          <span
            className="vgb-progress-fill"
            style={{ width: `${clampPercent(percent)}%` }}
          />
        </span>
      )}
      {row.detail ? <span className="vgb-progress-detail">{row.detail}</span> : null}
    </div>
  );
}

export const ProgressList = defineComponent({
  name: "ProgressList",
  props: ProgressListSchema,
  description:
    "Tasks with how far each has got, as a labelled bar per row: milestone completion, coverage by area, budget consumed. " +
    "items is {label, percent, detail?} where percent is a number on a 0–100 scale (62, not 0.62) — the figure is printed as it arrives and the bar is drawn from it, clamped to the track. " +
    "detail is a short qualifier such as \"12 of 20 done\". Use StatusList instead when an entry has a state rather than a figure. " +
    "The bars are a still picture of a finished answer: they do not animate and nothing updates.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.tasks ?? raw.progress, "label")
      .map(readProgressRow)
      .filter((row) => row.label || row.percent !== undefined);
    if (!rows.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-progress-list"
        data-slot="vgb-progress-list"
        data-a2ui-component="progress-list"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-progress-rows" role="list">
          {rows.map((row, index) => (
            <ProgressEntry key={`${row.label}-${index}`} row={row} />
          ))}
        </div>
      </section>
    );
  },
});
