import "./styles.css";

export type { BlockComponent } from "./blocks";
export type { BlockPropSpec, BlockSpec } from "./catalog";
export {
  blockCatalog,
  blockNames,
  describeBlock,
  renderBlockCatalogText,
} from "./catalog";
export { blockAdditionalRules, blockExamples, valuzPromptOptions } from "./prompt";
export {
  createBlockOnlyLibrary,
  createValuzLibrary,
  blockComponentGroups,
  blockComponents,
} from "./library";

// Metric tiles
export * from "./MiniCard";
export * from "./Metric";
export * from "./IconTag";

// Cards & tiles
export * from "./CardBlock";
export * from "./CompositeCard";
export * from "./ContextCard";
export * from "./DataTileCard";
export * from "./OptionCard";
export * from "./OverviewCard";
export * from "./ProfileTile";
export * from "./StatsCard";
export * from "./TileOption";
export * from "./ValueCard";
export * from "./VisualFirstCard";

// Citations & sources
export * from "./Citation";

// Report documents
export * from "./Report";

// Market data
export * from "./DataList";
export * from "./MarketIndexGrid";
export * from "./MarketBreadth";

// Layout & structure
export * from "./Layout";

// Content & annotation
export * from "./Avatar";
export * from "./Footnote";
export * from "./JsonView";
export * from "./KeyValue";
export * from "./MetricGroup";
export * from "./RichText";
export * from "./StatDelta";

// Collections
export * from "./ComparisonTable";
export * from "./Feed";
export * from "./Outline";
export * from "./StatusList";
export * from "./Timeline";

// Feedback states
export * from "./EmptyState";
export * from "./ErrorState";
export * from "./Progress";
export * from "./Result";
export * from "./Skeleton";

// Charts
export * from "./BoxPlot";
export * from "./CategoryBars";
export * from "./Funnel";
export * from "./Heatmap";
export * from "./Histogram";
export * from "./Sparkline";
export * from "./Treemap";
export * from "./Waterfall";

// Diagrams
export * from "./Mermaid";
