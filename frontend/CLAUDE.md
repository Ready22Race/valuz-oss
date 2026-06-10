# Frontend — Valuz OSS

> pnpm + Turborepo monorepo. Electron desktop + web SPA + CLI scaffold on a shared core / ui / shared package base.

## Layout

```
apps/
  desktop/   # Electron shell (main/preload + React SPA)
  webui/     # Lightweight browser SPA (HttpTransport)
  cli/       # TS runtime scaffold
packages/
  shared/    # Types, constants, pure utils — zero runtime deps
  ui/        # shadcn-ish components, AppShell, tailwind preset
  core/      # IPC transport, Zustand stores, hooks, edition profile
```

Package dependency rules:

- `shared` depends on nothing internal.
- `ui` depends on `shared` only.
- `core` depends on `shared` only.
- `apps/*` may depend on any package. Apps must not depend on each other.

Package names use the `@valuz/` scope. Don't introduce `apps/desktop-enterprise` or duplicate apps — enterprise is an overlay, not a fork.

## Commands

```bash
pnpm dev                        # webui dev server
pnpm --filter @valuz/desktop dev        # full Electron desktop shell
pnpm typecheck                  # all packages
pnpm test                       # vitest across the workspace
pnpm build                      # turbo build
```

EDITION selector: `EDITION=personal` (default) or `EDITION=enterprise`. Drives Vite `__EDITION__` define. Enterprise-native services are not implemented in the current Electron shell.

## Edition architecture — one trunk, overlay everything else

The whole repo is organized around this principle:

> **Personal = Base. Enterprise = Personal + overlay (feature flags + registries + native services).**

Everything edition-specific flows through **`packages/core/src/edition/`**:

- `profile.ts` — `EditionProfile`, `FeatureFlags`, `ServiceDescriptor`, `DesktopRouteModule`, `SettingsSectionModule`, `ProjectPanelModule` types.
- `personal-profile.ts` — personal baseline.
- `registries/{desktop-routes,settings-sections,service-panels}.ts` — per-edition module lists.
- `resolve.ts` — `resolveEdition()` / `getActiveProfile()` (build-time).
- `registry-store.ts` — **runtime** mutable store (Zustand) seeded from the active profile.
- `plugin.ts` — `PluginManifest` + `registerPlugin()` + `loadPluginFromUrl()`.

### Adding a route / settings section / project panel

Edit **registries only**. The app shell discovers entries through the store.

1. Append a `DesktopRouteModule` to `registries/desktop-routes.ts`.
2. Add an `id → Component` entry in `apps/desktop/src/routes/route-registry.ts` (app-local map; registries can't import app components).
3. Done. Router, sidebar nav, and settings page pick it up.

Same pattern for `settings-sections.ts` and `service-panels.ts`.

For **settings sections**, `SettingsSectionModule` supports two optional fields:
- `icon?: string` — Lucide icon name (e.g. `"radio"`, `"cpu"`). Mapped to a component in `SettingsPage`'s `TAB_ICON_MAP`. Falls back to a gear icon.
- `component?: ComponentType` — React component for the section's content. If provided, it renders instead of any built-in tab with the same id. Overlay editions use this to inject their own settings UI.

Built-in tabs (model, general, parsing, system-logs, about) live in `pages/settings/` as standalone components. The SettingsPage shell reads `settingsSections` from the registry and dispatches to either the overlay `component` or the built-in `SECTION_MAP`.

### Adding enterprise capability

- Append to `enterpriseDesktopRoutes` / `enterpriseSettingsSections` / `enterpriseProjectPanels` / `enterpriseServiceOverlay`.
- Concrete module code goes under `packages/core/src/enterprise/{team,sso,audit}/`.
- Native sidecars: add a `ServiceDescriptor` in `apps/desktop/src/main/services/descriptors.ts` and register Electron IPC/runtime support under `apps/desktop/src/main/`.

Never add `if (enterprise) { ... }` in page code, router, or layout. Those files must render from the profile/registry only.

### Runtime plugin extension

The registry store is mutable, so plugins can contribute routes/sections/panels/services **without a rebuild**.

```ts
import { registerPlugin } from '@valuz/core'

await registerPlugin({
  id: 'my-plugin',
  version: '0.0.1',
  routes: [{ id: 'foo', path: '/foo', label: 'Foo', description: '…', layout: 'project', showInNav: true, edition: 'personal' }],
  settingsSections: [...],
  services: [...],
})
```

`loadPluginFromUrl(url)` is the ESM-import entry point. Security (signing, origin allowlist) is **not** provided here — it belongs at the delivery layer, not the loader. Plugins run in the host React root; trust is full.

### Edition hot-swap

`useRegistryStore.getState().setEdition('enterprise')` swaps the live profile. The router, nav, and settings re-render. Useful for demos and E2E tests; production builds normally fix edition at build time.

## Desktop service descriptors

`apps/desktop/src/main/services/descriptors.ts` owns the mutable `DescriptorRegistry`. Start/stop flows consume `descriptors.snapshot()` — the same path for personal and enterprise overlays.

IPC commands (beyond the basic service manager set):

- `list_service_descriptors` — returns `Vec<ServiceDescriptor>`.
- `register_service_descriptor { descriptor }` — upserts by name, emits `service-descriptors-changed`.
- `unregister_service_descriptor { name }` — removes by name, emits `service-descriptors-changed` if it existed.

Frontend consumer: `useServiceDescriptors()` in `@valuz/core` merges local (TS registry) + remote (Electron) descriptors and exposes `registerRemote` / `unregisterRemote`.

## i18n (Internationalization)

All user-facing strings must use `t()` calls — no hardcoded Chinese or English text in components.

### Architecture

- **Engine:** `packages/shared/src/i18n/index.ts` — module-level store, no React context. Uses `useSyncExternalStore` bridge.
- **Locale files:** `i18n/locales/{zh-CN,en-US}.json` — single source of truth for translations. Both are statically imported (bundled, no async loading).
- **Types:** Auto-generated `I18nKey` union at `packages/shared/src/types/i18n.ts` + `backend/valuz_agent/generated/i18n_keys.py`.
- **Initialization:** `initI18n()` called synchronously in `apps/desktop/src/renderer/main.tsx` before React renders (reads `localStorage("valuz-locale")`, prevents flash).

### Which hook to use

| Context | Import | Hook |
|---|---|---|
| `@valuz/ui` components | `import { useI18n } from "../../hooks/use-i18n"` | `const { t } = useI18n()` |
| `@valuz/core` hooks/utilities | `import { useTranslation } from "@valuz/core"` | `const { t } = useTranslation()` |
| Desktop pages/components | `import { useTranslation } from "@valuz/core"` | `const { t } = useTranslation()` |
| Non-React (main process, utilities) | `import { t } from "@valuz/shared/i18n"` | `t("key")` directly |

### Rules

1. **Every component that uses `t()` must call its own hook.** `t` is scoped to the component — do NOT rely on closure from a parent component. Nested functions (`memo`, arrow helpers) inside a component are fine.
2. **No `t()` in default parameter values.** Destructured defaults run before the hook call. Accept the raw prop and resolve after the hook:
   ```ts
   // BAD — t is not defined yet at default-value time
   ({ title = t("key") }: Props) => { const { t } = useI18n(); ... }
   // GOOD
   ({ title: titleProp }: Props) => { const { t } = useI18n(); const title = titleProp ?? t("key"); ... }
   ```
3. **Wrap `t()` in `{}` in JSX text.** The `<typeof` in `as Parameters<typeof t>[0]` is parsed as a JSX opening tag:
   ```tsx
   // BAD — parse error: Expected corresponding JSX closing tag for <typeof>
   <span>t("key" as Parameters<typeof t>[0])</span>
   // GOOD
   <span>{t("key" as Parameters<typeof t>[0])}</span>
   ```
4. **Use `${}` not `{}` in template literals.** `{t("...")}` inside backticks is literal text, not interpolation:
   ```ts
   // BAD — renders literal "{t("key")}"
   `Ready · ${count} {t("key" as Parameters<typeof t>[0])}`
   // GOOD
   `Ready · ${count} ${t("key" as Parameters<typeof t>[0])}`
   ```
5. **JSX attributes need `{}`:** `placeholder={t("key")}`, not `placeholder=t("key")`.
6. **Language selector items keep native script:** Use "中文" not `t()`, "English" not `t()`.

### Adding new keys

1. Add key+value to both `i18n/locales/zh-CN.json` and `i18n/locales/en-US.json`.
2. Regenerate types: `cd backend && uv run python ../i18n/scripts/gen_types.py`
3. Use the key in code with the type-safe cast: `t("namespace.key" as Parameters<typeof t>[0])`
4. Run `pnpm typecheck` to verify.

### Key namespaces

`common.*`, `time.*`, `sidebar.*`, `nav.*`, `conversation.*`, `skill.*`, `knowledge.*`, `project.*`, `cron.*`, `settings.*`, `system.*`, `oauth.*`, `onboarding.*`, `commandPalette.*`, `startup.*`, `tray.*`, `cliLogin.*`, `toolCall.*`, `ui.*`, `permission.*`, `offline.*`, `directoryPicker.*`

## What NOT to do

- Don't hardcode routes in `apps/desktop/src/routes/router.tsx` — it must build from `useRegistryStore`.
- Don't hardcode nav items in `DesktopProjectLayout` — it derives from `desktopRoutes` where `layout === 'project' && showInNav`.
- Don't hardcode settings tabs in `SettingsPage` — it renders sidebar from `settingsSections` registry and content from `SECTION_MAP` / overlay `component`.
- Don't create `apps/*-enterprise` directories.
- Don't branch on `edition` inside a page component. Fork at the registry layer instead.
- Don't make `packages/core/src/enterprise/*` import from page code. Those modules must stay tree-shakable from the personal build.
- Don't reintroduce `export interface FeatureFlags` in `packages/core/src/config/features.ts`. The canonical type lives in `edition/profile.ts`; `features.ts` is a thin facade.

## Transport + stores

- `createTransport()` returns `ElectronTransport` when `window.valuzDesktop` is present, else `HttpTransport` (or `MockTransport` in tests).
- State: Zustand only. Never Redux / MobX. Stores live in `packages/core/src/store/`.
- Don't call `fetch` directly from pages — go through `transport.invoke(command, args)` so webui/desktop share code.

## Testing

- Vitest. Tests live beside source (`foo.ts` ↔ `foo.test.ts`).
- Router tests use `createMemoryRouter(routes)` where `routes` is the static snapshot exported from `apps/desktop/src/routes/router.tsx`.
- `App.test.tsx` mocks `./routes/router` as `{ AppRouter: () => <div>Desktop app ready</div> }`. If you rename or replace `AppRouter`, update the mock.
- `registry-store.test.ts` demonstrates the plugin lifecycle — prefer extending this over ad-hoc mocks.

## Tailwind

Tailwind v4 with CSS `@theme`. Single source of truth: `packages/ui/src/styles/project.css` (re-exported by `@valuz/ui`). `packages/ui/tailwind.preset.ts` exports TS design tokens for non-CSS consumers (charts, inline styles) — it is **not** a Tailwind v3 preset.

Apps use `@tailwindcss/vite` + `tailwindcss()` plugin. No `tailwind.config.ts` needed.

## Reference architecture

The canonical design doc is `docs/desktop/FRONTEND-ARCH.md` in the sibling `reportify-prd` repo. Deviations here:

- Package scope is `@valuz/` (not `@reportify/`).
- App directory is `apps/desktop` (not `apps/tauri`).
- Tailwind v4 CSS-based config instead of v3 JS preset — `tailwind.preset.ts` keeps the token surface but is not consumed by Tailwind itself.
- Enterprise modules (PG / RustFS / Redis / rapiline / LibreOffice / ParadeDB) are declared as seams only; none are implemented.

## UI 组件规范

> `@valuz/ui` 组件库的强制规范。所有 token 定义在 `packages/ui/src/styles/project.css`。

### 边框圆角

| 元素 | Tailwind | Token |
|------|----------|-------|
| Button / Input / Textarea / Select / TabsTrigger / Checkbox | `rounded-md` | 6px |
| Dialog / Drawer / IconBox(md) / 代码块 | `rounded-lg` | 8px |
| Card / DropdownMenu / Popover / EmptyState / IconBox(lg) | `rounded-xl` | 10px |
| SectionCard / ActionCardGrid 图标 | `rounded-2xl` | 12px |
| Badge / Switch / Avatar / StatusPill | `rounded-full` | — |

规则：不使用任意值（如 `rounded-[7px]`）。无匹配场景时选相邻较小值。

### 语义颜色

**文字：** `text-ink-heading`（标题）/ `text-ink-label`（表单 label、按钮）/ `text-ink-body`（正文、描述）/ `text-ink-meta`（时间戳、元数据）/ `text-ink-muted`（占位图标）/ `text-ink-disabled`（禁用态）

**表面/背景：** `bg-surface`（卡片）/ `bg-surface-soft`（hover 背景）/ `bg-surface-2`（次级背景）/ `bg-surface-muted`（分割线、SegmentedControl）

**边框：** `border-surface-border`（默认）/ `border-surface-border-strong`（强边框）/ `border-surface-border-hover`（hover 态）

**状态色：** 成对使用 `bg-success-light` + `text-success-text`；warning / error / info 同理。

**品牌：** `bg-brand` / `text-brand`（CTA、active 态）/ `bg-brand-light`（品牌浅色背景）/ `text-brand-secondary`

规则：禁止硬编码十六进制颜色。所有颜色通过语义 token 引用。

### 交互状态

- **Focus：** `focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50`。使用 `focus-visible` 而非 `focus`。
- **Hover：** Button default `hover:bg-primary/90`；outline `hover:bg-surface-2`；ghost `hover:bg-accent`；Card 可交互用 `card-interactive` utility class。
- **Active (Radix)：** 使用 `data-[state=active]` 选择器。
- **Disabled：** `disabled:pointer-events-none disabled:opacity-50`。

### 基础原语索引

| 组件 | 位置 | 要点 |
|------|------|------|
| Button | `ui/button` | variant: default/destructive/outline/secondary/ghost/link · size: default/xs/sm/lg/icon · `loading`/`asChild` |
| Card | `ui/card` | 复合: CardHeader/CardTitle/CardDescription/CardAction/CardContent/CardFooter · 可交互加 `card-interactive` |
| Tabs | `ui/tabs` | variant: default(药丸)/line(下划线) · orientation: horizontal/vertical |
| Dialog | `ui/dialog` | 复合: DialogHeader/DialogContent/DialogFooter · `showCloseButton` · 长内容用 `flex-1 overflow-y-auto` |
| Badge | `ui/badge` | variant: default/secondary/outline/ghost/brand/success/warning/error/destructive |
| Item | `ui/item` | 复合: ItemMedia/ItemContent/ItemTitle/ItemDescription/ItemActions · variant: default/outline/muted · `asChild` |
| 其他原语 | `ui/*` | Input · Textarea · Select(sm/default) · Switch(sm/default) · Checkbox · SegmentedControl · Tooltip · Popover · DropdownMenu · Sheet · Drawer · ScrollArea · Skeleton · Spinner · Avatar |

### 业务组件详解

#### IconBox — 图标容器

统一所有图标容器的尺寸、圆角和背景色。使用 CVA 管理 variant。

```tsx
import { IconBox } from "@valuz/ui"

<IconBox size="md" variant="brand"><FolderIcon /></IconBox>
```

| size | 尺寸 | 圆角 | 典型场景 |
|------|------|------|---------|
| `sm` | 7×7 | md | 操作按钮图标容器 |
| `md` | 9×9 | lg | **默认**。列表项图标、设置页图标 |
| `lg` | 10×10 | xl | ActionCardGrid 图标 |
| `xl` | 11×11 | xl | 空态图标、onboarding 图标 |

| variant | 背景 | 用途 |
|---------|------|------|
| `default` | surface-soft | 通用图标容器 |
| `brand` | brand-light + border | 品牌强调图标 |
| `muted` | surface-soft + ink-muted | 次要图标 |
| `outline` | surface-soft + border | 带边框图标（设置页常用） |

#### EmptyState — 空状态

两个 variant 覆盖所有空态场景：

```tsx
import { EmptyState } from "@valuz/ui"

// 行内空列表（虚线边框卡片）
<EmptyState message={t("common.noData")} />

// 全页居中空态（无背景）
<EmptyState
  variant="plain"
  title={t("project.createTitle")}
  description={t("project.emptyState")}
  icon={<FolderKanban />}
  action={<Button size="sm">创建</Button>}
/>
```

- `dashed`（默认）：虚线边框 + 背景色，用于列表内的行内空态
- `plain`：居中布局，用于全页空态。图标用 `IconBox size="xl"` 渲染

#### FormDialog — 表单弹窗模板

自动生成 DialogHeader + 内容区 + DialogFooter，消除 20+ 弹窗的重复代码。

```tsx
import { FormDialog, DialogField } from "@valuz/ui"
import { Input } from "@valuz/ui"

<FormDialog
  open={open} onOpenChange={setOpen}
  title={t("common.create")}
  description={t("project.instruction")}
  onSubmit={handleSubmit}
  submitLabel={t("common.submit")}
  cancelLabel={t("common.cancel")}
  loading={busy}
>
  <DialogField label={t("common.name")} required>
    <Input value={name} onChange={e => setName(e.target.value)} />
  </DialogField>
</FormDialog>
```

- `onSubmit`：设置后自动渲染 Submit 按钮（`variant="default"`）
- `destructive`：Submit 按钮改为 `variant="destructive"`
- `loading`：Submit 显示 spinner 并 disable，Cancel 也 disable
- `footer`：传入自定义节点完全替换默认 footer
- `maxWidthClass`：覆盖弹窗宽度，如 `"sm:max-w-xl"`
- 弹窗内字段统一用 `DialogField`（支持 required/help/helpUrl）

#### PageHeader — 页面头部

```tsx
import { PageHeader } from "@valuz/ui"

<PageHeader
  title={t("sidebar.projects")}
  description={t("project.createDesc")}
  action={<Button size="sm"><Plus /> 创建</Button>}
/>
```

- 布局: title + description 左对齐，action 右对齐
- 用法：通过 `setHeader()` 设置到页面 header slot（见 ProjectsPage）

#### SectionCard — 内容区块

```tsx
import { SectionCard } from "@valuz/ui"

<SectionCard
  eyebrow="品牌"       // 可选，Badge 展示
  title="标题"
  description="描述"
  accent={<Button>操作</Button>}  // 可选，右上角
>
  {/* 子内容 */}
</SectionCard>
```

#### SettingsNav — 设置页导航

桌面端侧边栏 + 移动端 pill 按钮自适应。替换 SettingsPage 的手写导航。

```tsx
import { SettingsNav } from "@valuz/ui"

<SettingsNav
  items={[
    { id: "general", icon: <Palette />, label: t("...") },
    { id: "model", icon: <Cpu />, label: t("...") },
  ]}
  value={tab}
  onValueChange={setTab}
/>
```

#### CategorizedList — 分类列表

4+ 页面复用（Agents/Skills/Connectors/Knowledge）。可折叠分组 + 自定义 filter/sort + 空态。

```tsx
import { CategorizedList } from "@valuz/ui"

<CategorizedList
  items={items}
  categories={categories}
  renderItem={(item) => <MyItemRow ... />}
  emptyState={<EmptyState message={t("common.noData")} />}
/>
```

#### DeleteConfirmDialog — 删除确认

15+ 处使用。内置 AlertTriangle 图标、loading 态、i18n。

```tsx
import { DeleteConfirmDialog } from "@valuz/ui"

<DeleteConfirmDialog
  open={!!deleteTarget}
  onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
  itemName={deleteTarget?.name}
  onConfirm={() => void handleDelete()}
/>
```

#### 其他业务组件

| 组件 | 用途 |
|------|------|
| `StatusPill` | 状态标签。running 自动脉冲动画。颜色通过 `status-tone.ts` 映射 |
| `ActionCardGrid` | 操作卡片网格（2 列）。Onboarding 入口选择 |
| `CatalogPickerDialog` | 多选选择器。skills/connectors 批量选择 |
| `BackLink` | 返回导航。ArrowLeft + 可配置 label |
| `PageLoader` | 页面加载态。Logo shimmer |
| `SearchInput` | 搜索输入框。内置 Search 图标 |
| `ResourceActionSlot` | 插件扩展点。OSS 渲染空，commercial 注入操作按钮 |

### 表单规范

**字段包装器分工：**

| 组件 | 用途 | label 样式 | 特殊能力 |
|------|------|-----------|---------|
| `DialogField` | 弹窗内表单 | `text-xs text-ink-meta` | required、help、helpUrl |
| `FormField` | 通用表单 | `text-xs font-medium text-ink-label` | error message |
| `SettingsRow` | 设置页 label + control 并排 | `text-sm font-medium text-ink-heading` | desc、`grid-cols-[1fr_auto]` |

**控件选择：** Input(h-9) / Textarea(auto-grow, max-h-[40vh]) / Select(sm/default) / Switch(on/off) / Checkbox(multi-select) / SegmentedControl(2-4 互斥选择)。所有控件共享 `rounded-md` + `border-input` + `focus-visible:ring` + `disabled:opacity-50`。

### 页面级组合模式

**列表页**（AgentsPage / SkillsPage / ConnectorsPage / KnowledgePage）：
```
PageHeader(title + desc + action)           ← 设置到 header slot
  └─ CategorizedList                         ← 分组列表
       ├─ ResourceActionSlot                 ← 插件扩展
       └─ EmptyState(variant="dashed")       ← 空态
PageLoader                                   ← loading 态
```

**设置页**（SettingsPage）：
```
SettingsNav(items, value, onValueChange)     ← 左侧导航 + 移动端 pill
  └─ 右侧内容区
       └─ SettingsSection(title + desc)
            └─ SettingsRow(label + control)
```

**详情页**（AgentDetailPage / SkillDetailPage）：
```
BackLink                                     ← 返回导航
Tabs(variant="line")                         ← 标签页切换
  └─ 各标签页内容
```

**表单弹窗**（所有创建/编辑弹窗）：
```
FormDialog(title + onSubmit + loading)
  └─ DialogField(label + required + help)
       └─ Input / Select / SegmentedControl
```

### 组合与扩展

- **className 合并：** 所有组件通过 `cn()` 合并，后传入优先
- **asChild：** Radix Slot 模式，将样式应用到子元素（Link 等）。`asChild` 下 Button 的 loading spinner 不渲染
- **data-slot：** 所有组件根元素设置 `data-slot`。有 variant/size 的同时设置 `data-variant` / `data-size`
- **CVA：** 新增带变体的组件使用 `class-variance-authority`
- **Utility classes：** `card-interactive` / `hover-lift` / `section-card` / `label-mono` / `tabular`

### 新增组件检查清单

- [ ] 放置位置：基础原语 `ui/`，通用业务 `common/`
- [ ] `data-slot` 属性已设置
- [ ] 圆角遵循上方对照表
- [ ] 颜色使用语义 token，无硬编码色值
- [ ] Focus 态：`focus-visible:border-ring focus-visible:ring-[1px] focus-visible:ring-ring/50`
- [ ] Disabled 态：`disabled:pointer-events-none disabled:opacity-50`
- [ ] `className` 通过 `cn()` 合并，支持外部覆盖
- [ ] 有 variant 时使用 CVA 管理
- [ ] i18n：所有用户可见文字使用 `t()` 调用
