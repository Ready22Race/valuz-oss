"use client";

import { defineComponent } from "@openuidev/react-lang";
import { Fragment } from "react";

import { readItems } from "../lib/collections";
import { readTextFromKeys } from "../lib/props";
import { BreadcrumbSchema, DescriptionListSchema, TreeSchema } from "./schema";

export {
  BreadcrumbItemSchema,
  BreadcrumbSchema,
  DescriptionItemSchema,
  DescriptionListSchema,
  TreeItemSchema,
  TreeSchema,
} from "./schema";

/**
 * How many levels are drawn. Six is already deeper than a reader can hold in a
 * chat column, and the indent of a seventh would leave no room for its label.
 * Anything below the cap is replaced by a single ellipsis row, so the outline
 * says that it was truncated instead of pretending the branch ended.
 */
const MAX_DEPTH = 6;

/** One node, flattened into the row that draws it. */
interface TreeRow {
  children: Record<string, unknown>[];
  depth: number;
  detail: string;
  label: string;
}

/**
 * Depth-first flatten.
 *
 * The rows come out as one flat list with a depth number each, rather than as
 * nested elements: indentation is the only thing depth changes, and a nest of
 * containers would give every level its own width to shrink inside — which is
 * how a deep branch ends up narrower than its own text.
 */
function flattenTree(
  nodes: Record<string, unknown>[],
  depth: number,
  out: TreeRow[],
): TreeRow[] {
  for (const node of nodes) {
    const children = readItems(node.children ?? node.items ?? node.nodes, "label");
    out.push({
      children,
      depth,
      detail: readTextFromKeys(node, ["detail", "description", "note", "value"]),
      label: readTextFromKeys(node, ["label", "title", "name", "text"]),
    });
    if (!children.length) continue;
    if (depth + 1 < MAX_DEPTH) {
      flattenTree(children, depth + 1, out);
    } else {
      out.push({ children: [], depth: depth + 1, detail: "", label: "…" });
    }
  }
  return out;
}

export const Tree = defineComponent({
  name: "Tree",
  props: TreeSchema,
  description:
    "A nested outline drawn with indentation: a file tree, an org chart, a taxonomy, the sections of a document. " +
    "items is {label, detail?, children?} and children is an array of the same shape, nested as deep as the structure goes — detail is a short qualifier shown after the label, such as a size, a count or a role. " +
    "Six levels are drawn; anything deeper is replaced by a single … row, so keep the meaningful structure in the top six. " +
    "This is a static outline: no row collapses, expands or can be clicked. Use DescriptionList when the entries are flat term/definition pairs.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const nodes = readItems(raw.items ?? raw.nodes ?? raw.tree, "label");
    const rows = flattenTree(nodes, 0, []).filter((row) => row.label || row.detail);
    // Nothing to show means nothing rendered: an empty frame reads as data that
    // failed to load.
    if (!rows.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-tree"
        data-slot="vgb-tree"
        data-a2ui-component="tree"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <div className="vgb-tree-rows" role="list">
          {rows.map((row, index) => (
            <div
              className="vgb-tree-row"
              data-depth={row.depth}
              data-slot="vgb-tree-item"
              key={`${row.label}-${index}`}
              role="listitem"
              /* Depth is data, so the indent is the one declaration that cannot
                 live in the stylesheet. `padding-inline-start` rather than
                 `padding-left` so a right-to-left host indents the right way. */
              style={{ paddingInlineStart: `calc(var(--openui-space-m) * ${row.depth})` }}
            >
              <span aria-hidden="true" className="vgb-tree-marker" />
              <span className="vgb-tree-label">{row.label}</span>
              {row.detail ? <span className="vgb-tree-detail">{row.detail}</span> : null}
            </div>
          ))}
        </div>
      </section>
    );
  },
});

export const Breadcrumb = defineComponent({
  name: "Breadcrumb",
  props: BreadcrumbSchema,
  description:
    "The chain of ancestors above something, from the outermost down to the thing itself: 研究 / 行业 / 半导体, or Portfolio / Fund II / Holding. " +
    "items is {label, current?} in that outer-to-inner order, and the last entry is treated as the current one unless a different entry sets current. " +
    "Use it to say where an answer sits, not to offer a way out of it — these are labels, not links, and nothing here navigates.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.path ?? raw.trail, "label")
      .map((item) => ({
        current: item.current === true,
        label: readTextFromKeys(item, ["label", "title", "name", "text"]),
      }))
      .filter((row) => row.label);
    if (!rows.length) return null;
    const marked = rows.some((row) => row.current);

    return (
      <div
        className="vgb-collection vgb-breadcrumb"
        data-slot="vgb-breadcrumb"
        data-a2ui-component="breadcrumb"
        role="list"
      >
        {rows.map((row, index) => {
          const current = marked ? row.current : index === rows.length - 1;
          return (
            <Fragment key={`${row.label}-${index}`}>
              {index ? (
                <span aria-hidden="true" className="vgb-breadcrumb-separator">
                  /
                </span>
              ) : null}
              <span
                aria-current={current ? "true" : undefined}
                className="vgb-breadcrumb-item"
                data-current={current ? "true" : undefined}
                role="listitem"
              >
                {row.label}
              </span>
            </Fragment>
          );
        })}
      </div>
    );
  },
});

export const DescriptionList = defineComponent({
  name: "DescriptionList",
  props: DescriptionListSchema,
  description:
    "Term and definition pairs, the term on the left and its explanation on the right: a glossary, a spec sheet, the fields of a record, the assumptions behind a figure. " +
    "items is {term, description} — term is the short name and description the sentence that explains it. " +
    "Reach for MiniCardBlock instead when every right-hand side is a single figure, and for DataList when the rows are a ranking.",
  component: ({ props }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readItems(raw.items ?? raw.pairs ?? raw.entries, "term")
      .map((item) => ({
        description: readTextFromKeys(item, ["description", "detail", "value", "text"]),
        term: readTextFromKeys(item, ["term", "label", "title", "name", "key"]),
      }))
      .filter((row) => row.term || row.description);
    if (!rows.length) return null;
    const title = readTextFromKeys(raw, ["title", "label"]);

    return (
      <section
        className="vgb-collection vgb-description-list"
        data-slot="vgb-description-list"
        data-a2ui-component="description-list"
      >
        {title ? <div className="vgb-collection-title">{title}</div> : null}
        <dl className="vgb-description-rows">
          {rows.map((row, index) => (
            <div className="vgb-description-row" key={`${row.term}-${index}`}>
              <dt className="vgb-description-term">{row.term}</dt>
              <dd className="vgb-description-detail">{row.description}</dd>
            </div>
          ))}
        </dl>
      </section>
    );
  },
});
