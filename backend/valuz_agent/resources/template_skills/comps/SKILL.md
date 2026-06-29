---
name: comps
description: Comparable company analysis for global equities (focus US / HK / A-shares, also other markets). Uses the Valuz Quotes MCP (valuz-stock) and Valuz Search MCP (valuz-search) to build cross-market peer groups, pull financial data, compute valuation multiples (PE, PB, PS), and assess relative value within an industry sector.
---

# comps

## Data Sources

全球股票市场（美股/港股/A 股为主，兼顾其他市场）的可比公司分析使用两个 Valuz 连接器：

> **代码格式（首次取数务必注意）**：`valuz-stock` 用**裸代码**（`AAPL` / `00700` / `600519`）；`valuz-search` 用 `market:ticker`（`US:AAPL` / `HK:00700` / `SH:600519`）。

### `valuz-stock` — Valuz Stock MCP
行情、财务报表、估值因子、行业成分。可比分析常用：

```text
industry_constituents(...)                       → 同业（行业成分股）              # 选同业集
index_constituents(...)                          → 指数成分股（备选同业来源）
company_overview(symbol)                         → 公司画像、规模、业务描述
stock_quote(symbol)                              → 价格、市值
factors_compute(symbols=[...], ...)              → PE()/PB()/PS()/ROE()/EPS() 估值倍数
factors(symbol=...)                              → 单票现成因子值
income_statement(symbol, period="annual")        → 营收、净利润（财务口径）
balance_sheet(symbol, period="annual")           → 账面价值、负债（财务口径）
```

### `valuz-search` — Valuz Search MCP
财报、公告、研报、纪要、电话会检索。可比分析主要用 `reports_search` 取行业研报做定性对照。

```text
reports_search(query=..., symbols=["US:AAPL", ...])   → 行业研报 / 同业定性对照
comprehensive_search(query=...)                       → 综合检索（财报/纪要/公告/新闻）
```

> 取数原则：用 `valuz-stock` 取行情/财务/倍数与同业成分，用 `valuz-search`（`reports_search`）取定性研报对照。

---

# comps

## Workflow

### 1. Define the peer group

Start with the target stock, then use `industry_constituents`（valuz-stock，裸代码如 `600519`）to retrieve industry peers for that sector — or `index_constituents`（valuz-stock）when the peer set is better anchored to an index. Peer sets should be **cross-market** — a peer group can mix US, HK, and A-share listings within the same industry. Common sectors and example leaders:

| Industry | Example Leaders |
|----------|-----------------|
| 白酒 / Spirits | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 半导体 / Semiconductors | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 电池 / Batteries | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 银行 / Banks | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 证券 / Brokers | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 保险 / Insurance | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 医疗器械 / Medical Devices | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 光伏设备 / Solar | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 汽车整车 / Autos | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |
| 软件开发 / Software | {{SECTOR_LEADER}}, {{CHALLENGER_1}}, {{CHALLENGER_2}} |

Tickers follow each market's convention — US (`AAPL`), HK (`0700.HK`), A-share (`600519.SH`).

### 2. Pull financial data for each peer

代码格式提醒：valuz-stock 用裸代码（`AAPL` / `00700` / `600519`），valuz-search 用 `market:ticker`（`US:AAPL` / `HK:00700` / `SH:600519`）。

```text
For the peer set as a whole:
  factors_compute(symbols=[...], ...)  → PE()/PB()/PS()/ROE()/EPS() 倍数（批量）   (valuz-stock)

For each ticker in the peer set:
  stock_quote(symbol)                       → price, market cap                    (valuz-stock)
  company_overview(symbol)                  → business description, profile         (valuz-stock)
  income_statement(symbol, period="annual") → revenue, net income (财务口径)        (valuz-stock)
  balance_sheet(symbol, period="annual")    → book value, debt (财务口径)           (valuz-stock)
  reports_search(query="...", symbols=["US:AAPL", ...]) → qualitative color (行业研报) (valuz-search)
```

### 3. Compute standard multiples

倍数优先用 `factors_compute`（valuz-stock）批量计算，因子语法用 `PE()` / `PB()` / `PS()` / `ROE()` / `EPS()`；无现成因子时再用 `stock_quote` + 报表口径自行换算。

| Multiple | Formula | valuz-stock source |
|----------|---------|--------------------|
| PE (TTM) | Price / EPS TTM | `factors_compute` → `PE()`（或 `PE_TTM()`） |
| PB | Price / Book Value per share | `factors_compute` → `PB()` |
| PS (TTM) | Market Cap / Revenue TTM | `factors_compute` → `PS()`（或由 `stock_quote` 市值 + `income_statement` 营收换算） |
| EV/EBITDA | Enterprise Value / EBITDA | 由 `stock_quote` 市值 + `balance_sheet` 负债/现金换算 |
| ROE | Net Income / Equity | `factors_compute` → `ROE()` |
| Dividend Yield | DPS / Price | `stock_quote` → dividend yield |

### 4. Present the comps table

Sort by market cap (largest first). Flag outliers (>2 standard deviations from mean). Include:
- Ticker, company name, price
- Market cap (in the listing's local currency, e.g. USD / HKD / CNY)
- PE, PB, PS
- Revenue growth %, Net margin %
- 52-week high/low

### 5. Relative value assessment

- If target's PE is >1 std dev above peer mean → potentially overvalued
- If target's PE is >1 std dev below peer mean → potentially undervalued
- Flag companies with negative earnings separately
- Compare PEG ratios (PE / growth rate) when growth data is available

## Notes

- Financial data may be reported under US GAAP / IFRS / CAS depending on the listing — normalize when comparing across markets (按当地准则口径)
- Revenue is reported 按当地准则口径; reconcile definitions before comparing cross-market peers
- Some markets apply daily price-limit mechanisms (e.g. A-shares ±10% main board / ±20% ChiNext/STAR) — note these when interpreting single-day moves
- For A-share names, market cap may be quoted as 流通市值 (circulating) or 总市值 (total) — confirm which the user wants
- For cross-market comps (e.g. A-share vs HK-listed dual-listings), note that A-shares typically trade at a premium
