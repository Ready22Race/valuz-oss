"use client";

import { defineComponent } from "@openuidev/react-lang";
import {
  ResponsiveContainer,
  Sankey as RechartsSankey,
  Tooltip,
} from "recharts";
import type { SankeyLinkProps, SankeyNodeProps } from "recharts";

import { formatValue, readItems, seriesTone } from "../lib/chart";
import { ChartFrame } from "../lib/chart-parts";
import { readLooseNumber, readTextFromKeys } from "../lib/props";
import {
  CHART_INITIAL_DIMENSION,
  TOOLTIP_CONTENT_STYLE,
  TOOLTIP_CURSOR,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
} from "../lib/recharts-chrome";
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
  tone: Tone;
  /** Inflow and outflow disagree. Drawn as given, marked, never balanced. */
  mismatch: boolean;
}

interface Link {
  from: string;
  to: string;
  value: number;
}

interface Diagram {
  nodes: Node[];
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
 * The diagram, reconciled.
 *
 * The invariant this block exists for: **what arrives at a node should equal
 * what leaves it.** Where it does not, both figures are printed and the node
 * is flagged, never quietly balanced — silently scaling one side to match the
 * other would make a broken flow statement look like a sound one, which is
 * the failure this shape invites.
 *
 * Geometry (node position, ribbon curvature) is no longer computed here —
 * `<Sankey>` lays that out itself from `nodes`/`links`. What stays is the
 * business logic a layout engine cannot know: caps, validity, reconciliation,
 * and which column a node's label reads from.
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
    const value = readLooseNumber(
      record.value ?? record.amount ?? record.flow ?? record.weight,
    );
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

  const labels = new Map(
    declared.map((node) => [node.id, node.label || node.id]),
  );
  // A model routinely supplies links and forgets the node list. Every id a link
  // names is a node whether or not it was declared.
  for (const link of allLinks) {
    if (!labels.has(link.from)) labels.set(link.from, link.from);
    if (!labels.has(link.to)) labels.set(link.to, link.to);
  }

  const throughputOf = (id: string, links: Link[]) => {
    const inflow = links
      .filter((l) => l.to === id)
      .reduce((sum, l) => sum + l.value, 0);
    const outflow = links
      .filter((l) => l.from === id)
      .reduce((sum, l) => sum + l.value, 0);
    return { inflow, outflow };
  };

  const ranked = [...labels.keys()].sort((a, b) => {
    const left = throughputOf(a, allLinks);
    const right = throughputOf(b, allLinks);
    return (
      Math.max(right.inflow, right.outflow) -
      Math.max(left.inflow, left.outflow)
    );
  });
  const keptIds = ranked.slice(0, MAX_NODES);
  const kept = new Set(keptIds);
  const droppedNodes = ranked.length - keptIds.length;

  const reachable = allLinks.filter(
    (link) => kept.has(link.from) && kept.has(link.to),
  );
  const links = [...reachable]
    .sort((a, b) => b.value - a.value)
    .slice(0, MAX_LINKS);
  const droppedLinks = reachable.length - links.length;
  if (links.length === 0) return null;

  const depth = depthsOf(keptIds, links);
  const columnCount = Math.max(
    ...keptIds.map((id) => (depth.get(id) ?? 0) + 1),
  );

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
      tone: seriesTone(nodeDepth),
      mismatch:
        inflow > 0 && outflow > 0 && Math.abs(inflow - outflow) > EPSILON,
    };
  });

  const sources = nodes.filter((node) => node.inflow === 0);
  const rooted = sources.length > 0;

  return {
    nodes,
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
    const labelOf = (id: string) =>
      diagram.nodes.find((node) => node.id === id)?.label ?? id;

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
            .join(
              ", ",
            )}. Drawn exactly as given — the difference is not distributed.`
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

    // Index into `diagram.nodes`/`diagram.links`, in the same order they are
    // handed to `<Sankey data>` below — recharts keeps a node's `index` and a
    // link's `index` stable to that input order, so this is the lookup both
    // custom renderers use instead of trusting recharts' internal payload.
    const idIndex = new Map(
      diagram.nodes.map((node, index) => [node.id, index]),
    );
    const toneById = new Map(diagram.nodes.map((node) => [node.id, node.tone]));

    const sankeyData = {
      nodes: diagram.nodes.map((node) => ({ name: node.label })),
      links: diagram.links.flatMap((link) => {
        const source = idIndex.get(link.from);
        const target = idIndex.get(link.to);
        return source === undefined || target === undefined
          ? []
          : [{ source, target, value: link.value }];
      }),
    };

    function renderNode(nodeProps: SankeyNodeProps) {
      const { x, y, width, height, index } = nodeProps;
      const info = diagram!.nodes[index];
      if (!info) return null;
      const last = info.depth === diagram!.columns - 1;
      const labelX = last ? x - 6 : x + width + 6;
      const midY = y + height / 2;
      return (
        <g key={`node-${info.id}`}>
          <rect
            fill={toneText(info.tone)}
            height={height}
            rx={1}
            width={width}
            x={x}
            y={y}
          />
          <g
            className="vgb-sankey-label"
            data-chart-mismatch={info.mismatch ? "true" : undefined}
            data-sankey-side={last ? "end" : "start"}
          >
            <text
              className="vgb-sankey-name"
              dominantBaseline="middle"
              fill={toneText("neutral")}
              fontSize={11}
              textAnchor={last ? "end" : "start"}
              x={labelX}
              y={midY - 6}
            >
              {info.label}
            </text>
            <text
              className="vgb-chart-sub"
              dominantBaseline="middle"
              fill={toneText("neutral")}
              fontSize={10}
              opacity={0.75}
              textAnchor={last ? "end" : "start"}
              x={labelX}
              y={midY + 8}
            >
              {formatValue(info.throughput)}
              {info.mismatch
                ? ` (in ${formatValue(info.inflow)} / out ${formatValue(info.outflow)})`
                : ""}
            </text>
          </g>
        </g>
      );
    }

    function renderLink(linkProps: SankeyLinkProps) {
      const {
        sourceX,
        sourceY,
        sourceControlX,
        targetX,
        targetY,
        targetControlX,
        linkWidth,
        index,
      } = linkProps;
      const link = diagram!.links[index];
      const tone = link ? toneById.get(link.from) : undefined;
      return (
        <path
          className="vgb-sankey-ribbon"
          d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
          fill="none"
          key={`link-${index}`}
          stroke={toneText(tone)}
          strokeOpacity={0.5}
          strokeWidth={Math.max(1, linkWidth)}
        />
      );
    }

    return (
      <ChartFrame
        footnote={
          notes.length > 0 ? (
            <span
              data-chart-mismatch={
                diagram.unbalanced.length > 0 ? "true" : undefined
              }
            >
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
          className="vgb-sankey vgb-recharts"
          data-a2ui-sankey
          data-sankey-balanced={
            diagram.unbalanced.length > 0 ? "false" : "true"
          }
        >
          <ResponsiveContainer
            height="100%"
            initialDimension={CHART_INITIAL_DIMENSION}
            minHeight={0}
            minWidth={0}
            width="100%"
          >
            <RechartsSankey
              data={sankeyData}
              link={renderLink}
              node={renderNode}
              nodePadding={16}
              nodeWidth={10}
            >
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                cursor={TOOLTIP_CURSOR}
                isAnimationActive={false}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
              />
            </RechartsSankey>
          </ResponsiveContainer>
        </div>
      </ChartFrame>
    );
  },
});
