# Valuz Marketplace Product Requirements

> Date: 2026-07-07
> Worktree: `/Users/zhourongyu/Dev/valuz-oss-skillhub-marketplace`
> Branch: `codex/skillhub-marketplace`

## Product Positioning

Valuz should ship marketplace-backed import flows, but not as a standalone
top-level sidebar module. The marketplace is a Valuz-owned discovery and import
surface for the objects in our own product model:

- **Skills** are agent equipment.
- **Agents** are official or curated expert workers.
- **Agent Teams** are curated scenario packages made of multiple Agents.

The new product decision is:

| Market area | Primary supply | Product rule |
|---|---|---|
| `Skills` | SkillHub skills + Valuz official skills | Can be supplied at scale. SkillHub is the main external source. |
| `Agents` | Valuz official curated templates | Valuz defines the Agent identity, prompt, runtime, skills, and install behavior. |
| `Agent Teams` | Valuz official curated templates | Valuz defines the team roles, lead/member relationship, and workflow. |
| SkillHub `skillsets` / "expert packs" | Candidate material only | Use as source material for Valuz curation; do not expose them directly as final Agents or Teams. |

In short:

> SkillHub provides Skill supply and workflow material. Valuz defines the final
> Agents and Agent Teams.

## Non-Goals

The marketplace should avoid these concepts in the primary UI:

- No top-level `Recommended` tab.
- No top-level `Agent Team` tab.
- No top-level `Suite` concept.
- No MCP marketplace in this phase.
- Do not bulk mirror all SkillHub expert packs as Agent Teams.

If curation is needed later, it should be shown as ordering, pinned rows, or
small sections inside `Agents` or `Skills`, not as a third marketplace module.

## Core IA

The market surface has only two primary tabs:

| Tab | Contains | Notes |
|---|---|---|
| `Agents` | Valuz official/curated Agent Teams | Current phase leads with Teams only; single Agents stay out of the browse UI until the Team model is stable. |
| `Skills` | SkillHub skills and Valuz official skills | Uses SkillHub categories/subcategories where available. |

Entry points:

| Entry | Opens |
|---|---|
| Agent Library visible header CTA | `Marketplace > Agents` |
| Skill Library visible header CTA | `Marketplace > Skills` |
| Onboarding / first-run scenario suggestion | `Marketplace > Agents`, optionally highlighting a relevant Agent Team |

The `/marketplace` route remains an internal shared browse surface, but it must
not be shown as an independent primary sidebar tab. Users should encounter
marketplace supply in the resource module where they already are: Agent Teams in
the Agent Library, and SkillHub skills in the Skill Library. The CTA should be a
visible text button in the page header, not only an option hidden under a `+`
menu.

## Asset Definitions

| Asset type | User meaning | Install result | Source strategy |
|---|---|---|---|
| Skill | Reusable method, script, guide, or tool playbook that equips an Agent | Added to Skill Library; can be attached to Agents | SkillHub + Valuz official skills |
| Agent | One expert worker with name, role, instructions, runtime/model, skills, and connectors | Added to Agent Library | Valuz official curated templates |
| Agent Team | A scenario package made of multiple Agents with defined responsibilities | Adds multiple Agents into the library; optionally deploys them to a Project | Valuz official curated templates |

Current UI rule: the Agents tab shows Agent Teams only. Curated single Agents
can remain as an internal template resource/API capability, but they should not
appear as a lower browse section in the marketplace yet.

Agent Team execution rule: the Marketplace preview can explain the collaboration
model, but the actual coordination protocol must live in the installed Lead
Agent's instructions. Importing a Team creates multiple Agents in the library;
when those Agents are assigned to the same Project, the Lead is responsible for
clarifying the goal, routing work to member Agents, reconciling their outputs,
and producing the final response or project artifact. Therefore every Team
import must enrich the Lead Agent instructions with the Team workflow and member
handoff model.

Agent Team activation rule: installing a Team is not the end of the workflow.
The useful product action is to summon that Team into a Project. After a Team
install, the user should see a dedicated next-step dialog state where they can
choose an existing Project, create a new Project from a user-selected local
directory, or skip for now. Activation deploys every Team member into that
Project and navigates to the Project home. It must not create an empty
conversation automatically; the user starts the Lead conversation from the
Project when they are ready. To make that entry feel ready-to-use, the Project
home composer should default to the Team Lead after activation.

Agent Team installation feedback rule: Team installs may take noticeably longer
than single Agent installs because SkillHub-backed dependencies are downloaded
and registered before the Agents are created. The install dialog must therefore
show a clear in-progress state with the estimated member/skill workload and a
stage indicator (preparing dependencies, downloading/registering skills,
creating Agents, finishing) so the user understands that the app is working and
may need a little time.

## SkillHub Fit

SkillHub has two relevant data families:

### SkillHub Skills

SkillHub `skills` can directly power the `Skills` tab.

| SkillHub field | Valuz usage |
|---|---|
| `category`, `subCategories` | Category rail and subcategory chips |
| `slug`, `name`, `description_zh`, `iconUrl` | Skill card content |
| `downloads`, `stars`, `version` | Popularity and version metadata |
| `labels.requires_api_key` | Runtime setup/cost warning |
| `source`, `verified`, `ownerName` | Trust/source badges |
| detail `securityReports` | Import preview safety section |
| detail `evaluation` / TRACE report | Import preview quality section |
| files `path`, `sha256`, `size` | Pre-install file preview |
| download endpoint | Existing URL/archive import pipeline |

Required SkillHub APIs:

| API | Usage |
|---|---|
| `GET https://api.skillhub.cn/api/v1/categories` | Category list |
| `GET https://api.skillhub.cn/api/skills` | Skill catalog |
| `GET https://api.skillhub.cn/api/v1/skills/{slug}` | Detail preview |
| `GET https://api.skillhub.cn/api/v1/skills/{slug}/files` | File preview |
| `GET https://api.skillhub.cn/api/v1/download?slug={slug}` | Import archive |

### SkillHub Skillsets / Expert Packs

SkillHub `skillsets` are not Agent definitions. They are workflow recipes made
of a title, summary, content, scene, subscene, and skill slugs.

Observed fields:

| Field | Meaning |
|---|---|
| `displayName` | Workflow or expert-pack name |
| `summary` | Scenario description |
| `content` | Step-by-step workflow text |
| `skillSlugs` | Skills used by the workflow |
| `skillCount` | Number of skills |
| `scene`, `subScene` | Domain classification |

Valuz should treat these as a **candidate template pool**, not as market items.

## Skillset To Agent/Team Conversion

The conversion is a Valuz curation process, not a direct field mapping.

| SkillHub expert-pack shape | Valuz decision |
|---|---|
| One clear expert persona can complete the workflow | Convert to one `Agent` template |
| Workflow has multiple independent roles or handoffs | Convert to one `Agent Team` template |
| Workflow is only a loose bundle of skills | Keep as material; do not publish to Agents |
| Workflow requires high-risk external credentials or unclear paid services | Hold for manual review |
| Workflow domain is legally/medically/financially sensitive | Publish only if Valuz adds explicit disclaimers, guardrails, and quality review |

Conversion must add fields SkillHub does not provide:

- Agent name and avatar.
- Role/persona.
- System prompt / instructions.
- Default runtime/model/effort.
- Bound skills.
- Connector requirements.
- API key and third-party cost warnings.
- Install target.
- For Agent Teams: member list, lead Agent, handoff logic, and optional Project deployment behavior.

Example:

| SkillHub expert pack | Better Valuz result |
|---|---|
| `投研报告` with 6 finance skills | Single Agent: `投研报告 Agent` |
| `自动化测试` with TDD, test generation, E2E, API testing, QA plan | Agent Team: test strategist, automation executor, API tester, QA reporter |
| `腾讯云专家` with cloud ops, COS, DNSPod, ASR/OCR | Single Agent first; consider Team only if we split DevOps, storage, app deploy, and media processing roles |

## Official Content Strategy

Valuz official content should be first-class in the same market:

| Official source | Market placement | Notes |
|---|---|---|
| Official skills bundled with Valuz | Not shown as market items | Ship with Valuz or install with official Teams. |
| Official single-agent templates | Hidden in current marketplace UI | Created and maintained by Valuz, but not browsed until the Team model is stable. |
| Official Agent Teams | `Agents` tab | High-confidence scenario packages, shown as the primary browse object. |
| SkillHub-informed Agent/Team ideas | Agent Teams in `Agents` tab | Internal reference only. Once published, they are Valuz official curated templates. |

The investment research Team is a reserved `Valuz Official` Team because it
uses Valuz's own specialized research tools. SkillHub finance expert packs
should be treated as reference material or future add-ons, not as a competing
SkillHub-curated investment Team.

This means the UI does not need separate tabs or source filters for Agents.
All Agent and Agent Team cards are Valuz official curated content. SkillHub
provenance must not appear as a user-facing Agent source.

The Skills tab is the only place where SkillHub appears as a source.

Agent Team categories should be product-facing shelves derived from SkillHub
expert-pack scenes, not raw API scene names. The marketplace architecture uses:

| Category | Teams |
|---|---|
| 产品设计 | 产品战略团队、设计原型团队 |
| 技术工程 | 软件开发、QA 测试 |
| 金融投资 | 行业分析、产业链追踪 |
| 营销增长 | 竞品情报团队、内容增长团队、活动策划团队 |
| 内容创作 | 视频制作团队、短视频增长团队 |
| 法务安全 | 合同审查团队、合规审查团队 |
| 教育学术 | 学术研究团队、课程设计团队 |
| 运营人力 | 招聘评估团队 |
| 特色分类 | 健康报告解读、国学玄学、塔罗星座 |

Rollout rule:

- The category architecture can be defined up front.
- The first visible catalog should add only one representative Team per
  category, prioritizing clarity, distinctiveness, and low setup friction.
- 金融投资 can carry a second seed Team when it is clearly differentiated from
  行业分析. `产业链追踪` qualifies because it starts from a theme and works
  upstream to supply-chain bottlenecks, rather than building a broad industry
  research frame.
- The remaining Teams stay as curated candidates and can be added gradually
  after product/design review.

First visible seed catalog:

| Category | Seed Team | Why first |
|---|---|---|
| 产品设计 | 产品策略 | Most common entry point: turns fuzzy ideas into requirements and roadmap. |
| 技术工程 | 软件开发 | Core software development workflow; QA can follow as the second engineering Team. |
| 金融投资 | 行业分析、产业链追踪 | Keep 行业分析 as the flagship Valuz research Team, and add 产业链追踪 as a differentiated theme-to-bottleneck workflow. |
| 营销增长 | 竞品情报 | Broadly useful, low setup, clear business outcome. |
| 内容创作 | 小红书内容创作 | Keep the bundled Xiaohongshu note workflow as the first content-creation seed; postpone generic video production until the workflow is clearer. |
| 法务安全 | 合同审核 | Concrete workflow and easier to understand than broad compliance. |
| 教育学术 | 学术研究、课程设计 | Keep 学术研究 for papers/literature work, and add 课程设计 for turning a topic into syllabus, sessions, exercises, and facilitator notes. |
| 运营人力 | 招聘评估 | Single clear HR workflow with obvious multi-agent handoff. |
| 特色分类 | 国学解读 | Most distinctive category signal; position as culture/entertainment. |

Naming and instruction polish:

- Marketplace Team names should use plain scenario names, not stylized packaging.
  Avoid repeated `xxx Team` and avoid terms such as 工作室、作战组、指挥室、
  工作台、解读馆 on visible cards.
- Names should communicate the user task directly, such as 产品策略、行业分析、
  合同审核、小红书内容创作.
- Card descriptions should be short and outcome-first: what the Team turns the
  user's input into, and when to use it.
- Investment Team names should describe the research scene directly. For
  Serenity-inspired supply-chain work, use `产业链追踪` rather than naming the
  author, returns, or a stylized room / studio. The card should say it traces an
  investment theme upstream to find key nodes, bottlenecks, catalysts, and
  invalidation conditions. Any mention of Serenity's claimed track record must
  be treated as method background only (`作者自述 / 未经独立审计`) and should not
  appear as card copy or proof of expected performance.
- Education/academic Team names should map to user tasks. Keep `课程设计`
  instead of `培训课程`; it is clearer because the Team turns a topic, audience,
  and duration into a course outline, session plan, exercises, and facilitator
  notes. Do not show `统计分析` in this category for now; users perceive it as a
  data-analysis Team. Do not show `教学材料` until the product explicitly targets
  teachers with a separate teaching-material workflow.
- The first Agent in each Team is the Lead. The Lead instruction owns intake,
  task decomposition, member handoff, synthesis, and final delivery.
- Member Agent instructions should not be one-line role blurbs. Each member
  instruction should include:
  1. The member's responsibility in the Team workflow.
  2. The expected inputs from the Lead or upstream members.
  3. The structured output it must return to the project.
  4. A boundary or quality guardrail, especially for legal, finance, health,
     hiring, and culture/entertainment scenarios.
- Team metadata can be installed into the Agent library, but the real product
  value starts after the Team is assigned to a project and the user begins the
  Lead conversation. Therefore, all Team instructions should assume the Agents
  are collaborating inside a project workspace.

## UI Requirements

### Overall layout

- Full-screen marketplace page, not a small modal.
- Keep the existing Valuz navigation and visual density.
- Two primary tabs only: `Agents`, `Skills`.
- Import preview opens in a modal from any card.
- The browse page should stay spacious; avoid a permanent right detail panel.

### Agents tab

The `Agents` tab should communicate that Agent Teams are Valuz curated and are
built from multiple cooperating Agents.

Recommended layout:

1. Main grid: curated Agent Teams.
2. Category filters based on SkillHub expert-pack scenes.
3. Import preview shows Team members, bound skills, setup requirements, and install target.

Do not force users to switch between `Agent Team` and `Single Agent`. In the
current phase, hide the single-Agent browse section entirely.

### Skills tab

The `Skills` tab should behave like a large skill catalog:

1. Category rail from SkillHub categories.
2. Subcategory chips from SkillHub `subCategories`.
3. Source filter: `SkillHub`, `Valuz Official`.
4. Search by skill name, description, tag, category.
5. Import preview modal with files, version, owner, safety, and cost flags.

### Import preview modal

All cards open the same preview pattern, but content differs by type.

| Type | Preview should show |
|---|---|
| Skill | Source, owner, version, TRACE quality report, security reports, compact file structure, API-key/cost flags, install target |
| Agent | Role, instructions summary, bound skills, connector requirements, runtime/model defaults |
| Agent Team | Collaboration model, ordered workflow, member Agents, lead/member roles, included skills, optional deploy-to-project behavior |

Agent Team preview must explain the Team as a workflow, not just a roster. The
first screen should answer:

1. How the members collaborate.
2. What the step-by-step flow is.
3. Which members participate and what each one is responsible for.

Required Team preview sections:

| Section | Contents |
|---|---|
| 协作方式 | One short paragraph explaining how the lead/member agents hand work off. |
| 工作流程 | Ordered steps derived from the Team member order and responsibilities. |
| 团队成员 | Member list with Lead/Member labels and skill count. |

## Backend Requirements

The frontend should not call SkillHub directly. Backend should expose a Valuz
marketplace catalog API that normalizes all sources.

Recommended endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /v1/marketplace/items` | List normalized market items |
| `GET /v1/marketplace/items/{id}` | Detail and import preview |
| `POST /v1/marketplace/items/{id}:install` | Confirm import/install |
| `GET /v1/marketplace/categories` | Normalized categories and counts |

Normalized item shape:

| Field | Meaning |
|---|---|
| `id` | Stable Valuz market item id |
| `type` | `skill`, `agent_template`, or `agent_team_template` |
| `source` | `skillhub` for skills, `valuz_official` for Agents and Agent Teams |
| `source_ref` | SkillHub slug, official pack id, or internal template id |
| `title`, `description`, `icon` | Card content |
| `category`, `subcategories` | Browse filters |
| `badges` | Source, trust, cost, setup labels |
| `stats` | Downloads/stars/install count where available |
| `install_target` | Skill Library, Agent Library, or Agent Library + Project |

## Safety And Cost Rules

SkillHub installation itself should be presented as free unless the API later
returns paid metadata. Runtime cost still needs explicit flags.

| Flag | Source | UI copy |
|---|---|---|
| Free install | Default for public SkillHub zip download | `Free to install` |
| Requires API key | `labels.requires_api_key === "true"` or detected docs | `Needs API key` |
| Third-party cost | Cloud/service/API keywords or metadata | `May incur third-party cost` |
| Security reviewed | SkillHub security report status | `Reviewed by SkillHub` |
| Community source | Missing verified/official status | `Community source` |
| Valuz reviewed | Official or curated template | `Reviewed by Valuz` |

Install confirmation must show file list, origin URL, version, and risk flags.
Scripts are allowed only after preview. Destructive or credential-seeking files
should be highlighted before import.

## Phased Delivery

| Phase | Scope | Done when |
|---|---|---|
| MVP 1 | Marketplace shell + `Skills` tab from SkillHub | User can browse SkillHub skills and preview one skill |
| MVP 2 | Skill import confirmation | User can import a SkillHub skill into Skill Library through existing archive/URL pipeline |
| MVP 3 | Team-first `Agents` tab | User can browse Valuz-curated Agent Teams by SkillHub expert-pack scene |
| MVP 4 | Team import flow | Importing a Team installs SkillHub skill dependencies and creates its Agents |
| MVP 5 | Official/bundled Team polish | Valuz official Teams such as 投研 and 小红书 content have bundled skills and clear setup notes |
| MVP 6 | SkillHub expert-pack curation pipeline | Internal analysis ranks 70 SkillHub skillsets as Agent, Team, or not suitable |

## Next Document

The concrete analysis of all SkillHub expert packs should live in a separate
curation document, for example:

`docs/plans/2026-07-07-skillhub-skillset-curation.md`

That document should be a table with:

- SkillHub slug.
- Display name.
- Scene/subscene.
- Skill count.
- Recommended Valuz type: `Agent`, `Agent Team`, or `Do not publish`.
- Suggested Valuz name.
- Recommended skills to bind.
- Quality/risk notes.
- Priority: P0/P1/P2/Hold.

Keeping this separate lets UI design proceed from the stable product rules here
while curation analysis evolves independently.
