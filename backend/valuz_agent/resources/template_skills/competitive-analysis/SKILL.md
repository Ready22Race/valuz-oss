---
name: competitive-analysis
description: Competitive landscape analysis for global equities (focus US / HK / A-shares, also other markets). Maps competitors, compares positioning, and assesses relative strengths across markets. Uses `valuz-stock` (industry_constituents, company_overview, revenue_breakdown, income_statement, company_shareholders) for peer sets, financials, and share data, and `valuz-search` (reports_search, conferences_search, filings_search) for research, earnings calls, and filings. Triggers on "竞争格局", "行业竞争分析", "competitive landscape", "competitive analysis", or "[company] competitors".
---

# competitive-analysis

## Purpose

Analyze **全球股票市场（美股/港股/A 股为主，兼顾其他市场）行业竞争格局**, mapping competitive dynamics for companies and industries across markets.

## Data Sources

Two Valuz connectors cover everything this skill needs:

- `valuz-stock` — 行情、财务、份额/指标数值数据 (quotes, financials, market-share and indicator figures).
- `valuz-search` — 财报、公告、研报、纪要、电话会检索 (earnings reports, filings, research, minutes, earnings calls).

Rule of thumb: 用 `valuz-stock` 取财务/份额数据，用 `valuz-search` 取定性资料。

**Symbol format (重要):** `valuz-stock` 用裸代码 (`AAPL` / `00700` / `600519`)；
`valuz-search` 用 `market:ticker` (`US:AAPL` / `HK:00700` / `SH:600519`)。

```text
industry_constituents(industry="...")           (valuz-stock) → 同行/竞争集 peer list
company_overview(symbol=...)                     (valuz-stock) → 业务描述、估值、市值
revenue_breakdown(symbol=..., period="annual")   (valuz-stock) → 营收/业务结构、份额拆分
income_statement(symbol=..., period="annual")    (valuz-stock) → 营收、毛利、净利
balance_sheet(symbol=..., period="annual")       (valuz-stock) → 资产、负债
company_shareholders(symbol=...)                 (valuz-stock) → 股东/控制权
reports_search(query=..., symbols=[...])         (valuz-search) → 卖方研报、竞争定性
conferences_search(query=..., symbols=[...])     (valuz-search) → 电话会、管理层口径
filings_search(query=..., symbols=[...])         (valuz-search) → 公告、招股书、分部披露
```

Tickers span markets — US (`AAPL` / `US:AAPL`), HK (`00700` / `HK:00700`),
A-share (`600519` / `SH:600519`), and others.

### Secondary Sources
- Annual / segment reports — detailed segment data (`filings_search` via `valuz-search`)
- Sell-side industry reports — analyst competitive analysis (`reports_search` via `valuz-search`)
- Earnings-call commentary — management framing of rivals (`conferences_search` via `valuz-search`)
- Market-share / revenue-mix data — `revenue_breakdown` via `valuz-stock`
- Industry associations — industry statistics

## Workflow

### Step 1: Map the Competitive Set

**Industry definition:**
```text
# Get full industry composition (cross-market peers) — valuz-stock 裸代码输出
industry_constituents(industry="spirits / liquor")
```

提示：`valuz-stock` 用裸代码 (`AAPL` / `00700` / `600519`)，`valuz-search` 用 `market:ticker` (`US:AAPL` / `HK:00700` / `SH:600519`)。

**Tier the competitors:**

| Tier | Description | Examples |
|------|-------------|---------|
| Tier 1 (龙头) | Market leaders, >10% share | {{SECTOR_LEADER}} ({{EXAMPLE_SECTOR}}) |
| Tier 2 (挑战者) | Strong #2-5, growing share | {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| Tier 3 (跟随者) | Niche players, regional | {{NICH_PLAYER}} |
| Tier 4 (边缘) | Declining or niche | {{LOW_END_PLAYER}} |

Peer/competitor sets are cross-market — a leader in one market may compete with challengers listed elsewhere (US / HK / A-share / others).

### Step 2: Competitive Comparison Matrix

每家竞争者用 `company_overview` (valuz-stock) 取业务描述/估值/市值，
`income_statement` (valuz-stock) 取营收、毛利、净利，`revenue_breakdown`
(valuz-stock) 比业务结构与份额；控制权/股东差异用 `company_shareholders`
(valuz-stock)。全部传裸代码 (`AAPL` / `00700` / `600519`)。

**Core comparison table:**

| Company | Revenue | YoY | Gross Margin | Net Margin | ROE | Market Cap | PE (TTM) | Market Share |
|---------|---------|-----|-------------|------------|-----|-----------|----------|-------------|
| | | | | | | | | |

**Expand with competitive dimensions:**

| Dimension | Leader | Challenger 1 | Challenger 2 | Follower |
|-----------|--------|-------------|-------------|---------|
| 品牌力 (Brand) | | | | |
| 渠道能力 (Distribution) | | | | |
| 产品力 (Product quality) | | | | |
| 成本优势 (Cost advantage) | | | | |
| 研发投入 (R&D) | | | | |
| 国际化 (International) | | | | |

### Step 3: Market Share Analysis

份额/营收结构逐年取自 `revenue_breakdown`(symbol, period="annual") (valuz-stock)；
官方口径的市场地位/份额引述用 `reports_search` 或 `filings_search`
(valuz-search，`market:ticker` 代码，如 `US:AAPL`)。

**Share trends:**

| Company | 2020 | 2021 | 2022 | 2023 | 2024E | Trend |
|---------|------|------|------|------|-------|-------|
| | | | | | | ↑ / → / ↓ |

**Concentration metrics:**
- CR3, CR5, CR10 (top 3/5/10 concentration)
- HHI (Herfindahl-Hirschman Index)
- Market share distribution

### Step 4: Competitive Positioning

战略定位/护城河/管理层对竞争的定性判断，用 `reports_search`、`conferences_search`、
`filings_search` (valuz-search，`market:ticker` 代码) 检索研报观点、电话会口径与公告披露。

**Positioning map:**

For 2x2 matrices, use:
- X-axis: Price (价格) or Scale (规模)
- Y-axis: Quality (品质) or Growth (增速)

Example for {{EXAMPLE_SECTOR}}:
```
         高端/品质
           |
   {{SECTOR_LEADER}}    |   {{CHALLENGER_1}}/{{CHALLENGER_2}}
           |
   {{NICH_PLAYER}} |   {{NATIONAL_BRAND}}
           |
           |________________
              低端/性价比    高端/溢价
```

### Step 5: Barriers to Entry

**Common barriers:**

| Barrier Type | Examples |
|-------------|---------|
| 品牌护城河 | Consumer brand loyalty, 品牌认知 |
| 渠道壁垒 | Distribution network, 经销商体系 |
| 规模效应 | Cost advantages from scale |
| 技术壁垒 | Patents, know-how, 技术积累 |
| 牌照/资质 | Regulatory licenses, 牌照 |
| 资金壁垒 | Capital requirements |
| 政策壁垒 | Industry access / regulatory restrictions |
| 数据壁垒 | Data network effects |

### Step 6: Threat Assessment

**New entrants:**
- Likely sources (related industries, overseas)
- Barriers effectiveness

**Substitutes:**
- Alternative products/services
- Switching costs

**Supplier power:**
- Input concentration
- Price volatility (e.g., 原材料)

**Buyer power:**
- Customer concentration
- Switching costs

**Rivalry intensity:**
- Number and size of competitors
- Industry growth rate
- Differentiation level
- Exit barriers

### Step 7: Competitive Dynamics

价格战、产能扩张、并购、新品等竞争动态的定性证据，用 `conferences_search`
(管理层口径)、`reports_search` (卖方观点)、`filings_search` (公告/交易披露)
(valuz-search，`market:ticker` 代码)；扩张/并购对营收结构的影响用 `revenue_breakdown`
(valuz-stock) 印证。

**Historical evolution:**
- How has competitive landscape changed?
- What drove shifts (policy, technology, demand)?

**Current dynamics:**
- Price competition (价格战)
- Capacity expansion
- M&A activity
- New product launches

**Future outlook:**
- Likely consolidation?
- New entrants?
- Technology disruption?

## Market-Specific Considerations

### Industry Structure

| Pattern | Description | Example Industries |
|---------|-------------|-------------------|
| 寡头垄断 | Few large players | spirits (top 5 >80%) |
| 分散竞争 | Fragmented, many players | restaurants, retail |
| 区域割据 | Regional champions | beer, food processing |
| 龙头集中 | Consolidating toward leaders | appliances, drug distribution |

### Competitive Behavior

- **价格战** (price wars) — common in commoditized sectors
- **渠道争夺** (channel competition) — 经销商, 线上平台 / online platforms
- **产能扩张** (capacity race) — leads to overcapacity
- **并购整合** (consolidation M&A) — industry rationalization
- **国际化** (going global) — emerging competitive frontier

### Regulatory & Government Role

- Industrial policy shapes competitive dynamics
- State-owned vs private competitive dynamics (e.g., A-share 国企 vs 民企)
- Local protectionism (地方保护) in some markets
- Antitrust / 反垄断 enforcement affects market structure (e.g., US DOJ/FTC, EU, China SAMR)

## Output Format

**Standard competitive analysis deliverable:**

```
【行业】竞争格局分析

一、行业概述
   市场规模, 增速, 发展阶段

二、竞争地图
   Tier划分, 市场份额

三、核心竞争要素
   各玩家优势对比

四、竞争动态
   价格, 渠道, 产能, 并购

五、壁垒分析
   进入壁垒, 现有壁垒有效性

六、趋势展望
   行业整合, 新进入者, 技术变革

七、结论与启示
   投资/战略含义
```

## Quality Checks

Before delivering:
- [ ] Competitive set complete and relevant (`industry_constituents`)
- [ ] Market share / revenue mix sourced (`revenue_breakdown` + `reports_search`/`filings_search`)
- [ ] Comparison matrix comprehensive
- [ ] Barriers analyzed
- [ ] Competitive dynamics explained
- [ ] Forward outlook included
- [ ] Strategic implications drawn
