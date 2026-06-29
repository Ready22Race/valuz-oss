---
name: idea-generation
description: Systematic stock screening and investment idea sourcing for global equity markets (focus US / HK / A-shares, also other markets). Combines quantitative screens, thematic research, and pattern recognition to surface new long and short ideas. Powered by valuz-stock (quotes, financials, factor screening via factors_screen, concepts, backtest) and valuz-search (earnings, conferences, research reports, minutes, filings, news). Triggers on "选股", "股票筛选", "寻找机会", "stock screen", "stock ideas", "find ideas", or "screen for opportunities".
---

# idea-generation

## Purpose

Systematically surface new **全球股票市场（美股/港股/A 股为主，兼顾其他市场）投资机会** through quantitative screens, thematic analysis, and pattern recognition.

## Data Sources

Two MCP connectors cover the full sourcing workflow:

- `valuz-stock` (Stock MCP) — 行情、财务、指标、因子筛选数值数据. 用**裸代码** (`AAPL` / `00700` / `600519`).
- `valuz-search` (Search MCP) — 财报、公告、研报、纪要、电话会检索. 用 **`market:ticker`** 格式 (`US:AAPL` / `HK:00700` / `SH:600519`).

> **Symbol formats** — `valuz-stock` 用裸代码；`valuz-search` 用 `market:ticker`；`factors_screen` 用 `market` 参数 (`cn` / `hk` / `us`).

用 `valuz-stock` 的因子引擎 (`factors` / `factors_compute` / `factors_screen`) 做量化筛选，用 `valuz-search` 取定性催化/主题资料。

### Primary: valuz-stock

```python
factors()                                     → 列出可用因子 (PE/PB/ROE/RSI/MACD…)
factors_compute(symbol, formula)              → 算单只标的因子值, 验证公式
factors_screen(market, formula)               → 因子选股 (核心), market=cn/hk/us
stock_quote(symbol)                           → Price, PE, PB, market cap
index_quote(symbol)                           → 指数行情
company_overview(symbol)                       → 公司概览
income_statement(symbol, period, limit)       → Financial metrics
balance_sheet(symbol, period, limit)          → Balance sheet health
cashflow_statement(symbol, period, limit)     → 现金流
revenue_breakdown(symbol)                      → 收入拆分
industry_constituents(...)                     → Peer comparison
concepts_today() / concepts_latest()           → 当日/最新热门概念
ohlcv(symbol) / kline(symbol)                  → Price trends, momentum
backtest(...)                                  → 策略回测
```

### Secondary Screening Data

| Data | Source | Use |
|------|--------|-----|
| Hot concepts / themes | valuz-stock (`concepts_today`, `concepts_latest`) | Theme rotation, where the money is going |
| Index / industry constituents | valuz-stock (`index_constituents`, `industry_constituents`) | Universe definition, peer set |
| Shareholder structure | valuz-stock (`company_shareholders`) | Institutional accumulation/distribution |
| Earnings calendar | valuz-stock (`earnings_calendar`) | Catalyst timing |
| Institutional / analyst views | valuz-search (`reports_search`) | Sell-side conviction, target prices |
| Filings & disclosures | valuz-search (`filings_search`) | Insider transactions, lock-up, M&A |

## Workflow

### Step 1: Define Screen Criteria

**Investment philosophy alignment:**
- Value vs Growth vs GARP vs Momentum
- Market cap preference (large / mid / small)
- Sector focus or sector-agnostic
- Liquidity requirements (turnover threshold)
- Risk tolerance (volatility, leverage, earnings stability)

**Screen parameters (market-aware, fields not tied to one market's data vendor):**

| Parameter | Typical Range | Notes |
|-----------|--------------|-------|
| PE (TTM) | 5-50x | Avoid negative PE |
| PB | 0.5-5x | <1x may indicate distress |
| PS | 0.5-5x | For high-growth unprofitable |
| Market cap | above liquidity threshold | Tradability |
| Daily turnover | above liquidity threshold | Tradability |
| ROE | >10% | Quality filter |
| Debt/Equity | <100% | Financial health |
| Revenue growth | >10% | Growth filter |
| EPS growth | >15% | Earnings momentum |

### Step 2: Quantitative Screens

筛选用 `valuz-stock` 的 **`factors_screen`** (核心)，传 `market` (`cn` / `hk` / `us`) + 因子 `formula`。
先用 `factors()` 看可用因子，必要时用 `factors_compute(symbol, formula)` 在单只标的上验证公式再放大到全市场。

> **Factor syntax** — 技术: `RSI(14)` / `MACD()` / `BOLL(20,2)` / `KDJ()` / `ATR(14)`；
> 基本面: `PE()` / `PE_TTM()` / `PB()` / `ROE()` / `ROA()` / `EPS()`；
> 财报字段: `INCOME.net_profit` / `BALANCESHEET.total_assets`；
> 组合用 `&` / `|`，如 `(PE()<20)&(ROE()>0.15)`. 跨市场重复同一筛选，分别传 `market="us"` / `"hk"` / `"cn"`.

**Screen 1: Deep Value (深度价值)**

```python
factors_screen(market="us", formula="(PE_TTM()<15)&(PB()<1.5)&(ROE()>0.10)")
# 港股 / A 股: market="hk" / market="cn"
```

Output: Value candidates with potential mispricing.

**Screen 2: Growth at Reasonable Price (GARP)**

```python
factors_screen(market="us", formula="(PE()<25)&(ROE()>0.15)&(EPS()>0)")
# PEG 思路: 用 factors_compute 验证 PE() 与盈利增速的比值，再以 PE 上限近似 GARP
```

Output: Quality growth at reasonable multiples.

**Screen 3: Momentum (趋势跟踪)**

```python
factors_screen(market="us", formula="(RSI(14)>40)&(RSI(14)<70)&(MACD()>0)")
# 趋势确认可叠加均线/布林: 用 factors() 查可用价格因子, factors_compute 验证
```

Output: Momentum names in uptrends.

**Screen 4: Turnaround (困境反转)**

```python
# 量化部分: 当前承压但盈利转正/资产负债改善的标的
factors_screen(market="cn", formula="(ROE()>0)&(PB()<1.5)")
# 定性催化 (新管理层 / 债务重组 / 订单回暖 / 内部人买入):
#   reports_search / filings_search / news_search (valuz-search), 用 market:ticker
```

Output: Potential turnaround candidates.

**Screen 5: Dividend Yield (高股息)**

```python
factors_screen(market="hk", formula="(PB()<2)&(ROE()>0.10)")
# 股息率/派息率/FCF 用 income_statement / cashflow_statement 核验 (valuz-stock, 裸代码)
```

Output: High-quality income names.

**Screen 6: Special Situations (事件驱动)**

```python
# 事件驱动以检索为主, 用 valuz-search (market:ticker):
#   filings_search  → 限售解禁 / M&A / 重组公告
#   news_search     → 集采结果、指数纳入/剔除、监管催化
#   reports_search  → 卖方对事件的解读
# earnings_calendar (valuz-stock) 锁定业绩披露时点
```

Output: Event-driven opportunities.

### Step 3: Thematic Research

**Identify emerging themes:**

| Theme Type | Examples | Data Sources |
|-----------|-------------------|-------------|
| Policy-driven | 国产替代, 新基建, 双碳, reshoring | `news_search` / `filings_search` (valuz-search) |
| Technology | AI应用, 自动驾驶, 机器人 | `reports_search` / `news_search` (valuz-search) |
| Demographics | 老龄化, 少子化 | `reports_search` (valuz-search) |
| Consumption | 消费升级, 国货崛起 | `reports_search` / `news_search` (valuz-search) |
| Industrial | 高端制造, 专精特新 | `reports_search` / `filings_search` (valuz-search) |

**Thematic screening approach:**
1. Define theme and investable universe — `concepts_today()` / `concepts_latest()` (valuz-stock) 找当前热门概念，`industry_constituents(...)` 定义成分股
2. Map companies (US / HK / A-share and beyond) to theme — 用 `comprehensive_search` / `reports_search` (valuz-search) 佐证个股的主题表达度
3. Rank by exposure and quality — 叠加 `factors_screen(market=..., formula=...)` 在主题股池内做质量过滤
4. Identify pure-plays vs beneficiaries — `revenue_breakdown(symbol)` (valuz-stock) 看收入对主题的纯度

### Step 4: Technical Analysis

**Technical considerations** (factor 语法可直接进 `factors_screen` / `factors_compute`，原始价量用 `ohlcv` / `kline`):

| Indicator | Factor / Tool | Use |
|-----------|---------------|-----|
| 均线 (Moving averages) | `BOLL(20,2)` + `kline(symbol)` | Trend direction (5/10/20/60/120 day) |
| MACD | `MACD()` | Momentum and trend changes |
| RSI | `RSI(14)` | Overbought/oversold |
| KDJ | `KDJ()` | Short-term momentum |
| 成交量 (Volume) | `ohlcv(symbol)` | Confirmation of moves |
| 波动率 (Volatility) | `ATR(14)` | Risk sizing, breakout strength |
| 概念热度 (Theme heat) | `concepts_today()` | Where the money is rotating |

**Chart patterns to watch:**
- 突破 (breakout) — above resistance
- 回踩 (pullback to support) — entry opportunity
- 双底/头肩 (double bottom/head & shoulders) — reversal signals
- 量价背离 (volume-price divergence) — trend exhaustion

### Step 5: Fundamental Deep Dive

**For each candidate** (核验用 valuz-stock 裸代码 + valuz-search `market:ticker`):

1. **Business model review**: How does company make money? — `company_overview(symbol)`, `revenue_breakdown(symbol)`
2. **Financial health**: Balance sheet, cash flow, earnings quality — `income_statement` / `balance_sheet` / `cashflow_statement(symbol, period, limit)`
3. **Competitive position**: Market share, moat, pricing power — `industry_constituents(...)` + `reports_search` (valuz-search)
4. **Management quality**: Track record, capital allocation — `filings_search` / `minutes_search` (valuz-search)
5. **Valuation**: vs peers, vs history, vs international peers — `stock_quote(symbol)` + `factors_compute(symbol, "PE_TTM()")` / `PB()`
6. **Catalyst**: What could re-rate the stock? — `earnings_search` / `news_search` / `reports_search` (valuz-search), `earnings_calendar` (valuz-stock)

**Red flag checklist:**
- 商誉占比过高 (>30% of equity)
- 应收账款增速 > 收入增速
- 经营现金流持续为负
- 大股东质押比例过高 (>50%)
- 审计意见非标准无保留
- 频繁变更会计师事务所
- 关联交易占比高

### Step 6: Build the Ideas List

**Standard format:**

| Rank | Ticker | Company | Sector | Idea Type | Thesis | Catalyst | Risk | Conviction |
|------|--------|---------|-------|-----------|--------|----------|------|------------|
| {{RANK}} | {{TICKER}} | {{COMPANY_NAME}} | {{SECTOR}} | {{DIRECTION}} | {{THESIS}} | {{CATALYST}} | {{RISK}} | {{CONVICTION}} |
| Example | AAPL | Apple | Tech | Long | Services mix shift + buybacks | Earnings beat | Demand slowdown | High |
| 2 | 0700.HK | Tencent | Internet | Long | Game recovery + ad growth | New title approvals | Regulatory | Medium |
| 3 | 600519.SH | 贵州茅台 | 白酒 | Long | 批价稳+动销旺+分红高 | Q1业绩超预期 | 批价下行 | High |

Tickers span markets — US (`AAPL`), HK (`0700.HK`), A-share (`600519.SH`), and others.

**Conviction levels:**
- **High**: Strong thesis, clear catalyst, limited downside
- **Medium**: Good thesis, catalyst timeline uncertain
- **Low**: Exploratory, needs more research

### Step 7: Monitor & Update

**Ongoing tracking:**
- Weekly price and news updates — `stock_quote(symbol)` (valuz-stock), `news_search` (valuz-search)
- Catalyst tracking — `earnings_calendar` (valuz-stock), `earnings_search` / `filings_search` (valuz-search)
- Thesis validation / invalidation — `reports_search` (valuz-search), `factors_compute(symbol, formula)` to re-check the screen metrics
- Position sizing recommendations

**Update triggers:**
- Earnings results
- Material news (M&A, guidance, regulation)
- Price moves >15% in a week
- Thesis breaking or confirming

## Market-Specific Screening Considerations

### Market Structure

| Feature | Screening Implication |
|---------|----------------------|
| 涨跌停限制 (price limits, where they apply) | Momentum may be interrupted |
| 散户占比 (retail participation) | Sentiment-driven overreactions more common in retail-heavy markets |
| 政策敏感 (policy sensitivity) | Regulatory risk premium in certain sectors |
| Cross-border fund flows | Track foreign flows for large-caps (`company_shareholders` for ownership shifts) |
| 概念轮动 (Concept rotation) | `concepts_today` / `concepts_latest` signal sentiment shifts |
| 停牌 (Trading halt) | Due diligence risk for suspended names |

### Common Investment Styles

| Style | Description | Key Metrics |
|-------|-------------|-------------|
| 价值投资 | Deep value, dividend, asset-based | PB, dividend yield, 破净 |
| 成长投资 | High growth, innovation | Revenue growth, R&D intensity |
| 主题投资 | Policy/trend themes | Catalyst proximity, theme purity |
| 技术分析 | Chart-based trading | 均线, MACD, 量价 |
| 量化策略 | Systematic, factor-based | Multi-factor models |
| 打新 | IPO subscription | 中签率, 涨幅预期 |

### Sector-Specific Screening

| Sector | Screening Focus |
|--------|----------------|
| 消费/必选 | 批价趋势, 渠道库存, 回款, 品牌力 |
| 半导体 | 产能利用率, 国产替代进度, 技术迭代 |
| 新能源 | 产能过剩/出清, 技术路线, 补贴退坡影响 |
| 医药 | 创新药管线, 集采中标, 国际化 |
| 银行 | NIM趋势, 不良率, 拨备, 估值 (破净) |
| 房地产 | 销售额, 融资能力, 土储质量 |
| 消费 | 动销, 库存, 消费升级/降级趋势 |

## Quality Checks

Before delivering ideas list:
- [ ] Screen criteria documented and reproducible
- [ ] Each candidate has fundamental backing
- [ ] Catalysts identified for each idea
- [ ] Risk factors clearly stated
- [ ] Conviction levels assigned
- [ ] Liquidity verified (tradable)
- [ ] Regulatory/compliance review passed
