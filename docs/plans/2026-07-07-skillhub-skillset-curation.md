# SkillHub Expert Pack Curation For Valuz Agents

> Date: 2026-07-07
> Source: `GET https://api.skillhub.cn/api/v1/skillsets?page=1&pageSize=100`
> Observed total: 70 SkillHub expert packs

## Goal

Valuz Marketplace should lead with **Agent Teams**, not a large list of raw
SkillHub expert packs. This document identifies the SkillHub expert packs most
suitable to become Valuz-curated Agent Teams, then adds a smaller set of
distinctive single Agents.

The product model is:

- SkillHub expert packs are the main upstream source for non-official Valuz
  Agent Teams. A SkillHub expert pack already contains a workflow description
  and a group of `skillSlugs`; Valuz converts that structure into multiple
  cooperating Agents because a Team is easier to understand and operate than a
  single all-purpose expert.
- Valuz does not expose "SkillHub curated" as a customer-facing Agent source.
  Agent and Agent Team cards are Valuz-curated market items, with SkillHub
  provenance kept in internal docs/manifests.
- Only two first-wave Teams are truly Valuz official built-ins with bundled
  skills: `Investment Research Team` and `Xiaohongshu Content Team`.
- Other Teams should bind SkillHub skills from their source expert pack
  `skillSlugs`. Importing those Teams should install the required SkillHub
  skills automatically before creating the Agents.

This document is intentionally separate from the product requirements document.
UI can use the stable rules in:

`docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md`

while this curation list can evolve as we inspect quality, safety, and overlap.

## Source Coverage

SkillHub currently exposes 70 expert packs across 14 scenes:

| Scene | Count | Examples |
|---|---:|---|
| `tech` | 6 | 自动化测试, Bug 排查, 代码审查, 代码重构 |
| `marketing` | 6 | 竞品分析, 社媒运营, 广告文案, 用户增长 |
| `legal` | 6 | 合同审查, 合规分析, 诉讼策略 |
| `ecommerce` | 6 | 选品分析, 定价分析, 竞价策略 |
| `mysticism` | 6 | 紫微斗数, 塔罗占卜, 八字命理 |
| `finance` | 5 | 投研报告, 风控评估, 量化回测 |
| `academic` | 5 | 文献综述, 论文检索, 学术写作 |
| `content-creation` | 5 | 网文/小说创作, 剧本创作, 歌词创作 |
| `lifestyle` | 5 | 营养餐规划, 心理咨询陪伴, 健身训练计划 |
| `design` | 4 | PRD 撰写, UI 原型设计, 品牌视觉 |
| `education` | 4 | 培训方案, 教案设计, 题库生成 |
| `healthcare` | 4 | 病历分析, 临床辅助, 健康报告 |
| `hr` | 4 | 简历筛选, 会议纪要, PPT 制作 |
| `media` | 4 | 脚本拆解, 分镜设计, 视频剪辑 |

There is significant overlap inside each scene. Valuz should not mirror this
shape. We should merge overlapping packs into fewer, clearer official Teams.

## Curation Criteria

Team candidates must satisfy most of these:

| Criterion | Why it matters |
|---|---|
| Multi-step workflow | Team value is visible when work moves through stages. |
| Multiple natural roles | The pack should split into lead/member Agents cleanly. |
| Clear business outcome | The user should understand what is installed and what it does. |
| Reusable across many users | Marketplace Teams should be broadly useful, not niche curiosities. |
| Low setup friction | Needs fewer external accounts, credentials, or domain-specific data. |
| Manageable risk | High-stakes medical/legal/financial content needs extra guardrails. |
| Low overlap | Prefer one strong Team per cluster rather than many near-duplicates. |

Priority labels:

| Priority | Meaning |
|---|---|
| `P0 Team` | Strong candidate for the first Agent Team strip. |
| `P1 Team` | Good candidate after P0, or when the product wants this vertical. |
| `Agent` | Better as one curated single Agent than a Team. |
| `Hold` | Useful material but risky, too niche, too overlapping, or not ready. |

## Recommended P0 Agent Teams

These are the best first choices for a marketplace that wants to show the value
of Valuz Agent Teams quickly.

Financial research is intentionally excluded from the SkillHub-curated P0 list.
Valuz will provide an official Investment Research Agent Team with our own
specialized tools. SkillHub finance packs can inform add-ons or individual
Agents, but they should not compete with the official Team in the first market
wave.

### 1. Development Engineering Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub packs | `tech-code-refactoring`, `tech-bug-troubleshooting`, with expansion material from `tech-code-review` |
| Suggested market name | Development Engineering Team |
| Why Team | Feature development, code generation, refactoring, review, and bug fixing are distinct engineering roles. |
| Target users | Developers, product engineers, engineering teams, indie builders |
| Install result | Creates development, refactoring, review, and bug-fix Agents in Agent Library; optional deploy to a project. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Feature Developer Agent | Implements features from requirements and existing code context | `code-fix`, `cody`, `clean-code-review` material |
| Refactoring Agent | Improves structure, readability, and maintainability | `tech-code-refactoring` material |
| Code Review Agent | Reviews PRs for correctness, maintainability, security | `tech-code-review` material |
| Bug Fix Agent | Reproduces, diagnoses, and fixes defects | `debug-pro`, `bug-fixing`, `nexus-error-explain` |

UI card copy:

> Build and improve product code with a coordinated team for feature work,
> refactoring, code review, and bug fixing.

Notes:

- This should be the main engineering Team for implementation work.
- Keep testing as a separate QA Team so users can choose "build" versus
  "verify" clearly.

### 2. QA Testing Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub packs | `tech-test-automation`, with expansion material from `tech-code-review`, `tech-bug-troubleshooting` |
| Suggested market name | QA Testing Team |
| Why Team | Test planning, test case generation, automation, API testing, and regression review are distinct QA roles. |
| Target users | Developers, QA engineers, product teams, indie builders |
| Install result | Creates test strategy, test automation, API test, and regression Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Test Strategist | Defines test scope, risk areas, coverage plan | `superpowers-tdd`, `afrexai-qa-test-plan` |
| Test Case Agent | Generates unit, integration, and acceptance test cases | `test-case-generator`, `test-patterns` |
| Test Automation Agent | Builds automated test scripts and checks | `e2e-testing-patterns`, `api-test-automation` |
| Regression Review Agent | Reviews failures and confirms release readiness | `tech-bug-troubleshooting`, `tech-code-review` material |

UI card copy:

> Plan test coverage, generate cases, automate checks, and review release
> quality before shipping.

Notes:

- This replaces the broader `Engineering Quality Team` name.
- It is easier for users to understand than mixing development and QA into one
  generic engineering-quality card.

### 3. Product Strategy Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub packs | `design-prd-writing`, with expansion material from `marketing-competitor-analysis` |
| Suggested market name | Product Strategy Team |
| Why Team | Product discovery, requirements, PRD, user research, competitor analysis, and roadmap planning are distinct roles. |
| Target users | Founders, PMs, designers, product teams |
| Install result | Creates requirements, user research, competitor analysis, and roadmap Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Product Requirements Agent | Clarifies requirements, EPICs, user stories, acceptance criteria | `requirements-analysis`, `prd`, `prd-writer-pro` |
| User Research Agent | Synthesizes users, scenarios, jobs, and pain points | `prd-reviewer`, `requirements-analysis` material |
| Competitor Analysis Agent | Compares competing products, positioning, and features | `competitive-product-research`, `competitor-analysis-report` |
| Roadmap Planning Agent | Turns strategy into milestones and release scope | `software-manager-skill`, `prd-to-design-doc` |

UI card copy:

> Turn a product idea into requirements, user insight, competitor context, and a
> clear product roadmap.

Notes:

- This mirrors WorkBuddy's product-strategy card and should be separate from
  visual prototype work.
- It is a strong first-wave Team because many users arrive with vague product
  ideas rather than finalized requirements.

### 4. Design Prototype Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub packs | `design-ui-prototype`, `design-interaction-design`, `design-brand-visual` |
| Suggested market name | Design Prototype Team |
| Why Team | Interaction design, UI prototype generation, brand direction, and design review are different jobs. |
| Target users | Founders, PMs, designers, product teams |
| Install result | Creates UX, UI prototype, brand, and design review Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| UX Architect Agent | Turns PRD into IA, flows, page structure | `prd-to-design-doc`, `wireframe` |
| UI Prototype Agent | Produces high-fidelity HTML prototypes | `prd-to-prototype`, `ui-design`, `frontend-design-pro` |
| Brand Visual Agent | Defines visual direction, brand style, and asset language | `brand-cog`, `visual`, `theme-factory` |
| Design Review Agent | Reviews visual hierarchy, usability, accessibility | `prd-reviewer`, `afrexai-ui-design-system` |

UI card copy:

> Move from requirements to interaction flow, visual direction, and clickable UI
> prototype.

Notes:

- This replaces separate first-wave cards for `Brand Visual Studio Team` and
  `UX Research & Interaction Team`.
- Brand and UX remain visible as members/capabilities, but the market card stays
  simpler.

### 5. Competitive Intelligence Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub pack | `marketing-competitor-analysis` |
| Suggested market name | Competitive Intelligence Team |
| Why Team | Market sizing, competitor research, feature/pricing comparison, and monitoring are separable roles. |
| Target users | Founders, marketers, product strategists |
| Install result | Creates research, analysis, reporting, and monitoring Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Market Research Agent | Sizes market and segments opportunities | `market-research`, `market-analysis-cn` |
| Competitor Research Agent | Gathers competitor positioning, pricing, and features | `competitive-product-research`, `competitive-intelligence-market-research` |
| Strategy Analyst Agent | Produces SWOT, positioning, and opportunity analysis | `competitor-analysis-report` |
| Watch Agent | Monitors competitor sites/products/pricing over time | `competitor-watch` |

UI card copy:

> Research competitors, compare product positioning, and generate a structured
> competitive intelligence report.

Notes:

- Good Team candidate because the workflow naturally runs from collection to
  analysis to report.
- This should not be mixed with social media or ad copy in the first version.

### 6. Content Growth Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub packs | `marketing-social-media-operation`, optional material from `marketing-ad-copywriting`, `media-short-video-copy` |
| Suggested market name | Content Growth Team |
| Why Team | Trend discovery, planning, writing, platform optimization, and publishing feedback are separate roles. |
| Target users | Creators, marketers, small businesses |
| Install result | Creates content strategist, writer, platform optimizer, and growth analyst Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Trend Scout Agent | Finds platform trends and content angles | `content-hunter` |
| Content Strategist Agent | Plans themes, cadence, and channel strategy | `social-media-operator`, `newmedia-operations` |
| Copywriting Agent | Writes posts, captions, scripts, and公众号 drafts | `content-writer`, `wechat-mp-writer-skill-mxx` |
| Optimization Agent | Tunes platform fit, hooks, titles, and engagement | `social-media-optimizer`, `media-short-video-copy` material |

UI card copy:

> Turn platform trends into publishable content plans, posts, hooks, and channel
> optimization suggestions.

Notes:

- Good broad-market Team.
- Avoid creating separate first-wave Teams for `广告文案`, `短视频文案`, and
  `社媒运营`; they should be sub-capabilities of one Content Growth Team.

### Reserved Official Slot: Investment Research Team

| Field | Recommendation |
|---|---|
| Priority | `Valuz Official Team`, not SkillHub-curated |
| Primary source | Valuz official investment research tools and templates |
| SkillHub reference material | `finance-investment-research`, `finance-financial-report-analysis`, `finance-quant-backtesting` |
| Suggested market name | Investment Research Team |
| Why official | This is a core Valuz differentiator and should showcase our built-in research tooling, not a generic SkillHub conversion. |
| Target users | Analysts, investors, finance researchers |
| Install result | Creates the official Valuz research team with clear non-advisory disclaimers. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Industry Analyst | Builds industry landscape, competitive structure, comps, and target pools | Valuz bundled skills: `sector-overview`, `competitive-analysis`, `comps`, `idea-generation` |
| Financial Modeler | Builds DCF, three-statement, comparable valuation, and Excel model checks | Valuz bundled skills: `dcf`, `3-statement-model`, `comps`, `audit-xls` |
| Earnings Tracker | Tracks earnings season, variance analysis, model updates, and earnings notes | Valuz bundled skills: `earnings-analysis`, `earnings-preview`, `model-update` |
| Report Writer | Produces initiating coverage, morning notes, and presentation decks | Valuz bundled skills: `initiating-coverage`, `morning-note`, `pptx-author` |

UI card copy:

> Use Valuz's official research tools to move from industry research and
> financial modeling to earnings tracking and investment-report output.

Notes:

- Do not publish a separate SkillHub-curated `Investment Research Team` in the
  first wave.
- Finance packs overlap heavily and should feed the official Team roadmap.
- Keep only differentiated finance singles, such as `Quant Backtesting Agent`,
  if they do not duplicate the official Team.

### Supplemental Finance Team: Risk Control Review Team

| Field | Recommendation |
|---|---|
| Priority | `P1 Team` |
| Primary SkillHub pack | `finance-risk-assessment` |
| Suggested market name | Risk Control Review Team |
| Why Team | The built-in Investment Research Team covers research, modeling, earnings tracking, and report writing. It does not fully cover portfolio risk, stress testing, fraud signals, or risk dashboards. |
| Target users | Analysts, portfolio managers, founders, finance operators |
| Install result | Creates risk modeling, portfolio stress-test, fraud signal, compliance review, and dashboard Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Risk Model Agent | Builds scoring, feature bucketing, and decision-tree style risk checks | `finance-risk-assessment` material |
| Portfolio Stress Agent | Reviews VaR, stress test, drawdown, and scenario exposure | `finance-risk-assessment` material |
| Fraud Signal Agent | Flags accounting quality, cash-flow mismatch, and anomaly signals | `finance-risk-assessment` material |
| Compliance Report Agent | Summarizes risk review and produces a non-advisory report | `finance-risk-assessment` material |

UI card copy:

> Review portfolio risk, stress scenarios, accounting-quality signals, and
> compliance notes before making a finance decision.

Notes:

- This is the best SkillHub finance supplement because it complements the
  official Investment Research Team instead of duplicating it.
- Keep it as a Valuz official curated Team with `Sensitive domain` and finance
  disclaimer badges. SkillHub provenance stays internal.
- `finance-investment-research` and `finance-financial-report-analysis` overlap
  with the official Team and should remain reference material.
- `finance-quant-backtesting` is distinctive, but it needs data/runtime setup;
  keep it as a single Agent or later advanced Team.

### 7. Recruiting Evaluation Team

| Field | Recommendation |
|---|---|
| Priority | `P0 Team` |
| Primary SkillHub pack | `hr-resume-screening` |
| Suggested market name | Recruiting Evaluation Team |
| Why Team | Resume parsing, scoring, interview design, and final recommendation are distinct stages. |
| Target users | Recruiters, founders, team leads |
| Install result | Creates parsing, screening, ranking, and interview Agents. |

Suggested members:

| Agent | Role | Source material |
|---|---|---|
| Resume Parser Agent | Extracts structured candidate data | `resume-parser` |
| Screening Agent | Scores hard/soft fit and ranks candidates | `resume-screening`, `applicant-screening-zh` |
| Interview Designer Agent | Creates evidence-based interview strategy | `interview-designer` |
| Hiring Report Agent | Summarizes recommendations and risks | `resume-screener-pro`, `easy-recruitment` |

UI card copy:

> Screen resumes, rank candidates, design interviews, and produce a hiring
> recommendation pack.

Notes:

- Clear business workflow and easy to understand.
- Must include bias/fairness wording and human decision ownership.

## Recommended P1 Agent Teams

These are good follow-up Teams, but they are either more vertical, more
overlapping, or less urgent than P0.

| Priority | Suggested Team | Source packs | Why | Notes |
|---|---|---|---|---|
| `P1 Team` | Ecommerce Launch Team | `ecommerce-product-selection`, `ecommerce-product-copywriting`, `ecommerce-pricing-analysis`, `ecommerce-bidding-strategy` | Strong merchant workflow: choose product, price it, write listing, launch ads | Good vertical; avoid four separate Teams. |
| `P1 Team` | Campaign Event Team | `marketing-event-planning` | Natural roles: planner, ops/budget, page builder, creative strategist, post-event analyst | Useful for marketing teams. |
| `P1 Team` | Research Writing Team | `academic-literature-review`, `academic-paper-search`, `academic-academic-writing` | Search, summarize, outline, write, cite, revise are distinct roles | Needs citation quality guardrails. |
| `P1 Team` | Video Content Production Team | `media-script-breakdown`, `media-storyboard-design`, `media-video-editing`, optional `media-short-video-copy` | Script, storyboard, prompt generation, editing, and publish-ready short-video copy form one production pipeline | Strong demo potential; do not create a separate Short Video Growth Team in the first catalog. |
| `P1 Team` | Training Program Team | `education-training-program` | Training design, curriculum, workshop agenda, material conversion are separable | Good B2B/HR use case. |

## Expanded Category Shelves

The first hero strip should show the most understandable Team workflows, but it
is too thin for the whole marketplace. The market should also have category
shelves so users see breadth across design, legal, academic, education, media,
healthcare, mysticism, ecommerce, and other distinctive domains.

These shelves can be shown below the hero strip or behind category filters. Each
SkillHub expert-pack category should generally expose only 2-3 Valuz-curated
Teams, even when SkillHub has more raw expert packs in that category. That keeps
the marketplace opinionated while still preserving SkillHub's breadth.

They do not all need to be launch-ready on day one.

| Shelf | Candidate Team | Source packs | Market type | Readiness | Notes |
|---|---|---|---|---|---|
| Legal | Contract Review Team | `legal-contract-review` | Agent Team | `P2 / gated` | Strong workflow: extract, classify, risk mark, missing terms, key dates. Needs legal disclaimer and jurisdiction warning. |
| Legal | Compliance Review Team | `legal-compliance-analysis` | Agent Team | `P2 / gated` | Useful enterprise scenario; requires strong "not legal advice" framing. |
| Academic | Research Writing Team | `academic-literature-review`, `academic-paper-search`, `academic-academic-writing` | Agent Team | `P1` | Strong multi-role team: search, read, synthesize, cite, write, polish. |
| Academic | Statistical Analysis Team | `academic-statistical-analysis` | Agent Team | `P1` | Distinct data-analysis workflow; useful for researchers and analysts. |
| Education | Training Program Team | `education-training-program` | Agent Team | `P1` | B2B-friendly; lower risk than K12 assessment. |
| Education | Teaching Material Team | `education-lesson-planning`, `education-quiz-generation`, `education-student-assessment` | Agent Team | `P2` | Strong teacher workflow, but K12 claims and grading require cautious language. |
| Media | Video Content Production Team | `media-script-breakdown`, `media-storyboard-design`, `media-video-editing`, optional `media-short-video-copy` | Agent Team | `P1` | Very visual and suitable for UI showcase. Short-video hooks, titles, and platform copy should be capabilities, not a separate Team. |
| Finance | Risk Control Review Team | `finance-risk-assessment` | Agent Team | `P1 / sensitive` | Best finance supplement to the official Investment Research Team; focuses on portfolio risk, stress testing, fraud signals, and risk reports. |
| Healthcare | Health Report Interpreter Team | `healthcare-health-report`, `healthcare-medical-record-analysis` | Agent Team | `P2 / gated` | Can focus on explanation and organization, not diagnosis. Needs medical disclaimer. |
| Healthcare | Clinical Support Team | `healthcare-clinical-support` | Agent Team | `Hold / review` | High-stakes clinical decision support; publish only after domain review. |
| Mysticism | Chinese Metaphysics Studio | `mysticism-bazi-analysis`, `mysticism-ziwei-doushu`, `mysticism-yijing-divination`, `mysticism-lunar-almanac` | Agent Team | `P2 / entertainment` | Distinctive and popular, but should be positioned as entertainment/culture. |
| Mysticism | Tarot & Astrology Studio | `mysticism-tarot-divination`, `mysticism-zodiac-fortune` | Agent Team | `P2 / entertainment` | Good consumer novelty; keep out of productivity-first hero strip. |
| Ecommerce | Ecommerce Launch Team | `ecommerce-product-selection`, `ecommerce-product-copywriting`, `ecommerce-pricing-analysis`, `ecommerce-bidding-strategy` | Agent Team | `P1` | Strong business workflow and clear buyer persona. |
| Marketing | Campaign Event Team | `marketing-event-planning` | Agent Team | `P1` | Planning, ops, creative, page generation, ROI review are good Team roles. |

UI implication:

- The top of `Agents` can show 6-8 hero Teams.
- Below that, use category shelves such as `Design`, `Business`, `Education`,
  `Media`, `Specialized`, and `Entertainment`.
- Risk-gated Teams can appear with `Preview`, `Requires review`, or `Coming
  soon` states instead of being hidden entirely.

## Distinctive Single Agents

These are better as individual Agents because the workflow is either focused,
personal, or too narrow to justify a multi-agent team in the first version.

| Priority | Suggested Agent | Source pack | Why Agent, not Team | Notes |
|---|---|---|---|---|
| `Agent P0` | Bug Investigator Agent | `tech-bug-troubleshooting` | One expert can own systematic debugging end to end | Can later be a member of Development Engineering Team. |
| `Agent P0` | Code Review Agent | `tech-code-review` | Clear single expert persona for PR review | Useful for developer onboarding. |
| `Agent P0` | Meeting Notes Agent | `hr-meeting-minutes` | Focused outcome: transcript/notes to decisions and action items | Broad utility, low risk. |
| `Agent P0` | PPT Maker Agent | `hr-ppt-creation` | Focused output: deck generation and polishing | Good visual demo. |
| `Agent P1` | Quant Backtesting Agent | `finance-quant-backtesting` | Distinctive enough to stand apart from the official research Team | Requires data/runtime setup and finance disclaimer. |
| `Agent P1` | PRD Writer Agent | `design-prd-writing` | Can be a single PM expert outside the full product strategy Team | Also member of Product Strategy Team. |
| `Agent P1` | UI Prototype Agent | `design-ui-prototype` | Focused visual/prototype producer | Also member of Design Prototype Team. |
| `Agent P2` | Gaokao Advisor Agent | `academic-gaokao-expert` | Seasonal but concrete, high user value | Region/date sensitive; needs data freshness warnings. |
| `Agent P2` | Contract Drafting Agent | `legal-contract-drafting` | Focused document generation task | Needs legal disclaimer and review before official launch. |
| `Agent P2` | Health Report Explainer Agent | `healthcare-health-report` | Better as explanation/organization than medical advice | Needs medical disclaimer. |
| `Agent P2` | Tarot Reading Agent | `mysticism-tarot-divination` | Distinctive entertainment Agent | Position clearly as entertainment/culture. |

## Hold Or Defer

These packs may be useful, but should not be in the first official Agent/Team
market without additional review.

| Group | Packs | Reason |
|---|---|---|
| Medical/clinical | `healthcare-*` | Do not launch as diagnosis/treatment. Can become explanation/organization Teams after domain review and safety guardrails. |
| Mental health | `lifestyle-mental-counseling` | Crisis/self-harm risk and therapist-substitution risk. |
| Legal | `legal-*` | Can be valuable as contract/compliance productivity, but needs jurisdiction disclaimers and review. |
| Generic finance duplicates | `finance-investment-research`, `finance-financial-report-analysis` | Do not compete with the official Valuz Investment Research Team; use as reference material or future add-ons. |
| Cloud deployment | `tech-tencentcloud-expert` | Defer the `Cloud App Delivery Team` and `Tencent Cloud Ops Agent` until Valuz has a simple deployment flow. Keep cloud skills available in Skills or future setup-heavy Teams. |
| Mysticism | `mysticism-*` | Not core productivity, but distinctive. Put in an entertainment/culture shelf if product wants breadth. |
| Lifestyle health/fitness/nutrition | `lifestyle-*` | Consumer wellness; some high-stakes health overlap. |
| Long-form fiction/lyrics/poetry | `content-creation-*` niche packs | Useful but less aligned to opening Agent Team value; can become specialty Agents later. |
| Education assessment/K12 | `education-student-assessment`, `education-quiz-generation`, `education-lesson-planning` | Good education vertical, but less urgent than broad productivity/engineering/marketing teams. |

## Suggested Marketplace First Wave

For UI design, the first wave should show both depth and breadth:

### Hero Agent Team Strip

1. Product Strategy Team
2. Design Prototype Team
3. Development Engineering Team
4. QA Testing Team
5. Competitive Intelligence Team
6. Content Growth Team
7. Investment Research Team `Valuz Official`
8. Recruiting Evaluation Team

### Category Shelves

| Shelf | Suggested cards |
|---|---|
| Business & Growth | Competitive Intelligence Team, Content Growth Team, Ecommerce Launch Team, Campaign Event Team |
| Product Design | Product Strategy Team, Design Prototype Team |
| Technical Engineering | Development Engineering Team, QA Testing Team |
| Research & Education | Research Writing Team, Statistical Analysis Team, Training Program Team, Teaching Material Team |
| Media Creation | Video Content Production Team |
| Finance & Investment | Investment Research Team `Valuz Official`, Risk Control Review Team |
| Specialized & Sensitive | Contract Review Team, Compliance Review Team, Health Report Interpreter Team |
| Culture & Entertainment | Chinese Metaphysics Studio, Tarot & Astrology Studio |

### Agent list

1. Bug Investigator Agent
2. Code Review Agent
3. Meeting Notes Agent
4. PPT Maker Agent
5. Quant Backtesting Agent
6. PRD Writer Agent
7. UI Prototype Agent

This gives the market a strong opening shape:

- Product strategy and design prototype
- Development engineering and QA testing
- Business/market
- Content/growth
- Finance/research through the official Valuz Team, plus Valuz-curated risk
  control as a second finance card informed by SkillHub reference packs
- HR/recruiting
- Everyday productivity
- Specialty shelves for design, legal, academic, education, media, healthcare,
  and mysticism without forcing all of them into the top hero strip

## How To Represent Curated Sources In UI

Agent and Agent Team cards should not expose SkillHub as a user-facing source.
Use only Valuz ownership and setup/risk badges:

| Badge | Meaning |
|---|---|
| `Valuz Official` | Designed, selected, converted, and maintained by Valuz. |
| `Requires setup` | Needs API key, repo access, cloud account, or data file before use. |
| `Sensitive domain` | Finance, legal, medical, or HR use case with explicit warnings. |

For Agent Teams, do not show raw SkillHub skillset names as the main title.
Show the Valuz team name first. If provenance is needed for internal review,
keep it in the resource notes, not in the customer-facing preview.

Internal provenance example: `marketing-competitor-analysis`,
`competitor-watch`, `competitive-product-research`.

## Conversion Backlog

When converting a candidate into an actual Valuz template, each item still needs
manual product work:

1. Define final Valuz name and one-line promise.
2. Decide Agent versus Agent Team.
3. Define member roles and lead/member relationship for Teams.
4. Write Agent instructions.
5. Bind skill slugs.
6. Identify connector/API-key requirements.
7. Add cost and risk badges.
8. Define install behavior: Agent Library only, or Agent Library + deploy to Project.
9. Create preview content for UI.
10. Run safety review for high-stakes domains.

## Current Recommendation

Do not analyze all 70 packs into market items. Use the 70-pack list as an
internal source pool and launch with a layered catalog:

- 7 SkillHub-informed first-wave Agent Teams plus 2 Valuz official built-in
  Teams: Investment Research and Xiaohongshu Content.
- 2-3 Team candidates per major SkillHub expert-pack category over time, with
  sensitive categories gated or previewed until reviewed.
- 6-7 distinctive single Agents.
- Full SkillHub-powered Skills market.

After deferring `Cloud App Delivery Team`, adding `Risk Control Review Team`,
and merging `Short Video Growth Team` into video/content capabilities, the main
Agent Team catalog should be around 21 Teams. Revisit cloud deployment after
Valuz has a simple deployment setup flow.

This matches the product positioning: Valuz Marketplace is curated and
opinionated for Agents/Teams, while Skills can be broad and ecosystem-driven.
