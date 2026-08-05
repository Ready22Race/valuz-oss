"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems, readTone } from "../lib/collections";
import { BlockIcon, isKnownIcon } from "../lib/icon";
import { readRecord, readTextFromKeys, toArray } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneBorder, toneSurface, toneText } from "../lib/tone";
import {
  ActivityFeedSchema,
  ActivityItemSchema,
  TimelineItemSchema,
  TimelineSchema,
} from "./schema";

export {
  ActivityFeedSchema,
  ActivityItemSchema,
  TimelineItemSchema,
  TimelineSchema,
} from "./schema";

/**
 * One dated event, after every alias has been resolved.
 *
 * Reading happens once, up front, so the markup never has to ask which key a
 * field came from — and so an entry carrying nothing can be dropped before it
 * renders as an empty line on the rail.
 */
interface TimelineRow {
  description: string;
  icon: string;
  time: string;
  title: string;
  tone: Tone | undefined;
}

function readTimelineRow(value: unknown): TimelineRow {
  const record = readRecord(value);
  return {
    description: readTextFromKeys(record, ["description", "detail", "body", "note"]),
    icon: readTextFromKeys(record, ["icon"]),
    time: readTextFromKeys(record, ["time", "date", "when", "timestamp"]),
    title: readTextFromKeys(record, ["title", "label", "name", "event", "text"]),
    tone: readTone(record.tone ?? record.status),
  };
}

function readTimelineRows(value: unknown): TimelineRow[] {
  return readItems(value, "title")
    .map(readTimelineRow)
    .filter((row) => row.title || row.description || row.time);
}

/**
 * The marker on the rail.
 *
 * A named icon replaces the dot; an unknown one falls back to it rather than
 * leaving a hollow ring, because the model invents icon names and a gap in the
 * rail reads as a missing event.
 */
function TimelineMarker({ icon, tone }: { icon: string; tone: Tone | undefined }) {
  return (
    <span
      className="vgb-timeline-marker"
      style={{
        backgroundColor: toneSurface(tone),
        borderColor: toneBorder(tone),
        color: toneText(tone),
      }}
    >
      {isKnownIcon(icon) ? (
        <BlockIcon name={icon} size="60%" />
      ) : (
        <span className="vgb-timeline-dot" />
      )}
    </span>
  );
}

function TimelineEntry({ row }: { row: TimelineRow }) {
  return (
    <div className="vgb-timeline-entry" data-slot="vgb-timeline-item" role="listitem">
      <TimelineMarker icon={row.icon} tone={row.tone} />
      <span className="vgb-timeline-body">
        <span className="vgb-timeline-heading">
          <span className="vgb-timeline-title">{row.title}</span>
          {row.time ? <span className="vgb-timeline-time">{row.time}</span> : null}
        </span>
        {row.description ? (
          <span className="vgb-timeline-description">{row.description}</span>
        ) : null}
      </span>
    </div>
  );
}

export const Timeline = defineComponent({
  name: "Timeline",
  props: TimelineSchema,
  description:
    "Dated events down a vertical rail, each with a marker: a release history, a case chronology, the steps a filing went through. " +
    "items carries the events in the order they should read — the block never sorts, so put them in the order you mean, oldest or newest first as the answer requires. " +
    "Each item is {time, title, description?, tone?, icon?}: time is the already-formatted stamp (\"09:30\", \"2025-03-14\", \"Q2\"), title names the event in a few words, description adds at most a sentence, tone colours the marker (neutral | brand | success | warning | danger | info) and icon is any lucide-react icon name, never an emoji. " +
    "Reach for ActivityFeed instead when the entries are who-did-what rather than milestones, and Steps when the reader is meant to follow a procedure. Nothing here is clickable.",
  component: ({ props, renderNode }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readTimelineRows(raw.items ?? raw.events ?? raw.entries);
    const children = toArray(raw.children);
    // An empty frame is worse than no block: it reads as data that failed to
    // load. Nothing to show means nothing rendered, title included.
    if (!rows.length && !children.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-timeline"
        data-slot="vgb-timeline"
        data-a2ui-component="timeline"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-timeline-rail" role="list">
          {rows.length
            ? rows.map((row, index) => (
                <TimelineEntry key={`${row.time}-${row.title}-${index}`} row={row} />
              ))
            : renderNode(children)}
        </div>
      </section>
    );
  },
});

export const TimelineItem = defineComponent({
  name: "TimelineItem",
  props: TimelineItemSchema,
  description:
    "One event on a Timeline: the time on the right of its title, an optional sentence under it, and a marker on the rail. " +
    "tone colours that marker (neutral | brand | success | warning | danger | info) and icon is any lucide-react icon name — an unknown name falls back to the plain dot, and an emoji is never accepted. " +
    "Only use this inside a Timeline; a timeline built from items renders the same entries without it.",
  component: ({ props }) => <TimelineEntry row={readTimelineRow(props)} />,
});

/** One line of the activity log. */
interface ActivityRow {
  action: string;
  actor: string;
  icon: string;
  target: string;
  time: string;
}

function readActivityRow(value: unknown): ActivityRow {
  const record = readRecord(value);
  return {
    action: readTextFromKeys(record, ["action", "verb", "event", "did"]),
    actor: readTextFromKeys(record, ["actor", "who", "user", "author", "name"]),
    icon: readTextFromKeys(record, ["icon"]),
    target: readTextFromKeys(record, ["target", "object", "subject", "item"]),
    time: readTextFromKeys(record, ["time", "when", "at", "timestamp", "date"]),
  };
}

function readActivityRows(value: unknown): ActivityRow[] {
  return readItems(value, "action")
    .map(readActivityRow)
    .filter((row) => row.actor || row.action || row.target);
}

function ActivityEntry({ row }: { row: ActivityRow }) {
  return (
    <div className="vgb-activity-row" data-slot="vgb-activity-item" role="listitem">
      <span className="vgb-activity-mark">
        {isKnownIcon(row.icon) ? (
          <BlockIcon name={row.icon} size="14px" />
        ) : (
          <span className="vgb-activity-dot" />
        )}
      </span>
      {/*
       * One flowing sentence rather than three columns: the parts are read
       * together, and a fixed column for the actor would leave a ragged gap the
       * moment one name is a single character and the next is a full title.
       */}
      <span className="vgb-activity-text">
        {row.actor ? <span className="vgb-activity-actor">{row.actor}</span> : null}
        {row.actor && row.action ? " " : null}
        {row.action ? <span className="vgb-activity-action">{row.action}</span> : null}
        {row.target ? " " : null}
        {row.target ? <span className="vgb-activity-target">{row.target}</span> : null}
      </span>
      {row.time ? <span className="vgb-activity-time">{row.time}</span> : null}
    </div>
  );
}

export const ActivityFeed = defineComponent({
  name: "ActivityFeed",
  props: ActivityFeedSchema,
  description:
    "Who did what, when — an audit trail, a review history, a log of edits. Denser than Timeline and drawn without a rail, so it holds a long list without turning into a diagram. " +
    "items is {actor, action, target?, time?, icon?}: actor is the person or system, action the past-tense verb phrase (\"approved\", \"上传了附件\"), target the thing it acted on, time the already-formatted stamp. " +
    "Always pass time — it is optional only so the positional order can stay actor, action, target, time. icon is any lucide-react icon name, never an emoji. " +
    "Use Timeline instead when the entries are milestones rather than actions. Nothing here is clickable.",
  component: ({ props, renderNode }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readActivityRows(raw.items ?? raw.activities ?? raw.entries);
    const children = toArray(raw.children);
    if (!rows.length && !children.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-activity"
        data-slot="vgb-activity-feed"
        data-a2ui-component="activity-feed"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-activity-rows" role="list">
          {rows.length
            ? rows.map((row, index) => (
                <ActivityEntry key={`${row.actor}-${row.action}-${index}`} row={row} />
              ))
            : renderNode(children)}
        </div>
      </section>
    );
  },
});

export const ActivityItem = defineComponent({
  name: "ActivityItem",
  props: ActivityItemSchema,
  description:
    "One line of an ActivityFeed: actor, the action they took, the thing it was taken on, and when. " +
    "icon is any lucide-react icon name and replaces the dot before the line; an unknown name falls back to the dot and an emoji is never accepted. " +
    "Only use this inside an ActivityFeed; a feed built from items renders the same lines without it.",
  component: ({ props }) => <ActivityEntry row={readActivityRow(props)} />,
});
