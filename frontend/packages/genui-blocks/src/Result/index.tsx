"use client";

import { defineComponent } from "@openuidev/react-lang";

import { BlockIcon } from "../lib/icon";
import { readText } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneBorder, toneSurface, toneText } from "../lib/tone";
import type { ResultStatus } from "./schema";
import { ResultSchema } from "./schema";

export { ResultSchema, ResultStatusSchema } from "./schema";
export type { ResultStatus } from "./schema";

/** The one place the outcome vocabulary meets the shared colour roles. */
const RESULT_TONE: Record<ResultStatus, Tone> = {
  success: "success",
  warning: "warning",
  error: "danger",
  info: "info",
};

/**
 * The mark is derived from `status`, not authored: there is no `icon` prop, so
 * the model cannot pick one — and a four-value enum has exactly four right
 * answers. Colour alone would leave the status unreadable to anyone who cannot
 * separate the four hues.
 */
const RESULT_ICON: Record<ResultStatus, string> = {
  success: "circle-check",
  warning: "triangle-alert",
  error: "circle-x",
  info: "info",
};

function readStatus(value: unknown): ResultStatus {
  const key = readText(value).trim().toLowerCase();
  // `info` is the fallback rather than `error`: an unrecognised status is a
  // prompt miss, not a failure, and colouring one red invents bad news.
  return key in RESULT_TONE ? (key as ResultStatus) : "info";
}

export const Result = defineComponent({
  name: "Result",
  props: ResultSchema,
  description:
    "The outcome of something that already happened, as one panel: a status mark, a headline, and an optional sentence. " +
    "status is one of success | warning | error | info and drives both the colour and the mark; title states the outcome in plain language (\"Filing submitted\", \"Two rows could not be parsed\"); description is one optional sentence of consequence or next context. " +
    "Reach for it to close an answer that reports what an operation did. Use ErrorState instead when the failure has a technical line worth showing, and EmptyState when nothing went wrong and there is simply nothing to show. " +
    "It reports, it does not act: there is no button, no retry and no handler behind it, so never write text that asks the reader to press one.",
  component: ({ props }) => {
    // The renderer hands props through unvalidated, so `status` may be any
    // string (or absent) and `description` may be `null`.
    const raw = props as unknown as Record<string, unknown>;
    const status = readStatus(raw.status);
    const tone = RESULT_TONE[status];
    const title = readText(raw.title).trim();
    const description = readText(raw.description).trim();

    return (
      <div
        className="vgb-state vgb-state-panel"
        data-slot="vgb-result"
        data-status={status}
        style={{ backgroundColor: toneSurface(tone), borderColor: toneBorder(tone) }}
      >
        <div className="vgb-state-heading">
          <span className="vgb-state-icon-slot" style={{ color: toneText(tone) }}>
            <BlockIcon name={RESULT_ICON[status]} className="vgb-state-icon" />
          </span>
          {title ? (
            <p className="vgb-state-title" style={{ color: toneText(tone) }}>
              {title}
            </p>
          ) : null}
        </div>
        {description ? <p className="vgb-state-text">{description}</p> : null}
      </div>
    );
  },
});
