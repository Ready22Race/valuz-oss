---
name: audit-xls
description: Audit financial models and Excel workbooks for global equity analysis. Covers US / HK / A-share and other markets, cross-checking model numbers against valuz-stock financial statements (income_statement / balance_sheet / cashflow_statement / stock_quote) and tracing source disclosures via valuz-search (earnings_search / filings_search + document_raw_content). Triggers on "模型审计", "财务模型核查", "audit model", "audit xlsx", "模型QC", or "check model [company]".
---

# audit-xls

## Purpose

Audit **financial models** — comprehensive quality checks for equity financial models across **全球股票市场（美股/港股/A 股为主，兼顾其他市场）**.

## Data Sources

**代码格式约定（首次取数务必区分）**：valuz-stock 用**裸代码**（US `AAPL`、HK `00700`、A 股 `600519`）；valuz-search 用 `market:ticker`（US `US:AAPL`、HK `HK:00700`、A 股 `SH:600519`）。

### valuz-stock — 取数核对用的财务/行情数值

把模型里的财务数核回源（period 取 `annual`/`quarterly`，`limit` 控制期数）：

```text
income_statement(symbol, period, limit)     → 利润表 actuals，核对收入/成本/净利
balance_sheet(symbol, period, limit)        → 资产负债表，核对资产/负债/权益
cashflow_statement(symbol, period, limit)   → 现金流量表，核对经营/投资/筹资现金流
stock_quote(symbol)                         → 价格/市值核对（share count × price = market cap）
```

### valuz-search — 财报/公告原文溯源

核对模型中历史数据的科目口径与附注时，先检索文档、再取原文：

```text
earnings_search(query, symbols, num, start_datetime, end_datetime)  → 定位财报/业绩文档
filings_search(query, symbols, num, start_datetime, end_datetime)   → 定位公告/招股书等披露文件
document_raw_content(...)   → 取命中文档的原文（核对口径、附注）
document_fetch(...)         → 取文档结构化内容
```

## Workflow

### Step 1: Structural Audit

**Model structure checklist:**

| Area | Check | Pass Criteria |
|------|-------|--------------|
| Cover page | Company, date, version | Present and accurate |
| Assumptions | All key inputs centralized | No hardcoded values in calc |
| Historicals | All periods populated | 3-5 years |
| Forecast | Explicit forecast period | 3-5 years |
| Valuation | DCF, comps, football field | All methods present |
| Checks | Sum, balance, cross-ref | All checks green |
| Documentation | Methodology notes | Clear and complete |

### Step 2: Formula Audit

**Formula checks:**

| Check | Description | Pass Criteria |
|-------|-------------|--------------|
| No hardcodes | All inputs in assumptions | ✓ |
| Consistent formulas | Same formula across periods | ✓ |
| No circularity (unless intended) | Circular refs flagged | ✓ |
| Error handling | IFERROR used where needed | ✓ |
| Named ranges | Key cells named | ✓ |
| Sheet references | Cross-sheet refs work | ✓ |
| Broken links | No external links or all work | ✓ |

**Common hardcodes to find:**
- Growth rates embedded in formulas
- Multiples typed into cells
- Shares outstanding hardcoded
- Tax rates not in assumptions

### Step 3: Historicals Cross-Check

**Cross-check against source data:** 用 `income_statement`/`balance_sheet`/`cashflow_statement`(valuz-stock) 把表内历史数核回源；对存疑科目用 `earnings_search`/`filings_search` 定位文档、`document_raw_content`(valuz-search) 取原文核对口径。

| Line Item | Model | income_statement / balance_sheet / cashflow_statement (valuz-stock) | Difference | Explanation |
|-----------|-------|----------------|------------|-------------|
| 营业收入 / Revenue | | | | |
| 营业成本 / COGS | | | | |
| 归母净利润 / Net income | | | | |
| 总资产 / Total assets | | | | |
| 净资产 / Equity | | | | |
| 经营现金流 / Operating cash flow | | | | |

**Cross-check tolerance:**
- Revenue, profit: ±2%
- Balance sheet: ±1%
- Cash flow: ±3% (timing differences)

### Step 4: Accounting Compliance Check

按标的适用准则（US GAAP / IFRS / CAS）合规检查：

| Check | Description |
|-------|-------------|
| Revenue recognition | 按当地准则口径确认收入（如增值税/销售税口径） |
| R&D treatment | Expensed vs capitalized 按适用准则处理 |
| Tax calculation | 按当地适用税率正确计算 |
| Minority interest | Separated from parent equity |
| Government grants | Correct classification |
| Contract liabilities / 合同负债 | Recognized appropriately |
| Credit impairment / 信用减值损失 | 按适用准则减值模型应用 |

### Step 5: Balance Sheet Integrity

**BS checks:**

| Check | Formula | Pass |
|-------|---------|------|
| BS balances | Assets = L + E | ✓ |
| Cash flow ties | Ending cash = Beginning + Net CF | ✓ |
| Debt schedule | Short + Long = Total debt | ✓ |
| Share count | Shares × Price = Market cap（对回 `stock_quote`,valuz-stock） | ✓ |
| Minority interest | Correct % applied | ✓ |

### Step 6: Forecast Logic Check

**Forecast quality:**

| Check | Description | Pass |
|-------|-------------|------|
| Growth reasonable | Within industry range | ✓ |
| Margins stable | No unexplained jumps | ✓ |
| Working capital | Trends with revenue | ✓ |
| CapEx | Consistent with depreciation | ✓ |
| Debt service | Can be serviced from FCF | ✓ |
| Dividend | Consistent with policy | ✓ |

**Growth rate benchmarks:**

| Metric | Conservative | Base | Optimistic |
|--------|-------------|------|-----------|
| Revenue growth | 5-10% | 10-20% | 20-30% |
| Margin expansion | 0 ppts | 0-2 ppts | 2-5 ppts |
| Tax rate | 按当地法定税率 | 按当地法定税率 | 按适用优惠税率（如有） |

### Step 7: Valuation Check

**Valuation audit:**

| Method | Check | Pass |
|--------|-------|------|
| DCF | WACC reasonable (6-10%) | ✓ |
| DCF | Terminal growth < GDP growth | ✓ |
| DCF | FCF positive in base case | ✓ |
| Comps | Peer group appropriate | ✓ |
| Comps | Multiples reasonable | ✓ |
| Football field | Min/median/max consistent | ✓ |

**WACC components:**

| Component | Typical Range |
|-----------|--------------|
| Risk-free rate (10Y govt bond, 按标的市场) | 按当地市场利率 |
| Equity risk premium | 5-8% |
| Cost of debt | 按当地融资成本 |
| Tax rate | 按当地适用税率 |

### Step 8: Sensitivity & Scenario Check

**Sensitivity audit:**

| Check | Description |
|-------|-------------|
| Sensitivity tables | Key variables tested |
| Scenarios | Base / Bull / Bear |
| Tornado chart | Key drivers identified |
| Monte Carlo (if used) | Assumptions reasonable |

### Step 9: Common Model Errors

**Error checklist:**

| Error | Detection | Fix |
|-------|-----------|-----|
| 收入口径错误 | 未按当地准则口径处理 | Apply local-standard revenue basis |
| 税率错误 | Wrong rate applied | Verify applicable tax rate |
| 单位错误 | 按当地常用单位不一致 | Standardize to a consistent unit |
| 季度加总错误 | Q+Q ≠ Annual | Check sum |
| 增长率计算 | Wrong base period | Verify formula |
| 少数股东损益 | Missing | Add if applicable |
| 折旧年限 | Wrong | Check FA notes |

### Step 10: Audit Report

**Standard audit output:**

```
【模型审计报告】[Company] [Date]

一、结构检查
   [Structure assessment]

二、公式检查
   [Formula audit results]

三、历史数据核对
   [Cross-check results]

四、准则合规检查（US GAAP / IFRS / CAS）
   [Accounting compliance]

五、预测逻辑检查
   [Forecast quality]

六、估值检查
   [Valuation audit]

七、发现的问题
   [List of issues with severity]

八、建议修复
   [Recommended fixes]

总体评价: [Pass / Pass with conditions / Fail]
```

## Quality Checks

Before completing:
- [ ] All structural checks passed
- [ ] No hardcodes found
- [ ] Historicals cross-checked
- [ ] Accounting-standard compliant (US GAAP / IFRS / CAS as applicable)
- [ ] BS/CF integrity verified
- [ ] Forecast logic sound
- [ ] Valuation reasonable
- [ ] All issues documented
