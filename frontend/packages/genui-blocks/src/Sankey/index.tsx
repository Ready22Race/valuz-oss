"use client";

import { defineComponent } from "@openuidev/react-lang";

import { asPct, formatValue, readItems, seriesTone } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import type { Tone } from "../lib/schema";
import { toneText } from "../lib/tone";
import { SankeySchema } from "./schema";

export { SankeyLinkSchema, SankeyNodeSchema, SankeySchema } from "./schema";

/**
 * Caps.
 *
 * A Sankey stops being readable long before it stops being drawable: past a
 * dozen nodes the ribbons cross more than they connect and no label has room.
 * The columns are capped too — this is a deliberately layered layout, not a
 * general graph layout, and a chain longer than four stages wants a Timeline.
 */
const MAX_NODES = 12;
const MAX_LINKS = 24;
const MAX_COLUMNS = 4;

/** Node bar width and vertical gap, in the SVG's 0–100 user units. */
const NODE_W = 2.2;
const GAP = 3;
/** A node with no flow is still a node; it gets a stub rather than vanishing. */
const MIN_NODE_H = 1.5;

/**
 * Float tolerance for the conservation check.
 *
 * `0.1 + 0.2` is `0.30000000000000004`, so an exact comparison would flag a
 * diagram that balances perfectly. Anything genuinely unbalanced is wrong by
 * orders of magnitude more than this.
 */
const EPSILON = 1e-6;

interface Node {
  id: string;
  label: string;
  depth: number;
  inflow: number;
  outflow: number;
  throughput: number;
  x: number;
  y: number;
  h: number;
  tone: Tone;
  /** Inflow and outflow disagree. Drawn as given, marked, never balanced. */
  mismatch: boolean;
}

interface Link {
  from: string;
  to: string;
  value: number;
}

interface Ribbon {
  key: string;
  d: string;
  tone: Tone;
}

interface Diagram {
  nodes: Node[];
  ribbons: Ribbon[];
  links: Link[];
  columns: number;
  unbalanced: Node[];
  droppedNodes: number;
  droppedLinks: number;
  invalidLinks: number;
  /** Flows that do not run left to right: a loop, or a chain past the column cap. */
  folded: number;
  total: number;
  /** There is at least one node nothing flows into, so `total` is what enters. */
  rooted: boolean;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * Column per node, by longest path from a source.
 *
 * Relaxation rather than a topological sort, because model output is not
 * guaranteed acyclic and a sort would have to either fail or drop an edge. The
 * cap on depth is what makes a cycle terminate: it stops raising a node once it
 * reaches the last column, so a loop settles instead of running away.
 */
function depthsOf(ids: string[], links: Link[]): Map<string, number> {
  const depth = new Map(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass += 1) {
    let changed = false;
    for (const link of links) {
      const next = (depth.get(link.from) ?? 0) + 1;
      if (next <= MAX_COLUMNS - 1 && next > (depth.get(link.to) ?? 0)) {
        depth.set(link.to, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  /*
   * Compact the depths onto consecutive columns.
   *
   * A cycle pushes both of its nodes up until they hit the cap — `a → b → a`
   * ends at depths 2 and 3 — which would leave two empty columns on the left
   * and squeeze the diagram into the right-hand half for no reason a reader
   * could see. Renumbering the *used* depths fixes that and every other gap.
   */
  const used = [...new Set(depth.values())].sort((a, b) => a - b);
  const rank = new Map(used.map((value, index) => [value, index]));
  for (const [id, value] of depth) depth.set(id, rank.get(value) ?? 0);
  return depth;
}

/**
 * The diagram, laid out and reconciled.
 *
 * The invariant this block exists for: **what arrives at a node should equal
 * what leaves it.** Where it does not, the node is drawn at whichever side is
 * larger — so the shortfall shows as a bar edge with no ribbon on it — and both
 * figures are printed. Silently scaling one side to match the other would make
 * a broken flow statement look like a sound one, which is the failure this
 * shape invites.
 */
function buildDiagram(raw: Record<string, unknown>): Diagram | null {
  const declared = readItems(raw.nodes ?? raw.stages).map((record) => ({
    id: readTextFromKeys(record, ["id", "key", "name", "label"]),
    label: readTextFromKeys(record, ["label", "name", "title", "id"]),
  }));

  const allLinks: Link[] = [];
  let invalidLinks = 0;
  for (const record of readItems(raw.links ?? raw.flows ?? raw.edges)) {
    const from = readTextFromKeys(record, ["from", "source", "start"]);
    const to = readTextFromKeys(record, ["to", "target", "end"]);
    const value = readLooseNumber(record.value ?? record.amount ?? record.flow ?? record.weight);
    // A flow has no direction of its own, so a negative one has no drawable
    // width and a self-loop has nowhere to go. Both are counted and said.
    if (!from || !to || from === to || value === undefined || !(value > 0)) {
      invalidLinks += 1;
      continue;
    }
    allLinks.push({ from, to, value });
  }
  // Nodes without flows are not a flow diagram, and neither is a node list on
  // its own. Nothing is drawn rather than an empty box holding its height.
  if (allLinks.length === 0) return null;

  const labels = new Map(declared.map((node) => [node.id, node.label || node.id]));
  // A model routinely supplies links and forgets the node list. Every id a link
  // names is a node whether or not it was declared.
  for (const link of allLinks) {
    if (!labels.has(link.from)) labels.set(link.from, link.from);
    if (!labels.has(link.to)) labels.set(link.to, link.to);
  }

  const throughputOf = (id: string, links: Link[]) => {
    const inflow = links.filter((l) => l.to === id).reduce((sum, l) => sum + l.value, 0);
    const outflow = links.filter((l) => l.from === id).reduce((sum, l) => sum + l.value, 0);
    return { inflow, outflow };
  };

  const ranked = [...labels.keys()].sort((a, b) => {
    const left = throughputOf(a, allLinks);
    const right = throughputOf(b, allLinks);
    return (
      Math.max(right.inflow, right.outflow) - Math.max(left.inflow, left.outflow)
    );
  });
  const keptIds = ranked.slice(0, MAX_NODES);
  const kept = new Set(keptIds);
  const droppedNodes = ranked.length - keptIds.length;

  const reachable = allLinks.filter((link) => kept.has(link.from) && kept.has(link.to));
  const links = [...reachable].sort((a, b) => b.value - a.value).slice(0, MAX_LINKS);
  const droppedLinks = reachable.length - links.length;
  if (links.length === 0) return null;

  const depth = depthsOf(keptIds, links);
  const columnCount = Math.max(...keptIds.map((id) => (depth.get(id) ?? 0) + 1));

  const nodes: Node[] = keptIds.map((id) => {
    const { inflow, outflow } = throughputOf(id, links);
    const nodeDepth = depth.get(id) ?? 0;
    return {
      id,
      label: labels.get(id) ?? id,
      depth: nodeDepth,
      inflow,
      outflow,
      // Whichever side is larger. Drawing the smaller one would hide the
      // discrepancy by construction.
      throughput: Math.max(inflow, outflow),
      x: 0,
      y: 0,
      h: 0,
      tone: seriesTone(nodeDepth),
      mismatch: inflow > 0 && outflow > 0 && Math.abs(inflow - outflow) > EPSILON,
    };
  });

  const columns: Node[][] = Array.from({ length: columnCount }, (_, index) =>
    nodes
      .filter((node) => node.depth === index)
      .sort((a, b) => b.throughput - a.throughput),
  );

  /*
   * One vertical scale for the whole diagram, set by the busiest column.
   *
   * Per-column scaling would make each column fill the height, which reads as
   * every stage carrying the same amount — the exact claim a Sankey is drawn to
   * refute. A lighter column simply ends higher up.
   */
  const columnTotals = columns.map((column) =>
    column.reduce((sum, node) => sum + node.throughput, 0),
  );
  const busiest = Math.max(...columnTotals, 0);
  const tallest = Math.max(...columns.map((column) => column.length), 1);
  const usable = Math.max(20, 100 - GAP * (tallest - 1));
  const scale = busiest > 0 ? usable / busiest : 0;
  const step = columnCount > 1 ? (100 - NODE_W) / (columnCount - 1) : 0;

  columns.forEach((column, index) => {
    const heights = column.map((node) => Math.max(MIN_NODE_H, node.throughput * scale));
    const stacked = heights.reduce((sum, h) => sum + h, 0) + GAP * (column.length - 1);
    let cursor = Math.max(0, (100 - stacked) / 2);
    column.forEach((node, position) => {
      node.x = round(index * step);
      node.y = round(cursor);
      node.h = round(heights[position] ?? MIN_NODE_H);
      cursor += (heights[position] ?? MIN_NODE_H) + GAP;
    });
  });

  const sources = nodes.filter((node) => node.inflow === 0);
  const rooted = sources.length > 0;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  // Ordered by where the ribbons leave and land, so bands cross as little as a
  // layout this simple can manage.
  const ordered = [...links].sort((a, b) => {
    const from = (byId.get(a.from)?.y ?? 0) - (byId.get(b.from)?.y ?? 0);
    return from !== 0 ? from : (byId.get(a.to)?.y ?? 0) - (byId.get(b.to)?.y ?? 0);
  });
  const outCursor = new Map<string, number>();
  const inCursor = new Map<string, number>();
  const ribbons: Ribbon[] = [];
  ordered.forEach((link, index) => {
    const source = byId.get(link.from);
    const target = byId.get(link.to);
    if (!source || !target) return;
    const thickness = Math.max(0.4, link.value * scale);
    const y0 = source.y + (outCursor.get(source.id) ?? 0);
    const y1 = target.y + (inCursor.get(target.id) ?? 0);
    outCursor.set(source.id, (outCursor.get(source.id) ?? 0) + thickness);
    inCursor.set(target.id, (inCursor.get(target.id) ?? 0) + thickness);
    const x0 = source.x + NODE_W;
    const x1 = target.x;
    const mid = round((x0 + x1) / 2);
    ribbons.push({
      key: `${link.from}-${link.to}-${index}`,
      d:
        `M ${round(x0)} ${round(y0)} C ${mid} ${round(y0)}, ${mid} ${round(y1)}, ` +
        `${round(x1)} ${round(y1)} L ${round(x1)} ${round(y1 + thickness)} ` +
        `C ${mid} ${round(y1 + thickness)}, ${mid} ${round(y0 + thickness)}, ` +
        `${round(x0)} ${round(y0 + thickness)} Z`,
      tone: source.tone,
    });
  });

  return {
    nodes,
    ribbons,
    links,
    columns: columnCount,
    unbalanced: nodes.filter((node) => node.mismatch),
    droppedNodes,
    droppedLinks,
    invalidLinks,
    // A layered layout only draws flows that advance a column. A loop, or a
    // chain longer than the cap, ends up drawn between two nodes in the same
    // column or running backwards — visible, but not what a reader expects, so
    // it is counted and said rather than quietly straightened out.
    folded: links.filter(
      (link) => (depth.get(link.to) ?? 0) <= (depth.get(link.from) ?? 0),
    ).length,
    // What *enters* the diagram, not the sum of every ribbon. Adding the
    // ribbons up double-counts everything that passes through a middle node —
    // a 100 that splits 60/40 and then splits again is not 200 of anything.
    // A diagram that is all cycle has no entry point, so it falls back to the
    // ribbon sum and the summary says which figure it is quoting.
    total: rooted
      ? sources.reduce((sum, node) => sum + node.outflow, 0)
      : links.reduce((sum, link) => sum + link.value, 0),
    rooted,
  };
}

export const Sankey = defineComponent({
  name: "Sankey",
  props: SankeySchema,
  description:
    "Flow between stages: ribbons whose thickness is the amount moving from one node to the next, laid out in two to four columns. " +
    "nodes is {id, label} and links is {from, to, value} where from and to are node ids and value is a positive amount in one unit, named by unit. " +
    "A node's inflow and outflow are checked against each other: where they disagree the diagram is drawn exactly as given and the node is flagged, never quietly balanced, so a flow statement that does not add up looks wrong instead of looking authoritative. " +
    "At most 12 nodes and 24 flows are drawn. Use it for budget allocation, traffic sources to outcomes, or headcount movement; use Funnel when every stage feeds exactly one next stage.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const diagram = buildDiagram(raw);
    if (!diagram) return null;

    const title = readTextFromKeys(raw, ["title", "label"]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);
    const largest = [...diagram.links].sort((a, b) => b.value - a.value)[0];
    const labelOf = (id: string) => diagram.nodes.find((node) => node.id === id)?.label ?? id;

    const summary =
      `Sankey diagram${title ? ` of ${title}` : ""}: ${diagram.nodes.length} nodes in ` +
      `${diagram.columns} columns, ${diagram.links.length} flows, ` +
      `${formatValue(diagram.total)}${unit ? ` ${unit}` : ""} ` +
      `${diagram.rooted ? "entering at the sources" : "across all flows"}. ` +
      (largest
        ? `Largest flow ${labelOf(largest.from)} to ${labelOf(largest.to)}, ` +
          `${formatValue(largest.value)}. `
        : "") +
      (diagram.unbalanced.length > 0
        ? `${diagram.unbalanced.length} node${diagram.unbalanced.length === 1 ? "" : "s"} ` +
          "do not balance: " +
          diagram.unbalanced
            .map(
              (node) =>
                `${node.label} takes in ${formatValue(node.inflow)} and sends out ` +
                `${formatValue(node.outflow)}`,
            )
            .join("; ") +
          "."
        : "Every node's inflow matches its outflow.");

    const notes = [
      diagram.unbalanced.length > 0
        ? `Flow does not balance at ${diagram.unbalanced
            .map(
              (node) =>
                `${node.label} (in ${formatValue(node.inflow)}, out ` +
                `${formatValue(node.outflow)})`,
            )
            .join(", ")}. Drawn exactly as given — the difference is not distributed.`
        : "",
      diagram.invalidLinks > 0
        ? `${diagram.invalidLinks} flow${diagram.invalidLinks === 1 ? "" : "s"} without a ` +
          "positive value, or pointing at nothing, were not drawn."
        : "",
      diagram.droppedNodes > 0 || diagram.droppedLinks > 0
        ? `Showing the largest ${diagram.nodes.length} nodes and ${diagram.links.length} ` +
          `flows; ${diagram.droppedNodes} nodes and ${diagram.droppedLinks} flows were not ` +
          "drawn, so the ribbons do not sum to the whole."
        : "",
      diagram.folded > 0
        ? `${diagram.folded} flow${diagram.folded === 1 ? " does" : "s do"} not run left ` +
          `to right: this is a layered layout of at most ${MAX_COLUMNS} columns, so a loop ` +
          "or a longer chain is folded back into it."
        : "",
    ].filter(Boolean);

    return (
      <ChartFrame
        footnote={
          notes.length > 0 ? (
            <span data-chart-mismatch={diagram.unbalanced.length > 0 ? "true" : undefined}>
              {notes.join(" ")}
            </span>
          ) : null
        }
        slot="sankey"
        summary={summary}
        title={title}
        unit={unit}
      >
        <div
          className="vgb-sankey"
          data-a2ui-sankey
          data-sankey-balanced={diagram.unbalanced.length > 0 ? "false" : "true"}
        >
          {/*
           * Geometry only. `preserveAspectRatio="none"` means the 0–100 user
           * units map straight onto percentages of the box, which is what lets
           * the HTML labels above sit exactly on the bars below them — and a
           * ribbon's thickness is vertical, so the uniform vertical scale keeps
           * it proportional whatever width the column turns out to be.
           */}
          <svg
            aria-hidden="true"
            className="vgb-sankey-svg"
            preserveAspectRatio="none"
            viewBox="0 0 100 100"
          >
            {diagram.ribbons.map((ribbon) => (
              <path
                className="vgb-sankey-ribbon"
                d={ribbon.d}
                fill={toneText(ribbon.tone)}
                key={ribbon.key}
              />
            ))}
            {diagram.nodes.map((node) => (
              <rect
                fill={toneText(node.tone)}
                height={node.h}
                key={node.id}
                rx={0.6}
                width={NODE_W}
                x={node.x}
                y={node.y}
              />
            ))}
          </svg>
          {/*
           * Labels are HTML, never SVG `<text>`: an SVG label cannot wrap, and
           * this family's rule is that labels wrap rather than truncate. They
           * sit beside their bar — outside it on the last column so the text
           * never runs off the right-hand edge.
           */}
          {diagram.nodes.map((node) => {
            const last = node.depth === diagram.columns - 1;
            return (
              <span
                className="vgb-sankey-label"
                data-chart-mismatch={node.mismatch ? "true" : undefined}
                data-sankey-side={last ? "end" : "start"}
                key={node.id}
                style={
                  last
                    ? { right: asPct(100 - node.x), top: asPct(node.y + node.h / 2) }
                    : { left: asPct(node.x + NODE_W), top: asPct(node.y + node.h / 2) }
                }
              >
                <span className="vgb-sankey-name">{node.label}</span>
                <span className="vgb-chart-sub">
                  {formatValue(node.throughput)}
                  {node.mismatch
                    ? ` (in ${formatValue(node.inflow)} / out ${formatValue(node.outflow)})`
                    : ""}
                </span>
              </span>
            );
          })}
        </div>
      </ChartFrame>
    );
  },
});
