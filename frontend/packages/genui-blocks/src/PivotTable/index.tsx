"use client";

import { defineComponent } from "@openuidev/react-lang";

import { formatValue } from "../lib/chart";
import { readItems } from "../lib/collections";
import { readLooseNumber, readText, readTextFromKeys, toArray } from "../lib/props";
import { PivotTableSchema } from "./schema";

export { PivotRowSchema, PivotTableSchema } from "./schema";

/*
 * ── Results, never controls ───────────────────────────────────────
 *
 * A pivot table is normally a machine you drive: drag a field to the rows, drop
 * another on the columns, expand a group. None of that exists here. This is the
 * cross-tabulation the answer already computed, printed. There is no field
 * list, no expander, no drop target, and no header that can be moved — a
 * chevron on a row label would promise a sub-total that nothing can produce.
 *
 * ── The one thing it does check ───────────────────────────────────
 *
 * Same discipline as Waterfall: a supplied total is *verified*, never
 * recomputed behind the reader's back. When the cells disagree with the total
 * the model wrote, the reported figure is what prints — hiding it would hide
 * the disagreement — and the cell is flagged with a footnote naming both
 * figures. When a row holds a cell that is not a number, no total in that line
 * is checked at all: an unverifiable total is left plain rather than accused.
 */

/**
 * Float tolerance for the reconciliation.
 *
 * `0.1 + 0.2` is `0.30000000000000004`, so an exact comparison would flag a
 * pivot that adds up perfectly. Anything a model really got wrong is wrong by
 * orders of magnitude more.
 */
const EPSILON = 1e-6;

/** Mismatches enumerated in the footnote before it stops naming them. */
const MAX_NAMED = 3;

interface PivotRow {
  cells: string[];
  label: string;
  numbers: (number | undefined)[];
}

interface Mismatch {
  computed: number;
  reported: number;
  where: string;
}

function readPivotRow(record: Record<string, unknown>): PivotRow {
  const values = toArray(record.values ?? record.cells ?? record.data ?? record.row);
  return {
    cells: values.map(readText),
    label: readTextFromKeys(record, ["label", "title", "name", "row"]),
    numbers: values.map(readLooseNumber),
  };
}

/**
 * The sum of a line, or undefined when the line cannot be summed.
 *
 * A hole is not a zero and a dash is not a zero: if any cell that exists fails
 * to read as a number, the block has no arithmetic it is entitled to do, so it
 * checks nothing rather than checking something it made up.
 */
function sumLine(cells: (number | undefined)[]): number | undefined {
  let total = 0;
  for (const cell of cells) {
    if (cell === undefined) return undefined;
    total += cell;
  }
  return total;
}

export const PivotTable = defineComponent({
  name: "PivotTable",
  props: PivotTableSchema,
  description:
    "A cross-tabulation you have already computed: one measure summarised by a row dimension against a column dimension (revenue by region and quarter, headcount by team and level). " +
    "rowLabel and columnLabel name the two dimensions, columns lists the column values in order, and rows is {label, values[]} with values lining up index-for-index with columns. " +
    "rowTotals, columnTotals and grandTotal are optional and are checked against the cells rather than trusted — a total that disagrees is printed as written and flagged, so supply them only when you want them verified, and never as a way of stating a number the cells do not support. " +
    "unit names the basis of every cell (\"USD m\", \"headcount\") since one measure covers the whole grid. Nothing here can be pivoted, expanded or re-grouped: it is the finished table, so do not describe it as one the reader can drive.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.rows ?? raw.data ?? raw.items, "label")
      .map(readPivotRow)
      .filter((row) => row.label || row.cells.length);
    if (!rows.length) return null;

    const columns = toArray(raw.columns ?? raw.cols ?? raw.headers).map(readText);
    const columnCount = Math.max(columns.length, ...rows.map((row) => row.cells.length));

    const rowTotals = toArray(raw.rowTotals ?? raw.row_totals);
    const columnTotals = toArray(raw.columnTotals ?? raw.column_totals);
    const grandTotalRaw = raw.grandTotal ?? raw.grand_total ?? raw.total;
    const grandTotal = readText(grandTotalRaw);

    const hasTotalColumn = rowTotals.length > 0 || grandTotal !== "";
    const hasTotalRow = columnTotals.length > 0 || grandTotal !== "";

    /* ── The reconciliation ──────────────────────────────────────── */

    const mismatches: Mismatch[] = [];
    const check = (reportedRaw: unknown, computed: number | undefined, where: string) => {
      const reported = readLooseNumber(reportedRaw);
      if (reported === undefined || computed === undefined) return false;
      if (Math.abs(reported - computed) <= EPSILON) return false;
      mismatches.push({ computed, reported, where });
      return true;
    };

    // Padded to the full width first: a row with fewer cells than there are
    // columns has a *hole*, not a zero, and its total is therefore something
    // the block cannot check rather than something it can fail.
    const matrix = rows.map((row) =>
      Array.from({ length: columnCount }, (_, index) => row.numbers[index]),
    );

    const rowTotalBad = rows.map((row, index) =>
      check(rowTotals[index], sumLine(matrix[index] ?? []), row.label || `row ${index + 1}`),
    );

    const columnTotalBad = Array.from({ length: columnCount }, (_, index) =>
      check(
        columnTotals[index],
        sumLine(matrix.map((cells) => cells[index])),
        columns[index] || `column ${index + 1}`,
      ),
    );

    const grandBad = check(grandTotalRaw, sumLine(matrix.flat()), "the grand total");

    const title = readTextFromKeys(raw, ["title", "label"]);
    const rowLabel = readTextFromKeys(raw, ["rowLabel", "row_label", "rows_label"]);
    const columnLabel = readTextFromKeys(raw, [
      "columnLabel",
      "column_label",
      "columns_label",
    ]);
    const unit = readTextFromKeys(raw, ["unit", "units", "basis"]);

    const named = mismatches
      .slice(0, MAX_NAMED)
      .map(
        (m) =>
          `${m.where} reports ${formatValue(m.reported)}, cells sum to ${formatValue(m.computed)}`,
      )
      .join("; ");
    const note = [
      unit ? `All values in ${unit}.` : "",
      mismatches.length
        ? `Does not reconcile: ${named}${
            mismatches.length > MAX_NAMED ? `; and ${mismatches.length - MAX_NAMED} more` : ""
          }.`
        : "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <section
        className="vgb-collection vgb-pivot"
        data-slot="vgb-pivot-table"
        data-a2ui-component="pivot-table"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-scroll-x">
          <table className="vgb-pivot-table">
            <thead>
              {/* Two header rows: the dimension names, then the column values
                  under them. One row would have to choose between naming the
                  axis and naming the columns, and a pivot needs both. */}
              <tr>
                <th className="vgb-pivot-corner" scope="col">
                  {rowLabel}
                </th>
                <th className="vgb-pivot-axis" colSpan={columnCount} scope="colgroup">
                  {columnLabel}
                </th>
                {hasTotalColumn ? <th className="vgb-pivot-axis" scope="col" /> : null}
              </tr>
              <tr>
                <th className="vgb-pivot-corner" scope="col" />
                {Array.from({ length: columnCount }, (_, index) => (
                  <th className="vgb-pivot-head" key={index} scope="col">
                    {columns[index] ?? ""}
                  </th>
                ))}
                {hasTotalColumn ? (
                  <th className="vgb-pivot-head vgb-pivot-total-head" scope="col">
                    Total
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr className="vgb-pivot-row" key={`${row.label}-${rowIndex}`}>
                  <th className="vgb-pivot-label" scope="row">
                    {row.label}
                  </th>
                  {Array.from({ length: columnCount }, (_, index) => (
                    <td className="vgb-pivot-cell" key={index}>
                      {row.cells[index] ?? ""}
                    </td>
                  ))}
                  {hasTotalColumn ? (
                    <td
                      className="vgb-pivot-cell vgb-pivot-total"
                      data-mismatch={rowTotalBad[rowIndex] ? "true" : undefined}
                    >
                      {readText(rowTotals[rowIndex] ?? "")}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
            {hasTotalRow ? (
              <tfoot>
                <tr className="vgb-pivot-row vgb-pivot-total-row">
                  <th className="vgb-pivot-label vgb-pivot-total" scope="row">
                    Total
                  </th>
                  {Array.from({ length: columnCount }, (_, index) => (
                    <td
                      className="vgb-pivot-cell vgb-pivot-total"
                      data-mismatch={columnTotalBad[index] ? "true" : undefined}
                      key={index}
                    >
                      {readText(columnTotals[index] ?? "")}
                    </td>
                  ))}
                  {hasTotalColumn ? (
                    <td
                      className="vgb-pivot-cell vgb-pivot-total vgb-pivot-grand"
                      data-mismatch={grandBad ? "true" : undefined}
                    >
                      {grandTotal}
                    </td>
                  ) : null}
                </tr>
              </tfoot>
            ) : null}
          </table>
        </div>
        {note ? (
          <p className="vgb-collection-note" data-slot="vgb-pivot-note">
            {note}
          </p>
        ) : null}
      </section>
    );
  },
});
