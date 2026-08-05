import type { ComponentGroup, DefinedComponent } from "@openuidev/react-lang";

import { Avatar } from "./Avatar";
import { BoxPlot } from "./BoxPlot";
import { GroupedBar, StackedBar } from "./CategoryBars";
import { ComparisonTable, DiffView } from "./ComparisonTable";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { Feed } from "./Feed";
import { Footnote, FootnoteList } from "./Footnote";
import { Funnel } from "./Funnel";
import { Heatmap } from "./Heatmap";
import { Histogram } from "./Histogram";
import { JsonView } from "./JsonView";
import { KeyValue, KeyValueGroup } from "./KeyValue";
import {
  AspectRatio,
  Cluster,
  Collapsible,
  DashboardGrid,
  Divider,
  Inline,
  Page,
  PageFooter,
  PageHeader,
  ScrollArea,
  Spacer,
} from "./Layout";
import { MetricGroup } from "./MetricGroup";
import { Breadcrumb, DescriptionList, Tree } from "./Outline";
import { Progress } from "./Progress";
import { Result } from "./Result";
import { RichText } from "./RichText";
import { Skeleton } from "./Skeleton";
import { Sparkline } from "./Sparkline";
import { StatDelta } from "./StatDelta";
import { ProgressList, StatusItem, StatusList } from "./StatusList";
import { ActivityFeed, ActivityItem, Timeline, TimelineItem } from "./Timeline";
import { Treemap } from "./Treemap";
import { BridgeChart, Waterfall } from "./Waterfall";
import { MediumCardBlock, SmallCardBlock } from "./CardBlock";
import { Citation, CondensedSources, SourceItem, SourceList } from "./Citation";
import { CompositeCard } from "./CompositeCard";
import { ContextCard } from "./ContextCard";
import { DataList, DataListItem } from "./DataList";
import { DataTileCard } from "./DataTileCard";
import { IconTag, IconText } from "./IconTag";
import { MarketBreadth } from "./MarketBreadth";
import { MarketIndexCard, MarketIndexGrid } from "./MarketIndexGrid";
import { Mermaid, MermaidBadge } from "./Mermaid";
import { Metric } from "./Metric";
import { MiniCard, MiniCardBlock } from "./MiniCard";
import { OptionCard, OptionCards } from "./OptionCard";
import { OverviewCard } from "./OverviewCard";
import { ProfileTile } from "./ProfileTile";
import {
  ReportDocument,
  ReportFrontPage,
  ReportHeadline,
  ReportImage,
  ReportKeyStatement,
  ReportPage,
  ReportSection,
  ReportTable,
  ReportTocPage,
} from "./Report";
import { StatsCard } from "./StatsCard";
import { TileOption, TileOptionBlock } from "./TileOption";
import { ValueCard } from "./ValueCard";
import { VisualFirstCard } from "./VisualFirstCard";

/**
 * A block of any shape.
 *
 * `defineComponent` parameterises its return type by the component's own
 * schema, and the renderer inside is contravariant in props — so a
 * `DefinedComponent<typeof MiniCardSchema>` is *not* assignable to the
 * default-parameter `DefinedComponent`, and a heterogeneous registry cannot be
 * typed with it. `createLibrary` hits the same wall and resolves it the same
 * way (`DefinedComponent<any, C>[]`); matching upstream keeps this list
 * expressible without casting at every element.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type BlockComponent = DefinedComponent<any>;

/**
 * The registry. Every block must appear in both lists below: `blockComponents`
 * makes it renderable, `blockComponentGroups` makes the model aware it exists.
 * A component missing from the groups still renders if the model somehow emits
 * it, but nothing will ever tell the model to.
 */

export const blockComponents: BlockComponent[] = [
  // Metric tiles
  MiniCard,
  MiniCardBlock,
  Metric,
  IconTag,
  IconText,
  // Cards & tiles
  SmallCardBlock,
  MediumCardBlock,
  StatsCard,
  DataTileCard,
  ValueCard,
  OverviewCard,
  ContextCard,
  CompositeCard,
  VisualFirstCard,
  ProfileTile,
  OptionCards,
  OptionCard,
  TileOptionBlock,
  TileOption,
  // Citations & sources
  Citation,
  CondensedSources,
  SourceList,
  SourceItem,
  // Report pages
  ReportDocument,
  ReportPage,
  ReportFrontPage,
  ReportTocPage,
  ReportSection,
  ReportHeadline,
  ReportKeyStatement,
  ReportTable,
  ReportImage,
  // Market data
  MarketIndexGrid,
  MarketIndexCard,
  MarketBreadth,
  DataList,
  DataListItem,
  // Layout & structure
  Page,
  PageHeader,
  PageFooter,
  DashboardGrid,
  Inline,
  Cluster,
  Divider,
  Spacer,
  AspectRatio,
  ScrollArea,
  Collapsible,
  // Content & annotation
  RichText,
  KeyValueGroup,
  KeyValue,
  DescriptionList,
  FootnoteList,
  Footnote,
  JsonView,
  MetricGroup,
  StatDelta,
  Avatar,
  // Collections
  Timeline,
  TimelineItem,
  ActivityFeed,
  ActivityItem,
  Feed,
  StatusList,
  StatusItem,
  ProgressList,
  ComparisonTable,
  DiffView,
  Tree,
  Breadcrumb,
  // Feedback states
  EmptyState,
  ErrorState,
  Result,
  Progress,
  Skeleton,
  // Charts
  Sparkline,
  Waterfall,
  BridgeChart,
  Funnel,
  Heatmap,
  Histogram,
  BoxPlot,
  Treemap,
  GroupedBar,
  StackedBar,
  // Diagrams
  Mermaid,
  MermaidBadge,
];

export const blockComponentGroups: ComponentGroup[] = [
  {
    name: "Metric Tiles",
    components: ["MiniCardBlock", "MiniCard", "Metric", "IconTag", "IconText"],
    notes: [
      "Prefer MiniCardBlock over a row of Cards whenever every entry is a single label + figure; Metric is the unframed version, for use inside a surface that already has a frame. IconTag marks something with a lucide icon, and IconText pairs one with a line of text.",
    ],
  },
  {
    name: "Cards & Tiles",
    components: [
      "SmallCardBlock",
      "MediumCardBlock",
      "StatsCard",
      "DataTileCard",
      "ValueCard",
      "OverviewCard",
      "ContextCard",
      "CompositeCard",
      "VisualFirstCard",
      "ProfileTile",
      "OptionCards",
      "OptionCard",
      "TileOptionBlock",
      "TileOption",
    ],
    notes: [
      "Lay card sets out with SmallCardBlock (children are a label plus a figure) or MediumCardBlock (children carry body text); OptionCard and TileOption must sit inside OptionCards / TileOptionBlock, and their selected state is styling only — nothing is clickable.",
    ],
  },
  {
    name: "Citations & Sources",
    components: ["Citation", "CondensedSources", "SourceList", "SourceItem"],
    notes: [
      "Attach a Citation to every claim taken from a source, then close the answer with CondensedSources — reach for SourceList only when the sources are themselves part of the argument.",
    ],
  },
  {
    name: "Report Pages",
    components: [
      "ReportDocument",
      "ReportPage",
      "ReportFrontPage",
      "ReportTocPage",
      "ReportSection",
      "ReportHeadline",
      "ReportKeyStatement",
      "ReportTable",
      "ReportImage",
    ],
    notes: [
      "Long-form printable documents: ReportDocument wraps ReportFrontPage, an optional ReportTocPage, then one ReportPage per page — fill pages with the ordinary shared blocks (MiniCardBlock, TextContent, charts), never a report-specific twin of them.",
    ],
  },
  {
    name: "Market Data",
    components: [
      "MarketIndexGrid",
      "MarketIndexCard",
      "MarketBreadth",
      "DataList",
      "DataListItem",
    ],
    notes: [
      "Market answers open with a MarketIndexGrid of quotes and, when the answer claims the move was broad, a MarketBreadth beside it; DataList is the ranking/leaderboard shape and beats a Table whenever every row is name + figure + change.",
    ],
  },
  {
    name: "Layout & Structure",
    components: [
      "Page", "PageHeader", "PageFooter", "DashboardGrid", "Inline", "Cluster",
      "Divider", "Spacer", "AspectRatio", "ScrollArea", "Collapsible",
    ],
    notes: [
      "Root a laid-out answer in a Page and place its blocks with DashboardGrid (equal columns that re-fit themselves — never state a column count), Inline (two or three blocks sharing a line) or Cluster (many small tags). A Page is a frame that grows, so anything taller or wider than its column goes in a ScrollArea, and anything the reader may skip goes in a Collapsible. Prefer a container's own gap to a Spacer, and reach for Divider only between sections that are already whole.",
    ],
  },
  {
    name: "Content & Annotation",
    components: [
      "RichText", "KeyValueGroup", "KeyValue", "DescriptionList",
      "FootnoteList", "Footnote", "JsonView", "MetricGroup", "StatDelta", "Avatar",
    ],
    notes: [
      "Named fields go in a KeyValueGroup and defined vocabulary in a DescriptionList — a Table only once a row carries more than a label and a figure. MetricGroup is for figures that only mean something together, and its basis line is not optional in practice: a set of figures with no stated as-of invites a comparison that is not valid. Close a qualified answer with a FootnoteList for the answer's own caveats, keeping Citation and SourceList for other people's sources. RichText never parses markup — anything needing bold, headings, lists or links is a MarkDownRenderer.",
    ],
  },
  {
    name: "Collections & Structure",
    components: [
      "Timeline", "TimelineItem", "ActivityFeed", "ActivityItem", "Feed",
      "StatusList", "StatusItem", "ProgressList", "ComparisonTable", "DiffView",
      "Tree", "Breadcrumb",
    ],
    notes: [
      "Repeating entries that share a shape: Timeline for dated milestones and ActivityFeed for who-did-what (TimelineItem / ActivityItem / StatusItem only ever sit inside their parent); StatusList and ProgressList for state and completion; ComparisonTable for the same measures across two to four subjects and DiffView for before/after of one; Tree and Breadcrumb for structure without time. Every one renders a finished answer — nothing is clickable or live.",
    ],
  },
  {
    name: "Feedback States",
    components: ["EmptyState", "ErrorState", "Result", "Progress", "Skeleton"],
    notes: [
      "EmptyState when nothing matched and the absence is the answer, ErrorState when something failed and there is a technical line worth showing, Result to report the outcome of something that finished; Progress is a determinate bar for a figure you already have, and Skeleton stands in for content that is not here. None of them can act — no retry, no reload, nothing behind them — so never write text asking the reader to press one.",
    ],
  },
  {
    name: "Charts",
    components: [
      "Sparkline", "Waterfall", "BridgeChart", "Funnel", "Heatmap",
      "Histogram", "BoxPlot", "Treemap", "GroupedBar", "StackedBar",
    ],
    notes: [
      "Hand-drawn shapes OpenUI's own charts lack — reach for BarChart / LineChart / AreaChart / PieChart / ScatterChart / RadarChart first and come here only for these. Every one needs its values in a single unit, named by `unit`; Waterfall computes its own closing total and flags a reported figure that disagrees; a chart with no data renders nothing rather than an empty frame.",
    ],
  },
  {
    name: "Diagrams",
    components: ["Mermaid", "MermaidBadge"],
    notes: [
      "Mermaid shows the diagram definition as text, not a drawn picture — always label it with a MermaidBadge naming the diagram type.",
    ],
  },
];
