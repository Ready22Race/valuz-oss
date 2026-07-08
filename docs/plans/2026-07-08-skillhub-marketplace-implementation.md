# SkillHub Marketplace — Implementation Plan

> Date: 2026-07-08
> Worktree: `/Users/zhourongyu/Dev/valuz-oss-skillhub-marketplace`
> Branch: `codex/skillhub-marketplace`
> PRD: `docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md`
> Curation: `docs/plans/2026-07-07-skillhub-skillset-curation.md`
> Design: claude.ai/design project `6b02a1be-…` file `Marketplace.dc.html`

## Scope

One PR delivering PRD MVP 1–5 as a working full-stack feature:

- Backend `marketplace` module normalizing SkillHub skills and built-in Agent
  Team packs behind `/v1/marketplace/*`. Curated single-agent templates can
  remain as an internal API capability, but are hidden from the current
  marketplace browse UI.
- Frontend full-screen Marketplace page (Agents / Skills tabs) per the design,
  with the shared import-preview modal and toasts.
- Entry points: sidebar nav item + "Import from Marketplace" actions on the
  Agents & Skills library pages.

Out of scope (later): MVP 6 curation pipeline, paid metadata, MCP marketplace,
and public single-Agent browsing.

### Agent Team First Implementation Batch

Agents / Agent Teams are Valuz official curated templates. SkillHub expert
packs are internal source material only; the customer-facing market should not
show "SkillHub curated" as an Agent source.

Agent Team marketplace architecture:

| Category | Pack ids |
|---|---|
| 产品设计 | `product-strategy`, `design-prototype` |
| 技术工程 | `development-engineering`, `qa-testing` |
| 金融投资 | `investment`, `supply-chain-tracking` |
| 营销增长 | `competitive-intelligence`, `content-growth`, `campaign-event` |
| 内容创作 | `content`, `short-video-growth` |
| 法务安全 | `contract-review`, `compliance-review` |
| 教育学术 | `academic-research`, `training-program` |
| 运营人力 | `recruiting-evaluation` |
| 特色分类 | `health-report`, `chinese-metaphysics`, `tarot-astrology` |

The legacy broad `product`, `statistical-analysis`, `teaching-material`,
`video-production`, and `risk-control` packs can remain on disk for
compatibility/curation experiments, but they are not shown in the current
marketplace Team list unless the owner explicitly adds them back. `统计分析`
should return only with a future data-intelligence shelf; `教学材料` should wait
until teacher-oriented workflows become a product target.

Visible marketplace Team list:

1. `product-strategy`
2. `design-prototype`
3. `development-engineering`
4. `qa-testing`
5. `investment`
6. `supply-chain-tracking`
7. `competitive-intelligence`
8. `content-growth`
9. `campaign-event`
10. `content`
11. `short-video-growth`
12. `contract-review`
13. `compliance-review`
14. `academic-research`
15. `training-program`
16. `recruiting-evaluation`
17. `chinese-metaphysics`
18. `health-report`
19. `tarot-astrology`

Other category Teams can exist as manifests/candidates but should not be added
to `BUILTIN_PACK_IDS` until they are ready to show in the marketplace.

Equipment rule: every Marketplace Agent Team must ship with real bound skills,
and every role in the Team should reference at least one skill. Only
`investment` and `content` use Valuz bundled skills in the first wave. The other
Team packs are Valuz-curated conversions of SkillHub expert packs: their
manifest skills must come from the source expert pack `skillSlugs` with
`source: "skillhub"`, and Marketplace install must download those SkillHub
skills before creating the Team Agents. SkillHub remains internal provenance,
not a customer-facing Agent source label.

## Verified facts (2026-07-08)

- SkillHub API live: `GET api.skillhub.cn/api/v1/categories` (12 cats),
  `GET /api/skills?page=&pageSize=` (75k skills, fields match PRD),
  `GET /api/v1/skills/{slug}` (detail + `securityReports`),
  `GET /api/v1/skills/{slug}/evaluation` (TRACE quality report: Trust,
  Reliability, Adaptability, Convention, Effectiveness),
  `GET /api/v1/skills/{slug}/files` (path/sha256/size),
  `GET /api/v1/download?slug=` → **302 → COS zip**, SKILL.md at archive root.
  The API URL itself has no `.zip` suffix, so the URL-import pipeline must
  detect archives from the downloaded bytes, not from the original URL suffix.
- `/api/skills` response envelope: `{code, data: {skills, total}, message}`;
  categories endpoint returns `{count, items}` (no envelope).
- List query params verified: `category=` (server-side), `keyword=` (search,
  server-side), `source=` (server-side; real values `clawhub` 61,984 /
  `community` 13,220 / `enterprise` few — there is NO "skillhub" value).
  `subCategories=` / `q=` / `search=` / `verified=` are NOT supported
  upstream. Default sort is by `score` (curated-first).

### Product decision (owner, 2026-07-08) — updated ×2

**Browse = SkillHub's official curated shelf.** SkillHub's own 推荐精选 is
`GET /api/v1/showcase/recommended` (~100 skills, same item shape as the list
API; discovered by sniffing skillhub.cn's XHR — no documented sort param
reaches it). The Skills tab browse surface serves exactly this shelf, paged
in memory; the category rail derives from the shelf's own contents (with its
real small counts, allowlist order first, curator extras like design-media
after). The full 75k catalog is reachable ONLY through keyword search, which
stays scoped to the category allowlist when no category is selected.

### Product decision (owner, 2026-07-08) — updated

**Official skills are NOT market items.** They ship with the client (bundled)
or install automatically alongside official Agent Teams; the Skills tab is
SkillHub-only, with no "Valuz Official" source filter. (This supersedes the
PRD's §Official Content Strategy row that placed official skills in the
Skills tab.)

The catalog must NOT expose all 75k skills. Only a curated category
allowlist is browsable (one backend constant, easy to adjust):
`office-efficiency, content-creation, dev-programming, data-analysis,
ai-agent, knowledge-management, business-ops, professional` (excludes
design-media, education, it-ops-security, life-service). Within a category,
upstream score order = the curated ranking; page_size 30. Search uses
`keyword=` constrained to allowlisted categories (client-side check on the
result's category). Subcategory chips derive from the current page and
filter client-side (upstream cannot filter by subcategory). Trust badges
come from `verified` + security-report status; `community` source gets the
community badge.
- Skill list item fields used: `slug name description description_zh iconUrl
  category subCategories[{key,name}] downloads stars installs version source
  verified ownerName labels.requires_api_key updated_at`.

## Backend

### New module `backend/valuz_agent/modules/marketplace/`

- `skillhub.py` — `SkillHubClient` (httpx.AsyncClient, 15s timeout,
  base `https://api.skillhub.cn`). Methods: `categories()`, `skills(page,
  page_size, category, q)`, `skill_detail(slug)`, `skill_evaluation(slug)`,
  `skill_files(slug)`,
  `download_url(slug)` (returns the API URL; download itself happens through
  the existing skills URL-import pipeline). In-memory TTL cache: categories
  10 min, list/detail 60 s. Errors → `MarketplaceUpstreamError` → HTTP 502
  with i18n key; the Skills tab must degrade gracefully (official-only).
- `models.py` — Pydantic DTOs mirroring the OpenAPI contract (below).
- `templates.py` — loads `resources/marketplace/agent_templates.json`
  (internal Valuz single-agent templates; not shown in the current marketplace
  UI). Fields per template: `id slug name{zh,en} role{zh,en} instructions{zh,en}
  icon tint category runtime effort source(valuz_official) skills[display names
  or slugs] connectors[{name, requirement}]`.
- `service.py` — `MarketplaceService`:
  - `list_categories()` → skill categories (SkillHub, cached, fallback to
    empty/degraded) + agent categories from Team packs only.
  - `list_items(type, category, subcategory, source, q, page, page_size)`:
    - `skill`: SkillHub page-through + official skills from
      `valuz_skill_index` (`scope='official'`, via `SkillDatastore`), merged
      as normalized items. `installed` flag = slug present in user index.
    - `agent_template`: internal compatibility path; not requested by the
      current UI.
    - `agent_team_template`: `AgentPackService.list_packs` (3 built-in),
      `installed` = pack `added`.
  - `get_item(id)` → detail payload per type (skill: + files + security +
    optional TRACE evaluation; team: members; agent:
    instructions/skills/connectors).
  - `install(id, options)`:
    - skillhub skill → `SkillLibraryService.import_url_preview(download_url)`
      then `confirm_url_import` (auto-pick `suggested_name` on conflict).
    - official skill → enable in library (`library_enabled`), 409 if locked
      (Reportify required).
    - agent template → `AgentService.create_agent` with defaults from
      `_resolve_deploy_target` + `get_default_effort`; bind template skills
      that already exist in the user's index; idempotent by slug.
    - team → `AgentPackService.import_pack`.
- Item id format: `{source}:{type}:{ref}` e.g. `skillhub:skill:agent-memory`,
  `valuz:agent:report-writer`, `valuz:team:investment`.

### Route `backend/valuz_agent/api/routes/marketplace.py`

`router = APIRouter(tags=["marketplace"])`; endpoints:

- `GET /v1/marketplace/categories`
- `GET /v1/marketplace/items` (query: `type` required, `category`,
  `subcategory`, `source`, `q`, `page`, `page_size`)
- `GET /v1/marketplace/items/{item_id}`
- `POST /v1/marketplace/items/{item_id}:install` (body: `{deploy_project: bool
  | null}` reserved; v1 installs to library only)

Register in `api/app.py` (import + `api.include_router`). Deps:
`get_current_user_id`, `get_async_session`, local `_get_marketplace_service`
factory (pattern from `agent_templates.py:42`).

### Contract `api/openapi.yaml`

Add paths above with `operationId`s (`listMarketplaceCategories`,
`listMarketplaceItems`, `getMarketplaceItem`, `installMarketplaceItem`) and
components: `MarketplaceItem`, `MarketplaceItemDetail`,
`MarketplaceCategory`, `MarketplaceInstallResult`. Normalized item shape per
PRD §Backend Requirements: `id type source source_ref title description icon
category subcategories badges[] stats{downloads,stars,installs} version
install_target installed`.

Badges (PRD §Safety): `free_install requires_api_key third_party_cost
reviewed_skillhub reviewed_valuz community verified`.

### Backend tests

`backend/tests/` new `test_marketplace_*.py`: service normalization with a
faked SkillHubClient (no network), install orchestration (skill via staged
pipeline mock, agent idempotency, team pack), route smoke via app test
client. Follow existing test patterns in `backend/tests`.

## Frontend

All in shared packages (desktop + webui both get it):

- `frontend/packages/core/src/api/marketplace-api.ts` — hand-written types
  mirroring contract + `marketplaceApi` object (pattern: `skills-api.ts`,
  `createFetchJson`). Export from `core/src/index.ts`.
- Route: `frontend/packages/core/src/edition/registries/desktop-routes.ts`
  add `{ id: "marketplace", path: "/marketplace", layout: "project",
  showInNav: true }`; nav item in `personal-profile.ts` (`navGroup:
  "library"`, label `nav.marketplace`); icon in `ProjectLayoutBase.tsx`
  `NAV_ICON_MAP`; component map in `route-registry.ts`; page exported from
  `pages/index.ts`.
- `frontend/packages/app/src/pages/MarketplacePage.tsx`:
  - Header: title/subtitle + `SearchInput`, two underline tabs (Agents /
    Skills) per design (simple buttons w/ brand underline; `Tabs` primitive
    if it fits).
  - Agents tab: teams strip (horizontal scroll cards w/ member avatars) →
    category pill row (`FilterPillBar` or design-style chips) + source
    segmented (`SegmentedControl`) → agent card grid.
  - Skills tab: left category rail (w/ counts) + subcategory chips + source
    segmented + result count + skill card grid; server-driven search w/
    debounce; pagination = "load more".
  - Empty/degraded states (`EmptyState`): SkillHub unreachable → official
    skills only + notice.
- `frontend/packages/app/src/components/MarketplaceImportDialog.tsx` — one
  dialog, three bodies per design: skill (meta, security report, file list),
  agent (role/instructions, bound skills, connector requirements), team
  (members w/ Lead/Member tags, deploy toggle — v1 keep toggle only if
  install API supports it, else omit). Footer: install target note + cancel /
  install. Success → toast (existing toast util) + `installed` state.
- Library entry points: SkillsPage Add menu + AgentsPage header gain
  "Import from Marketplace" → navigate `/marketplace` (tab preselected via
  `?tab=`).
- i18n: `marketplace.*` namespace + `nav.marketplace` in BOTH
  `i18n/locales/zh-CN.json` and `en-US.json`; regenerate types
  (`cd backend && uv run python ../i18n/scripts/gen_types.py`). Follow
  frontend/CLAUDE.md i18n rules (t() casts, JSX wrapping).

Design tokens: use existing `--brand/--surface/--surface-soft/text-ink-*`
Tailwind classes; do NOT copy raw hexes from the prototype except badge tint
accents where no token exists.

## Order of work

1. Contract (`api/openapi.yaml`) — marketplace paths + schemas.
2. Backend: skillhub client → templates resource → service → route → tests.
3. i18n keys + gen_types.
4. Frontend: api client → page (tabs) → dialog → nav/route wiring → library
   entry points.
5. Verify: `make test-all`, `make typecheck`, `make lint`; browser-verify the
   page against the design (dev run), including SkillHub-offline degrade.

## Risks / notes

- SkillHub list is 75k items — never fetch unbounded; always paged
  (page_size 30) and category/sub filters push down to the upstream API.
- `subCategories` come per-skill; the category rail counts come from the
  categories endpoint; subcategory chips derive from upstream (check if a
  subcategory list API exists; else derive from current page or the v1
  category payload).
- Official skills may be locked (Reportify) — surface as badge, install
  returns 409 with i18n message.
- Existing URL import pipeline uses urllib with 30s timeout — fine for the
  302→COS zips (~KBs–MBs).
