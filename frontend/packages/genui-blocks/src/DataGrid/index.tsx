"use client";

import { defineComponent } from "@openuidev/react-lang";

import { readItems } from "../lib/collections";
import { formatCount, readRecord, readText, readTextFromKeys, toArray } from "../lib/props";
import type { Align } from "../lib/schema";
import { DataGridSchema } from "./schema";

export { DataGridColumnSchema, DataGridSchema } from "./schema";

/*
 * ── Results, never controls ───────────────────────────────────────
 *
 * A grid is normally something you operate. This one is a picture of a result,
 * and the line it must not cross is exactly there:
 *
 *  - The order on screen is the order that arrived. `sortedBy` is printed as a
 *    fact about how the answer was built — the block does not sort by it, and
 *    a header that offered to would be offering something nothing is behind.
 *  - `filteredBy` is the same: it says which rows were selected upstream. No
 *    row is hidden here.
 *  - Nothing renders a button, a chevron, a resize handle or a focusable cell.
 *    The only affordance in the block is the horizontal scroll of `.vgb-scroll-x`,
 *    which is a way of seeing the data, not a way of changing it.
 *
 * The one edit the block does make is a cap on rows, and it says so in words
 * with both counts — a table silently showing its first hundred rows is a
 * different claim than the data makes.
 */

/**
 * Rows rendered before the grid stops.
 *
 * Not a design preference: a thousand-row table in a chat column is a scroll
 * trap that buries whatever follows it. The cap is stated on screen with the
 * full count, so the reader knows what they are not seeing.
 */
const MAX_ROWS = 100;

interface GridColumn {
  align: Align | undefined;
  emphasis: boolean;
  label: string;
  unit: string;
}

const ALIGNMENTS = new Set(["left", "center", "right"]);

function readColumn(record: Record<string, unknown>): GridColumn {
  const align = readTextFromKeys(record, ["align", "alignment"]).trim().toLowerCase();
  const emphasis = record.emphasis ?? record.highlight ?? record.primary;
  return {
    align: ALIGNMENTS.has(align) ? (align as Align) : undefined,
    emphasis: emphasis === true || emphasis === "true",
    label: readTextFromKeys(record, ["label", "title", "name", "header"]),
    unit: readTextFromKeys(record, ["unit", "units"]),
  };
}

/**
 * A row as a list of already-formatted cells.
 *
 * The schema asks for an array, and an array is what usually arrives — but a
 * model that has just written `rows: [{ cells: [...] }]` for a sibling block
 * writes it here too, and losing the row over the wrapper would cost more than
 * reading through it.
 */
function readRow(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(readText);
  const record = readRecord(value);
  const cells = record.cells ?? record.values ?? record.row ?? record.data;
  if (cells !== undefined) return toArray(cells).map(readText);
  return [];
}

/*
 * The stated facts, normalised just enough not to stutter. A model writes
 * "revenue, descending" as often as "sorted by revenue", and "Sorted by sorted
 * by revenue" reads as a rendering bug rather than as a fact.
 */
const SORT_LEAD = /^(sorted|sorting|sort|ordered|order)\s+(by\s+)?/i;
const FILTER_LEAD = /^(filtered|filtering|filter)\s+(by|to|on)?\s*/i;

function statedFact(text: string, lead: RegExp, prefix: string): string {
  const value = text.trim().replace(lead, "").trim();
  return value ? `${prefix} ${value}` : "";
}

export const DataGrid = defineComponent({
  name: "DataGrid",
  props: DataGridSchema,
  description:
    "A dense table for a wide result set — more columns than a Table reads well with, every cell already formatted. " +
    "columns is {label, unit?, align?, emphasis?} per column (align right for figures, emphasis for the one column the answer is about); rows is an array of arrays whose cells line up index-for-index with columns — pass an empty string for a cell you do not have rather than shifting the rest along. " +
    "sortedBy and filteredBy are sentences describing how the result was already produced (\"revenue, descending\", \"US listings only\"): they are printed under the table as statements of fact, and nothing in the block sorts, filters or can be operated — never write a column label that invites the reader to click, drag or reorder. " +
    "The first column sticks while the rest scroll sideways inside the block. Use ComparisonTable for a handful of subjects side by side, DataList for a ranking of name + figure + change.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const columns = readItems(raw.columns ?? raw.headers ?? raw.fields, "label").map(
      readColumn,
    );
    const allRows = toArray(raw.rows ?? raw.data ?? raw.items)
      .map(readRow)
      .filter((row) => row.some((cell) => cell !== ""));
    // Nothing to show means nothing rendered: an empty frame reads as data that
    // failed to load.
    if (!allRows.length) return null;

    const rows = allRows.slice(0, MAX_ROWS);
    // A row carrying more cells than there are named columns still has to show
    // them — dropping a value is the one edit this block must never make — so
    // the surplus renders under a blank heading.
    const columnCount = Math.max(columns.length, ...rows.map((row) => row.length));
    const title = readTextFromKeys(raw, ["title", "label"]);

    const facts = [
      statedFact(readTextFromKeys(raw, ["sortedBy", "sorted_by", "sort"]), SORT_LEAD, "Sorted by"),
      statedFact(
        readTextFromKeys(raw, ["filteredBy", "filtered_by", "filter"]),
        FILTER_LEAD,
        "Filtered to",
      ),
      allRows.length > rows.length
        ? `Showing ${formatCount(rows.length)} of ${formatCount(allRows.length)} rows`
        : "",
    ].filter(Boolean);

    return (
      <section
        className="vgb-collection vgb-datagrid"
        data-slot="vgb-data-grid"
        data-a2ui-component="data-grid"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        {/* Wide content scrolls inside its own box; the chat column must never
            scroll sideways. */}
        <div className="vgb-scroll-x">
          <table className="vgb-datagrid-table">
            <thead>
              <tr>
                {Array.from({ length: columnCount }, (_, index) => {
                  const column = columns[index];
                  return (
                    <th
                      className={
                        index === 0
                          ? "vgb-datagrid-head vgb-datagrid-stick vgb-datagrid-stick-head"
                          : "vgb-datagrid-head"
                      }
                      data-align={column?.align}
                      data-emphasis={column?.emphasis ? "true" : undefined}
                      key={index}
                      scope="col"
                    >
                      {column?.label ?? ""}
                      {column?.unit ? (
                        <span className="vgb-datagrid-unit">{column.unit}</span>
                      ) : null}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr className="vgb-datagrid-row" key={rowIndex}>
                  {Array.from({ length: columnCount }, (_, index) => {
                    const column = columns[index];
                    const cell = row[index] ?? "";
                    // The first cell names the row, so it is the row's header
                    // as well as the column that stays put while the rest
                    // scroll away from it.
                    return index === 0 ? (
                      <th
                        className="vgb-datagrid-cell vgb-datagrid-name vgb-datagrid-stick"
                        data-align={column?.align}
                        data-emphasis={column?.emphasis ? "true" : undefined}
                        key={index}
                        scope="row"
                      >
                        {cell}
                      </th>
                    ) : (
                      <td
                        className="vgb-datagrid-cell"
                        data-align={column?.align}
                        data-emphasis={column?.emphasis ? "true" : undefined}
                        key={index}
                      >
                        {cell}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {facts.length ? (
          <p className="vgb-collection-note" data-slot="vgb-data-grid-facts">
            {facts.join(" · ")}
          </p>
        ) : null}
      </section>
    );
  },
});
