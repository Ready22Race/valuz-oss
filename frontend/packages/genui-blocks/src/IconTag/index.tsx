"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";
import { IconTagSchema, IconTextSchema } from "./schema";

export { IconSizeSchema, IconTagSchema, IconTextSchema } from "./schema";

/**
 * Icon names are lucide's kebab-case ids. The description below lists a handful
 * rather than all ~1900: the list is prompt text, and its job is to teach the
 * *shape* of a name so the model can reach for one it was never shown. An
 * unknown name renders nothing rather than breaking the document.
 */
const ICON_NAMING =
  'icon is a lucide icon name in kebab-case — "trending-up", "trending-down", "dollar-sign", "chart-line", "users", "alert-triangle", "circle-check", "info", "star", "activity", "wallet", "building-2". Any lucide name works; use the plain noun for the thing, not a brand or a filename.';

export const IconTag = defineComponent({
  name: "IconTag",
  props: IconTagSchema,
  description:
    "An icon in a tinted rounded square. Use it to mark a row or card with its category or status — a section about risk, a metric that is trending, an item that needs attention. " +
    `${ICON_NAMING} ` +
    "tone colours the square (neutral | brand | success | warning | danger | info) and size picks its footprint (xs | s | m | l | xl, default m). " +
    "Pair it with text through IconText rather than placing it beside a bare string.",
  component: ({ props }) => (
    <span
      className={`vgb-icon-tag vgb-icon-tag-${props.size ?? "m"} vgb-icon-tag-${props.tone ?? "neutral"}`}
      data-slot="vgb-icon-tag"
      data-a2ui-component="icon-tag"
    >
      <BlockIcon name={props.icon} size="60%" />
    </span>
  ),
});

export const IconText = defineComponent({
  name: "IconText",
  props: IconTextSchema,
  description:
    "An icon beside a line of text, with an optional second line under it. The workhorse for feature lists, capability rundowns, and any short labelled point that reads better with a mark against it. " +
    `${ICON_NAMING} ` +
    'layout "horizontal" (default) puts the icon left of the text; "vertical" stacks it above, which suits a row of equal-weight items.',
  component: ({ props }) => (
    <span
      className={`vgb-icon-text vgb-icon-text-${props.layout ?? "horizontal"}`}
      data-slot="vgb-icon-text"
      data-a2ui-component="icon-text"
    >
      <span
        className={`vgb-icon-tag vgb-icon-tag-s vgb-icon-tag-${props.tone ?? "neutral"}`}
      >
        <BlockIcon name={props.icon} size="60%" />
      </span>
      <span className="vgb-icon-text-body">
        <span className="vgb-icon-text-title">{props.text}</span>
        {props.description ? (
          <span className="vgb-icon-text-note">{props.description}</span>
        ) : null}
      </span>
    </span>
  ),
});
