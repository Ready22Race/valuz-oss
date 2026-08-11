"use client";

import { defineComponent } from "@openuidev/react-lang";
import { Card } from "@openuidev/react-ui";

import { formatCount, readLooseNumber, readTextFromKeys } from "../lib/props";
import { MarketBreadthSchema } from "./schema";

export { MarketBreadthSchema } from "./schema";

function BreadthStat({ label, value }: { label: string; value: number }) {
  return (
    <span className="vgb-breadth-stat">
      <span className="vgb-breadth-stat-label">
        {label} {formatCount(value)}
      </span>
    </span>
  );
}

export const MarketBreadth = defineComponent({
  name: "MarketBreadth",
  props: MarketBreadthSchema,
  description:
    "How wide a market move was: advancers, decliners and unchanged as one proportional bar with the three counts under it. " +
    "up, down and flat are counts of instruments, not percentages — the bar computes the shares itself. total defaults to their sum and is only worth passing when the universe is larger; source names where the counts came from. " +
    "Show it next to a MarketIndexGrid whenever the answer claims a move was broad or narrow, because an index level alone cannot support that claim.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const up = readLooseNumber(raw.up ?? raw.rise ?? raw.gainers) ?? 0;
    const down = readLooseNumber(raw.down ?? raw.fall ?? raw.losers) ?? 0;
    const flat = readLooseNumber(raw.flat ?? raw.unchanged) ?? 0;
    const total = readLooseNumber(raw.total) ?? up + down + flat;
    const source = readTextFromKeys(raw, ["source"]);
    const title = readTextFromKeys(raw, ["title", "label"]);
    const upLabel = readTextFromKeys(raw, ["upLabel", "up_label"]) || "Up";
    const downLabel =
      readTextFromKeys(raw, ["downLabel", "down_label"]) || "Down";
    const flatLabel =
      readTextFromKeys(raw, ["flatLabel", "flat_label"]) || "Flat";
    const totalLabel =
      readTextFromKeys(raw, ["totalLabel", "total_label"]) || "Total";
    const upShare = total ? up / total : 0;
    const downShare = total ? down / total : 0;
    const flatShare = total ? flat / total : 0;

    return (
      <Card variant="card" width="full">
        <article
          className="vgb-breadth"
          data-slot="vgb-market-breadth"
          data-a2ui-component="market-breadth"
        >
          <div className="vgb-breadth-heading" data-a2ui-market-breadth-heading>
            {title ? <span className="vgb-breadth-title">{title}</span> : null}
            <span className="vgb-breadth-total">
              {totalLabel} {formatCount(total)}
            </span>
          </div>
          {/*
           * The three shares are the data, so they are the one thing that has
           * to be inline: a stylesheet cannot express a bar whose widths come
           * from the counts.
           */}
          <div className="vgb-breadth-track" data-a2ui-market-breadth-track>
            <span
              className="vgb-breadth-bar vgb-breadth-bar-up"
              data-a2ui-market-breadth-bar="up"
              style={{ flex: `${upShare} 1 0` }}
            />
            <span
              className="vgb-breadth-bar vgb-breadth-bar-down"
              data-a2ui-market-breadth-bar="down"
              style={{ flex: `${downShare} 1 0` }}
            />
            <span
              className="vgb-breadth-bar vgb-breadth-bar-flat"
              data-a2ui-market-breadth-bar="flat"
              style={{ flex: `${flatShare} 1 0` }}
            />
          </div>
          <div className="vgb-breadth-stats" data-a2ui-market-breadth-stats>
            <BreadthStat label={upLabel} value={up} />
            <BreadthStat label={downLabel} value={down} />
            <BreadthStat label={flatLabel} value={flat} />
          </div>
          {source ? (
            <div className="vgb-breadth-source" data-a2ui-market-breadth-source>
              {source}
            </div>
          ) : null}
        </article>
      </Card>
    );
  },
});
