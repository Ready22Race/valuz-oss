# Valuz Design Spec v2.6

> 2026-06-15 · 替代 `frontend/docs/design/DESIGN.md`（v1）
> 配套文件：[tokens.css](tokens.css)（可直接替换 project.css 的 token 段）· [spec.html](spec.html)（可视化规范页）· [components.html](components.html)（十个高频组件）· [design-audit.html](design-audit.html)（一致性检测器）
> 本文档锚定 **`packages/ui` 真实组件代码**，不再锚定原型 `app.jsx`。

---

## 0. 裁决记录

v1 时期 Figma 色板、DESIGN.md、代码 token 三方互相矛盾。以下裁决为最终值，三端（Figma Variables / 本文档 / tokens.css）同步生效：

| 项 | 裁决值 | 废止值 | 说明 |
|---|---|---|---|
| 品牌主色 | **#725cf9** | #6d5cff、#533afd、#965cf9 | Figma 色板钦定主色；代码端改 `--brand` 一行全局生效 |
| Warning 橙 | **#ef8b0c**（v2.4） | #ff8710、#d97706 | 三件套架构后基色卸下文字职责，按设计反馈提亮去棕褐感；文字场景一律用派生的 warning-text |
| 状态绿 | **#16a34a** | — | 连接成功、完成态、checkmark |
| 财务绿（跌） | **#53bc76** | **#53cb76** | #53cb76 确认为 Figma 色板标签笔误（b/c 颠倒），代码中 9 处需替换 |
| 财务红（涨） | **#f54b4b** | — | 与 error #dc2626 语义分离，不得混用 |
| Accent 粉 | **#ec4899** | #ef5da8 | 色板色块误填，以标签与代码为准 |
| Error 红 | **#e5484d** | #dc2626 | 与品牌紫等相对亮度（≈0.18），视觉重量一致；白字对比度 4.5:1 压线 AA |
| Disabled 文字 | **fg-30 (≈#b6b7bc)** | #e6e7e9 | v1 值对比度 1.3:1 不可读；划线完成态仍可用 fg-30+line-through |

---

## 1. 架构原则

```
原语层（手选）   每个模式只允许 18 个手选颜色：9 基色 + 1 蓝灰极 + 8 点缀色
    ↓ color-mix 派生
语义层（派生）   灰阶 fg-1~80、状态三件套、品牌色阶、阴影
    ↓ 引用
组件层           button/popover/card… 只引用语义层，禁止引用原语或字面量
```

**三条铁律：**
1. 任何新颜色必须能回答"从哪个基色派生"。答不上来 = 不准进代码。
2. 同语义必须同 token：次级文字只有 `ink-body`（即 fg-60）一个名字。
3. 新组件先查 `packages/ui/components/ui/`，第二次出现的样式组合必须上提为组件。

---

## 2. 颜色

### 2.1 基色（唯一手选区）

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--background` | `#f8f9fb` | `#0f1012` | 页面大背景 |
| `--foreground` | `#131313` | `#e4e4e7` | 一级文字、灰阶派生源 |
| `--surface` | `#ffffff` | `#17181c` | 面板、卡片、弹层 |
| `--brand` | `#725cf9` | `#8b7afc` | 主 CTA、品牌强调、focus ring、Info |
| `--success` | `#16a34a` | `#22c55e` | 状态绿 |
| `--warning` | `#ef8b0c` | `#f59e0b` | 警示（实底/图标须配文字，独立传达信息用 warning-text） |
| `--error` | `#e5484d` | `#ef4444` | 错误、危险操作 |
| `--finance-up` | `#f54b4b` | `#ff6b6b` | 涨（仅财务数据） |
| `--finance-down` | `#53bc76` | `#5fd389` | 跌（仅财务数据） |
| `--slate` | `#444b54` | `#a8b1bf` | 蓝灰极（=v1 neutral/700），灰阶中段的派生轴 |

点缀/图表分类色（8 个，暗色用对应 400 档）：
`1 sky #0ea5e9 · 2 teal #14b8a6 · 3 amber #eab308 · 4 pink #ec4899 · 5 blue #3b82f6 · 6 lime #84cc16 · 7 orange #f97316 · 8 fuchsia #d946ef`
规则：图表系列严格按此顺序取色（保证跨页面同一序号同色）；超过 8 个系列用 `fg-50` 归并为"其他"；amber 是点缀不得当 warning 用；扩展时同亮度饱和度只动色相。

### 2.2 灰阶（派生，禁止手选）

双轴结构：浅端（1~12）沿 foreground 派生保持中性；中段（30~80）沿蓝灰极 `slate` 派生——冷调在中间最饱满、两端归零，与 v1 色板的冷灰气质一致。档位名表示深度，不等于混合百分比。

| Token | 配方 | ≈v1 值 | 用途 |
|---|---|---|---|
| `fg-1` | fg 1% → bg | `#f7f8fa` | hover 底、工具卡片底 |
| `fg-3` | fg 3% | `#f3f4f6` | 分割线、内卡边界 |
| `fg-5` | fg 5% | `#f5f5f4` | 次级浅底 |
| `fg-8` | fg 8% | `#e6e7e9` | 常规边框 |
| `fg-12` | fg 12% | `#dbdbdb` | 强边界 |
| `fg-30` | slate 37% → bg | `#b6b7bc` | 弱辅助文字、disabled |
| `fg-50` | slate 60% | `#898f9c` | 图标次级（v1 漏收，曾被硬编码） |
| `fg-60` | slate 75% | `#6e7481` | **次级文字唯一 token**（ink-body） |
| `fg-80` | = slate | `#444b54` | 强次级（v1 漏收） |

### 2.3 状态色三件套

每个状态色固定三个派生角色，**禁止手配浅底/深字**：

```
X-soft   = mix(X 10%, background)   浅底（badge、toast 背景）
X-border = mix(X 35%, background)   边框
X-text   = mix(X 65%, foreground)   该底色上的文字
```

Info 复用品牌紫（裁决：不引入第五个状态色相）。

### 2.4 品牌色阶

`50 #f3f2ff · 100 #eae6ff · 200 #d1c8ff · 300 #b29ff7 · 500 #725cf9 · 600 #5d46e8(hover) · 700 #4936c2(active/深字)`
渐变统一为 `--brand-gradient`（600→300），废止手写 `#533afd→#965cf9`。

---

## 3. 字体排印

- 主字体 `PingFang SC`；等宽 `ui-monospace/SF Mono/Menlo`；`Newsreader + Noto Serif SC` 仅限 onboarding 大标题。
- 注意：PingFang 无真斜体、字重档只有 Regular/Medium/Semibold 可用——**禁用 italic 和 700 以上字重**（700 仅限 ≤10px badge 的 Latin/数字）。

### 字阶（8 档整数，0.5px 档全部废止）

| Token | 字号 | 行高 | 字重 | 用途 |
|---|---|---|---|---|
| `micro` | 10px | 1.2 | 600/700 | 文件 badge、极小标识（仅图形场景，不做正文） |
| `2xs` | 11px | 1.4 | 400/600 | Section Label、表头、状态标签 |
| `xs` | 12px | 1.5 | 400/500 | meta、按钮标签、inline code、菜单副文案 |
| `sm` | 13px | 1.55 | 400/500 | Sidebar 行、Composer、列表主文案、菜单主文案 |
| `base` | 14px | 1.7 | 400/500 | **消息正文**、标题栏标题、页面基准 |
| `lg` | 15px | 1.5 | 500 | 右侧面板主标题 |
| `xl` | 18px | 1.4 | 500/600 | 页面标题 |
| `2xl` | 24px | 1.3 | 500 | onboarding、大数字 |

字重：400 正文 / 500 标题、关键字段 / 600 Label、强调 / 700 仅 micro badge。

---

## 4. 间距 · 圆角 · 阴影 · 层级 · 动效

- **间距**：4px 网格 `4 / 8 / 12 / 16 / 20 / 24 / 32`（v1 的 6/9/10/14/28 就近归档）。
- **圆角**：`sm 4 / md 6 / lg 8 / xl 10 / 2xl 12 / full`（2/3/7/14px 废止）。同层级容器共享同一档。
- **阴影**：从前景色派生（暗色自动加深）。**是否带 1px spread 环，取决于元素自身有没有边框**——v2.6 的关键分层：
  - `shadow-outline` 纯投影无环 → 给「自带 fg-8 边框」的元素（outline 按钮、input），避免环+边框双描边。
  - `shadow-1` 投影 + fg-3 浅环 → 给「无边框」的容器（卡片、popover、toast），环代替边框、更立体。
  - `shadow-2` 悬浮卡片/sidebar active · `shadow-3` popover/dropdown · `shadow-4` modal/浮层/应用外壳。
  - 铁律：**有边框用 shadow-outline，无边框用 shadow-1**，不要在有边框元素上叠带环阴影。
- **z-index**：`base 0 · sticky 20 · titlebar 40 · panel 50 · dropdown 100 · tooltip 150 · modal 200 · toast 300`。
- **动效**：`fast 120ms`（hover）· `base 200ms`（折叠/滑入）· `slow 250ms`（布局），easing 统一 `cubic-bezier(0.4,0,0.2,1)`。

## 5. 图标

- Lucide 风格，viewBox 24，round cap/join。
- **stroke 只有 2 档**：默认 `2`，≥18px 大图标 `1.5`（v1 的 1.9/1.8/1.7/1.6 全部归 2，未选中 checkbox 的 1 归 1.5）。
- 尺寸 3 档：`12 / 14 / 16`。
- 颜色：一级 `foreground`，二级 `fg-60`，折叠箭头 `fg-50`；同一行 ≤2 种图标色；普通图标禁用品牌色。

## 6. 无障碍底线

- 正文/标签文字 ≥ AA（4.5:1）：`foreground`、`fg-60`、`fg-80`、各 `X-text` 达标。
- `fg-50` 仅限 ≥18px 或图标；`fg-30` 仅限 disabled 与已完成划线，不承载必读信息。
- **两种 focus 视觉，职责不同，不要混为一谈**：
  - `focus-visible`（键盘可达性指示）：所有可交互元素 `2px var(--brand)` ring + `2px offset`，禁止 `outline: none` 裸奔。用于 Tab 导航高亮，**对所有组件统一**。
  - Input **active focus**（字段正被输入时的状态）：边框转 `brand` + `0 0 0 3px` 的 `brand 20%` 柔和内环（不带 offset）。这是「当前活跃字段」的视觉，与上面的键盘焦点环是两件事，可以共存（shadcn 等同款做法）。两者都保留，不要为了「统一」把 Input 改成硬 ring——那会损 UX。

## 7. 组件层对齐规则

- 设计稿变体名 = 代码 props 名，逐字一致：Button 为 `default(主紫) / outline(次要) / ghost / destructive / link` × `xs / sm / default / lg / icon*`。Figma 现有 Primary/Secondary 命名按此重命名。
- **`secondary` 变体废止，并入 `outline`**（裁决：一个强调层级只配一个变体，消除"用哪个"的歧义；代码实测 outline 102 处 vs secondary 4 处，outline 已是事实上的次要按钮，且在灰底面板上白底+边框比灰填充更清晰）。迁移：cva 里将 `secondary` 设为 `outline` 的 deprecated 别名 → 改掉 4 处调用（其中 MultiSelect 把按钮当标签用，应改 Badge）→ 删除别名。强调阶梯固定为：`default > outline > ghost > link`。
- 状态全集：`default / hover / active / focus-visible / disabled / loading`，新组件缺一不交付。
- **Button 阴影/hover 规则（v2.6）**：`outline` 用 `shadow-outline`（纯投影，因自带边框）；`outline`/`ghost` 的 hover 底统一用 `fg-5` 中性灰（ghost 旧版用 brand-100 紫底太抢眼，已改）；只有 `default` 实底按钮 hover 走品牌色 `brand-600`。
- DESIGN.md v1 §5 的组件像素规格（Sidebar/Composer/Popover/ToolCard/ContextPanel）仍然有效，但其中色值/字号/圆角一律按本文档 token 替换字面量。

## 8. 暗色模式

原则：`.dark` 只重定义 **§2.1 基色** + **明确声明的派生锚点**，其余全部自动重算。允许覆盖的锚点仅限两类（已在 tokens.css 注释标明，不得扩大）：
1. **品牌浅阶 brand-50~300**：暗底上的「浅紫」必须是「紫混深背景」，无法用亮色模式的公式推导，故按 `color-mix(brand, background)` 反向重定义。
2. **阴影参数 shadow-rgb / border-a / blur-a**：暗色下阴影需整体加深，调的是这三个强度参数（仍是参数化，非逐值特例）。

除以上两类锚点外，**禁止在 .dark 里写任何非基色覆盖**——若暗色下某处不对，修的是派生配方或上述锚点参数，而不是加一次性特例。

## 9. 迁移对照（codemod 清单）

| 旧值（代码中实测存在） | → 新 token |
|---|---|
| `#6d5cff` `#533afd` `#965cf9`（品牌紫） | `var(--brand)` / `--brand-gradient` |
| `#53cb76`（9 处笔误绿） | `var(--finance-down)` 或 `var(--success)` 按语义 |
| `#9aa3b2` `#898f9c` | `fg-50` |
| `#525860` `#444b54` `#1f2937` | `fg-80` |
| `#f7f7f8` `#F0F1F3` | `fg-1` / `fg-3` |
| `text-[13.5px]` | `text-base`（14px） |
| `text-[12.5px]` | `text-sm`（13px） |
| `text-[11.5px]` `text-[10.5px]` | `text-xs` / `text-2xs` |
| `rounded-[7px]` | `rounded-md` |
| `rounded-[14px]` `rounded-[18px]` | `rounded-2xl` |
| `text-muted-foreground`（63 处） | 保留可用（已 alias 到 fg-60），新代码统一 `text-ink-body` |
| `variant="secondary"`（4 处） | `variant="outline"`（MultiSelect 处改用 Badge） |

## 10. 治理

1. ESLint：tsx 禁 hex 字面量、禁 `text-[npx]` 任意值（CI 红灯）。
2. `scripts/design-audit.sh` 棘轮：违规计数只许减不许增。
3. `frontend/CLAUDE.md` 写入："颜色只用语义 token；新 UI 先查 packages/ui；变体/状态全集见 DESIGN-v2 §7"。
4. CODEOWNERS：`packages/ui/src/styles/**` 由设计负责人 review。
5. 本文档新增模式走文末 changelog：记"新增了什么、从哪个基色派生、为何现有组件不够"。

---

## Changelog

- **2026-06-11 v2**：三方裁决（§0）；token 架构改派生制；字阶/圆角/阴影/图标收敛；新增状态三件套、z-index、动效、无障碍、暗色规则。
- **2026-06-11 v2.1**：error 红 #dc2626 → #e5484d（设计评审反馈：与品牌紫明度不齐，提亮至等亮度）。
- **2026-06-11 v2.2**：灰阶改双轴派生——新增蓝灰极 `slate #444b54`，中段（fg-30~80）沿其派生恢复 v1 冷灰气质；手选色 13 → 14。
- **2026-06-11 v2.3**：Button `secondary` 变体废止并入 `outline`（outline 102 处 vs secondary 4 处，已是事实标准）；强调阶梯固定为 default > outline > ghost > link。
- **2026-06-11 v2.4**：warning #d97706 → #ef8b0c（设计反馈发脏；基色已卸下文字职责）；点缀色 4 → 8（投研图表多系列需要），定固定取色顺序；手选色 14 → 18。
- **2026-06-11 v2.5**：shadow-1 去掉 1px spread 环改纯投影 y1/blur2（设计反馈：环与 fg-8 边框叠加成双描边）。
- **2026-06-15 v2.6**：阴影改分层方案（采纳设计团队反馈，优于 v2.5 的一刀切去环）——新增 `shadow-outline`（纯投影）给有边框元素，`shadow-1` 恢复为投影+`fg-3`浅环给无边框容器；ghost hover 由 brand-100 紫底改 fg-5 灰底。三文件（tokens.css / spec.html / DESIGN）已同步（修复 v2.5→团队版只改 spec.html 未同步 tokens.css 的漏洞）。
- **2026-06-15 v2.6.1**：外部评审修正（均为文档/清理，无视觉数值改动）——①删除 spec.html 残留的死代码 `.btn-secondary`（v2.3 已废止）；②spec.html 头部注明「视觉预览，完整 token 以 tokens.css 为准」；③§6 区分 focus-visible（键盘环）与 Input active focus（柔和内环）两种焦点视觉，明确二者共存；④§8 放宽 dark 表述为「基色 + 明确声明的派生锚点（brand 浅阶 / 阴影参数）」，与 tokens.css 实现对齐。
