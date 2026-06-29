---
name: dcf
description: DCF valuation model for global equities (US / HK / A-shares focus, also other markets) using Valuz financial data. Uses valuz-stock (Valuz Quotes MCP — real-time & historical quotes, financial statements, indicators) for financials and WACC inputs (risk-free rate from the relevant market's government bond yields) and valuz-search (Valuz Search MCP — earnings reports, calls, research, minutes, filings) for qualitative context and growth projections. Use instead of the original dcf-model skill for cross-market equities.
---

# dcf

## Data Sources

全球股票市场（美股/港股/A 股为主，兼顾其他市场）的 DCF 建模，统一使用两个 Valuz 连接器取数：

- `valuz-stock` (Valuz Quotes MCP) — 行情、财务三表、指标、营收拆分等数值数据（quantitative/numeric）。
- `valuz-search` (Valuz Search MCP) — 财报、公告、研报、纪要、电话会、新闻检索（qualitative/text）。

> **代码格式**：`valuz-stock` 用裸代码（AAPL / 00700 / 600519）；`valuz-search` 用 `market:ticker`（US:AAPL / HK:00700 / SH:600519）。

> 取数原则：用 `income_statement` / `balance_sheet` / `cashflow_statement` / `stock_quote` / `ohlcv` / `revenue_breakdown`（valuz-stock）取财务与行情数据；用 `earnings_search` / `conferences_search` / `reports_search` / `news_search` / `filings_search`（valuz-search）取财报、纪要、研报、公告。

```text
income_statement(symbol, period="annual")    -> Historical P and L       (valuz-stock)
balance_sheet(symbol, period="annual")       -> Historical BS            (valuz-stock)
cashflow_statement(symbol, period="annual")  -> Historical CF            (valuz-stock)
stock_quote(symbol)                          -> Market cap, price        (valuz-stock)
revenue_breakdown(symbol)                    -> Revenue drivers          (valuz-stock)
reports_search(query=..., symbols=[...])     -> Research / guidance      (valuz-search)
```

## Key differences across markets

DCF conventions vary by the stock's home market — pick parameters per the standard
that applies to the ticker (US / HK / A-shares / other), not a single fixed market.

| Parameter | US DCF Convention | A-share DCF Convention |
|-----------|-------------------|---------------------|
| Risk-free rate | US 10Y Treasury | China 10Y CGB (国债收益率, ~2.5-3.5%) |
| Equity risk premium | ~5-6% (historical US) | ~6-8% (China A-share ERP) |
| Tax rate | US corporate 21% | China corporate 25% (高新技术企业 15%) |
| Terminal growth | US GDP growth (~2%) | China GDP growth (~4-5%) |
| Currency | USD | CNY |
| Reporting standard | US GAAP / IFRS | CAS (中国会计准则) |

> 实际建模时，按标的适用准则（US GAAP / IFRS / CAS）读取报表口径，营收等按当地准则口径（如增值税处理差异）处理。

## Workflow

### Step 1: Pull financials

用 `income_statement` / `balance_sheet` / `cashflow_statement`（valuz-stock，`period="annual"`，`limit` 取近 5 年；季度建基期改 `period="quarterly"`）拉历史三表：

```text
income_statement(symbol, period="annual", limit=5)   → last 5 years   (valuz-stock)
balance_sheet(symbol, period="annual", limit=5)                       (valuz-stock)
cashflow_statement(symbol, period="annual", limit=5)                  (valuz-stock)
```

> `valuz-stock` 用裸代码：美股 `AAPL`、港股 `00700`、A 股 `600519` —— 不带 `.HK` / `.SH` 后缀，也不硬限制为 .SH/.SZ。营收驱动可叠加 `revenue_breakdown(symbol)`。

### Step 2: Get market data

```text
stock_quote(symbol)                  → price, market cap, PE, PB       (valuz-stock)
ohlcv(symbol)                        → 个股历史价格序列（算 β / 收益率） (valuz-stock)
index_quote(index_symbol)            → benchmark 行情（β 估计基准）     (valuz-stock)
```

用 `ohlcv`（valuz-stock，个股历史价格序列）与 `index_quote`（valuz-stock，基准指数）做 β 回归；基准指数按标的所在市场选（S&P 500 美股、Hang Seng 港股、上证指数 A 股）。

### Step 3: Build projections

- Project revenue using historical growth rates adjusted for the relevant market's macro outlook（必要时用 `revenue_breakdown`（valuz-stock）分部驱动）
- Assume 65-75% operating margin for high-margin sectors (e.g. 白酒 / premium brands)
- Assume 15-25% operating margin for manufacturing
- CapEx as % of revenue: check historical from `cashflow_statement`（valuz-stock）
- 用 `earnings_search` / `conferences_search` / `reports_search`（valuz-search，`query` 必填，`symbols=["US:AAPL"]` 等限定标的）拉财报、业绩电话会纪要与机构研报，校验前瞻指引（forward guidance）与增长假设

### Step 4: Compute WACC

```
WACC = E/(D+E) * Ke + D/(D+E) * Kd * (1 - tax_rate)

Ke = Rf + β * ERP
  Rf   = 对应市场国债（如美债/中债）10Y yield —— 无专门国债工具，用 reports_search / news_search（valuz-search，query 必填，如 query="US 10Y Treasury yield"）查最新值，或作为分析师输入
  β    = regression on the market's benchmark index returns（ohlcv vs index_quote, valuz-stock）（or use comparable firm beta）
  ERP  = market-specific equity risk premium（同样可用 reports_search / news_search 佐证，e.g. ~5-6% US, ~6-8% A-share）

Kd   = market 5Y corporate bond yield + credit spread（用 reports_search / news_search 查对应市场公司债收益率）
```

### Step 5: Terminal value

```
Terminal Value = FCF_(n+1) / (WACC - g)
g = mature-company nominal GDP growth for the ticker's market (e.g. ~2% US, ~3-4% A-share)
```

## Notes

- Fiscal year-end varies by company/market (December 31 for most A-share/US names) — confirm per ticker
- Flow/sentiment indicators can be used as context (e.g. 北向资金 / Northbound flow for A-shares)
- 商誉 (goodwill) impairments are common in M&A — flag if goodwill > 30% of equity
- Watch reported units (e.g. 千元 thousands vs 元, or thousands/millions in USD statements) — check the unit
