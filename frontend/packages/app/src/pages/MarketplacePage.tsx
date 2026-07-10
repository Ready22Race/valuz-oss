import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  CloudOff,
  Download,
  Eye,
  Plug,
  Sparkles,
  Star,
  Store,
} from "lucide-react";
import { BackLink, Button, EmptyState, SearchInput, cn } from "@valuz/ui";
import type { MarketplaceCategory, MarketplaceItem } from "@valuz/core";
import { marketplaceApi, useTranslation } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { MarketplaceImportDialog } from "../components/MarketplaceImportDialog";
import { MarketplaceConnectorDialog } from "../components/MarketplaceConnectorDialog";
import {
  MarketplaceBadgePill,
  MarketplaceSourcePill,
  formatCount,
  marketplaceIcon,
  tintFor,
} from "../components/marketplace-ui";

type MarketTab = "agents" | "skills" | "connectors";

const SKILL_PAGE_SIZE = 30;
const CONNECTOR_PAGE_SIZE = 20;

/** Full-screen marketplace — two tabs (Agents / Skills) per the product
 * prototype (docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md).
 * Agents: Valuz official/curated Team grid. Skills: SkillHub category rail
 * (curated allowlist, server-side). */
export function MarketplacePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    setHideHeader,
    setHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
    setContentInnerClassName,
  } = useProjectOutlet();
  const tr = useCallback(
    (key: string, params?: Record<string, string | number>) =>
      t(key as Parameters<typeof t>[0], params),
    [t],
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const tab: MarketTab =
    requestedTab === "skills" || requestedTab === "connectors" ? requestedTab : "agents";
  const [queries, setQueries] = useState<Record<MarketTab, string>>({
    agents: "",
    skills: "",
    connectors: "",
  });
  const [debouncedQueries, setDebouncedQueries] = useState<Record<MarketTab, string>>({
    agents: "",
    skills: "",
    connectors: "",
  });
  const query = queries[tab];
  const debouncedQuery = debouncedQueries[tab];
  const setQuery = (value: string) => {
    setQueries((prev) => ({ ...prev, [tab]: value }));
  };
  const from = searchParams.get("from");
  const backTarget =
    from === "skills"
      ? "/skills"
      : from === "agents"
        ? "/agents"
        : from === "connectors"
          ? "/connectors"
          : null;
  const backLabel =
    from === "skills"
      ? tr("marketplace.backToSkills")
      : from === "agents"
        ? tr("marketplace.backToAgents")
        : from === "connectors"
          ? tr("marketplace.backToConnectors")
          : null;
  const setTab = (next: MarketTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  // Each catalog owns its query so a Skill keyword never filters Agent Teams.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQueries((prev) => ({ ...prev, [tab]: query.trim() }));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, tab]);

  const [dialogItem, setDialogItem] = useState<MarketplaceItem | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [connectorItem, setConnectorItem] = useState<MarketplaceItem | null>(null);
  const [connectorOpen, setConnectorOpen] = useState(false);
  const openItem = (item: MarketplaceItem) => {
    if (item.type === "connector") {
      setConnectorItem(item);
      setConnectorOpen(true);
      return;
    }
    setDialogItem(item);
    setDialogOpen(true);
  };

  useEffect(() => {
    setHideHeader(true);
    setHeader(null);
    setRightPanel(null);
    setAsideClassName(undefined);
    setMainClassName("flex-1 w-auto bg-card");
    setContentInnerClassName("p-0");
    return () => {
      setHideHeader(false);
      setHeader(null);
      setRightPanel(null);
      setAsideClassName(undefined);
      setMainClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [
    setAsideClassName,
    setContentInnerClassName,
    setHeader,
    setHideHeader,
    setMainClassName,
    setRightPanel,
  ]);

  // Flip the card state in place after an install (no full reload).
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set());
  const markInstalled = (item: MarketplaceItem) =>
    setInstalledIds((prev) => new Set(prev).add(item.id));
  const withInstalled = useCallback(
    (items: MarketplaceItem[]) =>
      items.map((i) => (installedIds.has(i.id) ? { ...i, installed: true } : i)),
    [installedIds],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* header */}
      <div className="border-b border-surface-border px-6 pt-5">
        {backTarget && backLabel ? (
          <BackLink
            onClick={() => navigate(backTarget)}
            label={backLabel}
            className="mb-3"
          />
        ) : null}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Store className="h-[18px] w-[18px] text-brand" />
              <span className="text-[17px] font-medium tracking-tight text-ink-heading">
                {tr("marketplace.title")}
              </span>
            </div>
            <div className="mt-1 text-[12.5px] text-ink-body">{tr("marketplace.subtitle")}</div>
          </div>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder={
              tab === "agents"
                ? tr("marketplace.searchAgents")
                : tab === "skills"
                  ? tr("marketplace.searchSkills")
                  : tr("marketplace.searchConnectors")
            }
            className="w-[250px]"
          />
        </div>
        {/* tabs */}
        <div className="mt-3 flex gap-5">
          {(["agents", "skills", "connectors"] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cn(
                "relative px-1 py-2 text-sm",
                tab === key ? "font-semibold text-ink-heading" : "text-ink-body",
              )}
            >
              {key === "agents"
                ? tr("marketplace.tabAgents")
                : key === "skills"
                  ? tr("marketplace.tabSkills")
                  : tr("marketplace.tabConnectors")}
              <span
                className={cn(
                  "absolute inset-x-0 -bottom-px h-0.5 rounded-full",
                  tab === key ? "bg-brand" : "bg-transparent",
                )}
              />
            </button>
          ))}
        </div>
      </div>

      {tab === "agents" ? (
        <AgentsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      ) : tab === "skills" ? (
        <SkillsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      ) : (
        <ConnectorsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      )}

      <MarketplaceImportDialog
        item={dialogItem}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onInstalled={markInstalled}
      />
      <MarketplaceConnectorDialog
        item={connectorItem}
        open={connectorOpen}
        onOpenChange={setConnectorOpen}
        onConnected={markInstalled}
      />
    </div>
  );
}

type Tr = (key: string, params?: Record<string, string | number>) => string;

interface TabProps {
  q: string;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
  withInstalled: (items: MarketplaceItem[]) => MarketplaceItem[];
}

/* ── shared bits ─────────────────────────────────────────────── */

function CategoryChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-xs",
        active
          ? "border-brand bg-brand font-medium text-white"
          : "border-surface-border bg-surface text-ink-body hover:bg-surface-soft",
      )}
    >
      {label}
    </button>
  );
}

function DegradedNotice({ tr }: { tr: Tr }) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 py-2 text-xs text-ink-body">
      <CloudOff className="h-3.5 w-3.5 flex-none text-ink-meta" />
      {tr("marketplace.degradedNotice")}
    </div>
  );
}

function ItemIcon({ item, size }: { item: MarketplaceItem; size: "sm" | "md" }) {
  const isImage = !!item.icon && /^https?:\/\//.test(item.icon);
  const Icon = marketplaceIcon(item.icon);
  const tint = tintFor(item.id);
  const cls = size === "md" ? "h-[38px] w-[38px] rounded-[9px]" : "h-9 w-9 rounded-[9px]";
  return (
    <div
      className={cn("flex flex-none items-center justify-center overflow-hidden", cls)}
      style={isImage ? undefined : { background: tint.bg, color: tint.fg }}
    >
      {isImage ? (
        <img src={item.icon ?? undefined} alt="" className="h-full w-full object-cover" />
      ) : (
        <Icon className={size === "md" ? "h-[19px] w-[19px]" : "h-[18px] w-[18px]"} />
      )}
    </div>
  );
}

/* ── Agents tab ──────────────────────────────────────────────── */

function AgentsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  const [teams, setTeams] = useState<MarketplaceItem[]>([]);
  const [categories, setCategories] = useState<MarketplaceCategory[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    marketplaceApi
      .categories("agent")
      .then((res) => setCategories(res.categories))
      .catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    marketplaceApi
      .list({
        type: "agent_team_template",
        category: category ?? undefined,
        q: q || undefined,
      })
      .then((teamRes) => {
        if (cancelled) return;
        setTeams(teamRes.items);
      })
      .catch(() => {
        if (cancelled) return;
        setTeams([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [q, category]);

  const teamItems = withInstalled(teams);
  const hasResults = teamItems.length > 0;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-7 pt-5">
      {/* filters */}
      <div className="mb-5 flex flex-wrap items-center gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          <CategoryChip
            label={tr("marketplace.filterAll")}
            active={category === null}
            onClick={() => setCategory(null)}
          />
          {categories.map((c) => (
            <CategoryChip
              key={c.key}
              label={c.label}
              active={category === c.key}
              onClick={() => setCategory(c.key)}
            />
          ))}
        </div>
      </div>

      {!hasResults && !loading && <EmptyState title={tr("marketplace.empty")} />}

      {/* teams grid */}
      {teamItems.length > 0 && (
        <section className="mb-7">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="h-[15px] w-[15px] text-brand" />
              <span className="text-sm font-semibold tracking-tight text-ink-heading">
                {tr("marketplace.teamsTitle")}
              </span>
              <span className="text-[11px] text-ink-meta">{tr("marketplace.teamsSubtitle")}</span>
            </div>
            <span className="text-xs tabular-nums text-ink-muted">
              {tr("marketplace.teamsCount", { count: teamItems.length })}
            </span>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
            {teamItems.map((team) => (
              <TeamCard key={team.id} team={team} tr={tr} onOpen={onOpen} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function TeamCard({
  team,
  tr,
  onOpen,
}: {
  team: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  const members = team.members ?? [];
  return (
    <button
      type="button"
      onClick={() => onOpen(team)}
      className="flex min-h-[154px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2.5 flex items-center gap-2.5">
        <ItemIcon item={team} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-semibold tracking-tight text-ink-heading">
            {team.title}
          </div>
        </div>
        {team.installed && (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-[10px] font-medium text-ink-meta">
            {tr("marketplace.installed")}
          </span>
        )}
      </div>
      <div className="mb-3 line-clamp-2 min-h-[37px] text-xs leading-relaxed text-ink-body">
        {team.description}
      </div>
      <div className="mt-auto flex items-center justify-between">
        <div className="flex items-center">
          {members.slice(0, 4).map((m) => {
            const tint = tintFor(m.name);
            return (
              <div
                key={m.name}
                className="-ml-1.5 flex h-6 w-6 items-center justify-center rounded-full border-2 border-surface text-[10px] font-semibold first:ml-0"
                style={{ background: tint.bg, color: tint.fg }}
              >
                {m.name.slice(0, 1)}
              </div>
            );
          })}
          <span className="ml-2 text-[11.5px] text-ink-body">
            {tr("marketplace.membersAndSkills", {
              members: members.length,
              skills: team.skill_count ?? 0,
            })}
          </span>
        </div>
        <ArrowRight className="h-4 w-4 text-ink-muted" />
      </div>
    </button>
  );
}

/* ── Skills tab ──────────────────────────────────────────────── */

function SkillsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  const [categories, setCategories] = useState<MarketplaceCategory[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestSeq = useRef(0);

  useEffect(() => {
    marketplaceApi
      .categories("skill")
      .then((res) => {
        setCategories(res.categories);
        if (res.degraded) setDegraded(true);
      })
      .catch(() => setCategories([]));
  }, []);

  const load = useCallback(
    (nextPage: number, append: boolean) => {
      const seq = (requestSeq.current += 1);
      setLoading(true);
      marketplaceApi
        .list({
          type: "skill",
          category: category ?? undefined,
          q: q || undefined,
          page: nextPage,
          page_size: SKILL_PAGE_SIZE,
        })
        .then((res) => {
          if (seq !== requestSeq.current) return;
          setItems((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          setDegraded(res.degraded);
          setPage(nextPage);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          if (!append) {
            setItems([]);
            setTotal(0);
          }
        })
        .finally(() => {
          if (seq === requestSeq.current) setLoading(false);
        });
    },
    [category, q],
  );

  useEffect(() => {
    load(1, false);
  }, [load]);

  const visible = withInstalled(items);
  const hasMore = !degraded && items.length < total && items.length >= SKILL_PAGE_SIZE;

  return (
    <div className="flex min-h-0 flex-1">
      {/* category rail */}
      <div className="w-[190px] flex-none overflow-y-auto border-r border-surface-border px-2.5 py-4">
        <div className="px-2 pb-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-meta">
          {tr("marketplace.categories")}
        </div>
        <RailItem
          label={tr("marketplace.filterAll")}
          count={null}
          active={category === null}
          onClick={() => setCategory(null)}
        />
        {categories.map((c) => (
          <RailItem
            key={c.key}
            label={c.label}
            count={c.count ?? null}
            active={category === c.key}
            onClick={() => setCategory(c.key)}
          />
        ))}
      </div>

      {/* content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {degraded && <DegradedNotice tr={tr} />}
        <div className="mb-3 text-xs tabular-nums text-ink-muted">
          {q
            ? tr("marketplace.skillsTotal", { count: total })
            : tr("marketplace.curatedShelf", { count: total })}
        </div>
        {visible.length === 0 && !loading ? (
          <EmptyState title={tr("marketplace.empty")} />
        ) : (
          <>
            <div className="flex flex-wrap gap-3">
              {visible.map((skill) => (
                <SkillMarketCard key={skill.id} skill={skill} tr={tr} onOpen={onOpen} />
              ))}
            </div>
            {hasMore && (
              <div className="mt-5 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={loading}
                  onClick={() => load(page + 1, true)}
                >
                  {loading ? tr("marketplace.loading") : tr("marketplace.loadMore")}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Connectors tab ──────────────────────────────────────────── */

function ConnectorsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  const [categories, setCategories] = useState<MarketplaceCategory[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestSeq = useRef(0);

  useEffect(() => {
    marketplaceApi
      .categories("connector")
      .then((res) => setCategories(res.categories))
      .catch(() => setCategories([]));
  }, []);

  const load = useCallback(
    (nextPage: number, append: boolean) => {
      const seq = (requestSeq.current += 1);
      setLoading(true);
      marketplaceApi
        .list({
          type: "connector",
          source: "modelscope",
          category: category ?? undefined,
          q: q || undefined,
          page: nextPage,
          page_size: CONNECTOR_PAGE_SIZE,
        })
        .then((res) => {
          if (seq !== requestSeq.current) return;
          setItems((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          setPage(nextPage);
          setDegraded(res.degraded);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          if (!append) {
            setItems([]);
            setTotal(0);
          }
          setDegraded(true);
        })
        .finally(() => {
          if (seq === requestSeq.current) setLoading(false);
        });
    },
    [category, q],
  );

  useEffect(() => {
    load(1, false);
  }, [load]);

  const visible = withInstalled(items);
  const hasMore =
    !degraded && items.length < total && page * CONNECTOR_PAGE_SIZE < 100;
  return (
    <div className="flex min-h-0 flex-1">
      <div className="w-[190px] flex-none overflow-y-auto border-r border-surface-border px-2.5 py-4">
        <div className="px-2 pb-1.5 font-mono text-[11px] uppercase tracking-wider text-ink-meta">
          {tr("marketplace.categories")}
        </div>
        <RailItem
          label={tr("marketplace.filterAll")}
          count={null}
          active={category === null}
          onClick={() => setCategory(null)}
        />
        {categories.map((entry) => (
          <RailItem
            key={entry.key}
            label={entry.label}
            count={null}
            active={category === entry.key}
            onClick={() => setCategory(entry.key)}
          />
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {degraded && <DegradedNotice tr={tr} />}
        <div className="mb-3 flex items-center gap-2">
          <Plug className="h-3.5 w-3.5 text-brand" />
          <span className="text-xs text-ink-body">
            {q
              ? tr("marketplace.connectorSearchResults", { count: visible.length })
              : tr("marketplace.connectorPopular", { count: visible.length })}
          </span>
        </div>
        {visible.length === 0 && !loading ? (
          <EmptyState title={tr("marketplace.empty")} />
        ) : (
          <>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
              {visible.map((connector) => (
                <ConnectorMarketCard
                  key={connector.id}
                  connector={connector}
                  tr={tr}
                  onOpen={onOpen}
                />
              ))}
            </div>
            {hasMore ? (
              <div className="mt-5 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={loading}
                  onClick={() => load(page + 1, true)}
                >
                  {loading ? tr("marketplace.loading") : tr("marketplace.loadMore")}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function ConnectorMarketCard({
  connector,
  tr,
  onOpen,
}: {
  connector: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(connector)}
      className="flex min-h-[150px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2.5 flex items-start gap-2.5">
        <ItemIcon item={connector} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-semibold tracking-tight text-ink-heading">
            {connector.title}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <MarketplaceSourcePill source={connector.source} />
            {connector.category_label ? (
              <span className="truncate text-[10.5px] text-ink-meta">
                {connector.category_label}
              </span>
            ) : null}
          </div>
        </div>
        {connector.installed ? (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-[10px] font-medium text-ink-meta">
            {tr("marketplace.connected")}
          </span>
        ) : null}
      </div>
      <div className="line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {connector.description || tr("marketplace.connectorNoDescription")}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <div className="flex items-center gap-3 text-[11px] tabular-nums text-ink-body">
          {connector.stats.views != null ? (
            <span className="inline-flex items-center gap-1">
              <Eye className="h-3 w-3" />
              {formatCount(connector.stats.views)}
            </span>
          ) : null}
          {connector.stats.stars != null && connector.stats.stars > 0 ? (
            <span className="inline-flex items-center gap-1">
              <Star className="h-3 w-3" />
              {formatCount(connector.stats.stars)}
            </span>
          ) : null}
        </div>
        <span className="text-xs font-medium text-brand">
          {connector.installed
            ? tr("marketplace.connected")
            : tr("marketplace.viewConnector")}
        </span>
      </div>
    </button>
  );
}

function RailItem({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number | null;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left",
        active
          ? "bg-brand-light font-semibold text-brand-700"
          : "text-ink-heading hover:bg-surface-soft",
      )}
    >
      <span className="truncate text-[12.5px]">{label}</span>
      {count != null && (
        <span className="ml-2 flex-none text-[11px] tabular-nums text-ink-muted">
          {formatCount(count)}
        </span>
      )}
    </button>
  );
}

function SkillMarketCard({
  skill,
  tr,
  onOpen,
}: {
  skill: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  const setupBadges = skill.badges.filter((badge) =>
    ["requires_api_key", "third_party_cost", "locked"].includes(badge),
  );
  return (
    <button
      type="button"
      onClick={() => onOpen(skill)}
      className="flex w-[278px] flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={skill} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold tracking-tight text-ink-heading">
            {skill.title}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <MarketplaceSourcePill source={skill.source} />
            {setupBadges.map((badge) => (
              <MarketplaceBadgePill key={badge} badge={badge} />
            ))}
          </div>
        </div>
      </div>
      <div className="mb-2.5 line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {skill.description}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <div className="flex items-center gap-2.5 text-[11px] tabular-nums text-ink-body">
          {skill.stats.downloads != null && (
            <span className="inline-flex items-center gap-1">
              <Download className="h-[11px] w-[11px]" />
              {formatCount(skill.stats.downloads)}
            </span>
          )}
          {skill.stats.stars != null && (
            <span className="inline-flex items-center gap-1">
              <Star className="h-[11px] w-[11px]" />
              {formatCount(skill.stats.stars)}
            </span>
          )}
          {skill.version && <span className="font-mono text-[10.5px]">{skill.version}</span>}
        </div>
        <span className="text-xs font-medium text-brand">
          {skill.installed ? tr("marketplace.installed") : tr("marketplace.import")}
        </span>
      </div>
    </button>
  );
}
