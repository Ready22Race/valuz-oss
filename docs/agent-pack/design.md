# Agent Pack — 一组 Agent 的导入导出格式

> 定义一套**可分享、可移植、声明式**的 Agent 打包格式（"Agent Pack"），
> 统一三件事:用户导出/导入一组 agent、官方模板的承载结构、单个 agent 的分享。
> 本文档是该格式的设计 spec(单一来源),实现以此为准。

状态: 设计已评审通过(2026-06),待实现。

---

## 1. 为什么做 / 设计目标

把「一组 agent + 它们的装备(skill / connector)」做成一个可落盘、可分享的包,
使得:

- 用户能把自己调好的一组 agent 导出,发给别人;别人导入后**只需授权一下**(填 key / 走 OAuth)即可使用。
- 官方模板不再是 bespoke 的 Python 静态结构,而是**同一种格式**的预置内容 —— 导出格式自己 dogfood 官方内容。
- 单个 agent 的分享是 N=1 的退化情形,不需要单独格式。

### 核心建模决策(及其理由)

| 决策 | 理由 |
|---|---|
| **单位是 Agent,不是 Team** | 领域模型里**没有 team 表**。「团队」是 project membership 涌现的,task 的 lead/member 是运行时分配的。把 team 烘进格式 = 发明一个不存在的实体。 |
| 包 = 1..N 个 agent + **可选** `collection` 展示头 | 单个 agent / 一组 / 官方模板,全是同一 schema 的不同 N。`collection` 只是画廊卡片的外围信息,不带任何「队」语义。 |
| 「组队」是**导入之后的独立动作** | 导入只把 agent 放进 Agent Library;要不要把其中几个部署进项目(=组队)走现有 deploy 流程。导入不碰项目/团队结构。 |
| manifest 是 **100% 声明式 JSON** | 现有 `definitions.py`(Python 静态结构 + i18n key)整个废掉。官方模板改成这份 JSON。 |
| runtime 中立 | 我们支持 claude_agent / codex / deepagents。格式不绑任何单一 SDK(不与 OpenAI Agents SDK 的运行时对象对齐 —— 那是另一层的东西,详见 §8)。 |

---

## 2. 三层数据切分(格式的骨架)

一个 agent 涉及的字段天然分三层,必须分开处理:

**第 1 层 · 可移植定义(原样打包)**
agent 的 identity / instructions / runtime / effort / avatar、skill 引用、connector 引用。跨机器不变。

**第 2 层 · 导入时重新解析(install-local,不硬编码)**
- `provider_id` —— 指向**这台机器**配置的模型通道,换机即废。**不导出**。
- `model` —— 跟 provider 绑定,只作 `model_hint`(推荐值),导入时用 resolver 重新绑到目标通道。

**第 3 层 · 绝不导出,导入时补(密钥)**
第三方 `API_KEY`、OAuth token 等,在 keychain/secret store 里。包里**一个密钥字段都没有**,只用 `auth_type` / `requires_credentials` 声明「需要 key」,值在导入时补。

> 安全红线:可分享的包里一旦带密钥,用户一发出去就泄了。

---

## 3. 归档布局

Agent Pack 本质是一个 zip 归档(扩展名 `.valuzpack`):

```
investment-pro.valuzpack
├── manifest.json          ← 纯 JSON,无代码、无密钥、无 provider_id
└── skills/                ← 唯一载荷目录
    ├── dcf/
    │   ├── SKILL.md
    │   └── ...(skill 自带的资产/脚本)
    └── comps/SKILL.md
```

- `skills/` 是**唯一**的文件载荷目录,因为 skill 天生是文件包。只装**用户拥有**的 skill;
  app 自带的(bundled)只在 manifest 里引用,不进包。
- **没有 `connectors/` 代码目录** —— 连接器是纯指针(见 §5)。

---

## 4. manifest.json 完整结构

```jsonc
{
  "schema_version": 1,
  "kind": "agent-pack",

  // ── 可选展示头。N=1 裸导出可整块省略;官方模板/用户给组命名时才带 ──
  "collection": {
    "id": "investment-pro",      // 稳定标识,去重/迁移用
    "name": <Text>,
    "description": <Text>,
    "scenario": <Text>,          // 适用场景,画廊详情页用
    "icon": "gem"                // 头像预设 key
  },

  // ── 核心:1..N 个 agent。导入导出的真正单位 ──
  "agents": [
    {
      "slug": "inv-industry-analyst",  // 稳定,导入按它去重
      "name": <Text>,
      "description": <Text>,
      "instructions": <Text>,          // system prompt 正文
      "avatar": "analyst",
      "runtime": "claude_agent",       // claude_agent | codex | deepagents
      "model_hint": "claude-sonnet-4-6", // 仅推荐,导入时重绑通道;可为 null
      "effort": "high",                // low|medium|high|xhigh|max | null
      "skills": ["dcf", "comps"],   // 引用 skills[].slug
      "connectors": ["my-stdio-mcp", "my-research-api"] // 引用 connectors[].slug
    }
  ],

  // ── skill 清单(被 agents 引用,包级共享去重)──
  "skills": [
    {
      "slug": "dcf",
      "name": <Text>,                  // 列表展示,可选
      "description": <Text>,           // 可选
      "source": "embedded"             // embedded(skills/ 下有文件) | bundled(app 自带,引用)
    }
  ],

  // ── connector 清单(纯指针 + 定义,密钥/代码均无)──
  "connectors": [
    {
      "slug": "my-stdio-mcp",
      "display_name": <Text>,
      "description": <Text>,
      "transport": "stdio",            // http | sse | stdio
      "auth_type": "none",             // none | bearer | oauth
      "requires_credentials": false,   // true → 导入「待配置」托盘:补 key
      "requires_setup": false,         // true → 导入「待配置」托盘:需本机自部署
      "url": null,                     // http/sse 用
      "command": "uv",                 // stdio 用(指针原文)
      "args": ["run","--with","<deps>","python","{mcp_dir}/my-stdio-mcp/server.py","--transport","stdio"],
      "oauth_metadata": null,          // OAuth/directory 用,可为 null
      "setup_hint": null               // requires_setup 时给一句本机部署提示 <Text>
    }
  ]
}
```

### `<Text>` 类型(i18n 统一的关键)

```
<Text> = "纯字符串"                            // 用户导出:当前语言
       | { "zh-CN": "...", "en-US": "..." }    // 官方包:双语
```

loader 把裸字符串当「所有语言同值」。**官方与用户导出共用这一个 schema**,
彻底干掉「文本是 `agentTemplates.xxx` i18n key」那种写法 —— 那种 key 烘焙在
app bundle 里,根本没法导出/导入。

---

## 5. 连接器可移植性规则

连接器是「指向一个 server 的指针」,**永不带代码**。判定准则:

> **可移植连接器 = 接收方导入后,只需「授权一下」(填 key / 走 OAuth)就能用。**
> 达不到这条的,不进可用集。

按此分两档:

**✅ 可移植档(导入 + 授权即用)—— 导出主路径**
- http / sse(一个 URL)
- OAuth / directory(GitHub、Notion 这类,导入走 OAuth 重新授权)
- catalog / builtin(app 自带,args 里 `{mcp_dir}/<slug>/server.py` 在目标机解析到 app 代码)
- npx / uvx / docker 包运行器型 stdio(命令自己从 registry 拉)

**⚠️ 不可移植档 —— 本地路径型 stdio**
`python /Users/me/my_server.py`、指向本地二进制 —— 依赖发送者的机器,做不到「授权即用」。

处理:**不静默丢,降级成 `requires_setup` 声明**。agent 进库,该连接器挂在「待配置」
托盘里,提示「需在本机自行部署」。**绝不替用户搬运代码。**

> 为什么不直接丢:丢掉等于偷偷改了 agent 的能力集,接收方拿到一个「缺了一块、
> 还不知道为什么缺」的 agent。声明式依赖把设计意图留住、变成可执行提示
> ——「反馈引导行动,不静默失败」。

---

## 6. 导入流程(系统吃复杂度,用户只做选择)

1. **读 manifest** → 预览(collection 信息、N 个 agent、要装的 skill / connector)。
2. **选目标模型通道**:按各 agent 的 `runtime` 给个默认通道,可逐 agent 覆盖
   (复用现有 onboarding / `add_template` 的 resolver,把 `runtime` + `model_hint` → 具体 `provider_id` + `model`)。
3. **落地 skill**:`source: embedded` 的从 `skills/` 物化到 official-skills 目录
   (复用 `materialize_template_skills`);`bundled` 的仅引用。**按未知来源代码对待 + 沙箱**(`sandbox_seatbelt`)。
4. **登记 connector**:把 connector 定义写进 `valuz_connector`(密钥留空)。
5. **建 agent**:按 `slug` 去重(已存在则 skip),写入 `valuz_agent`。
6. **待配置托盘**:汇总两类「装了但还不能用」的待办,一次性呈现:
   - `requires_credentials: true` → 提示补 key / 走 OAuth
   - `requires_setup: true` → 提示本机部署本地 MCP
   **不阻塞导入** —— agent 先建好,待办后补。
7. (可选)**部署进项目**:用户选择把哪些 agent deploy 进哪个项目 = 组队。走现有 deploy 流程,导入本身不创建任何「队」记录。

---

## 7. 导出流程(逆过程 + 导出时反馈)

输入:用户选定的一组 library agent(或一个项目的 roster)。

1. **收集 agent** → 写 `agents[]`,抹掉 `provider_id`,`model` 降级为 `model_hint`。
2. **收集 skill** → 引用的 skill:用户拥有的 `source: embedded` 并把目录打进 `skills/`;
   app 自带的标 `source: bundled` 仅引用。
3. **收集 connector** → 按 §5 分档:
   - 可移植档:写完整定义(指针),`requires_credentials` 按 `auth_type` 标注,**剥所有密钥**。
   - 本地路径型:标 `requires_setup: true` + `setup_hint`,带 command/args 指针,**不带代码**。
4. **导出时反馈**(UX 收口):若组里含本地路径型 MCP,当场提示
   「连接器 `X` 是本地部署的,接收方将无法直接使用,会以『需手动配置』形式导出」。
   让用户在导出那一刻就知道哪些能跑、哪些不能。
5. 写 `collection`(可选,用户可在导出时定义名字/描述/场景/图标 —— 外围信息)。
6. 打 zip → `.valuzpack`。

---

## 8. 与运行时 SDK 的关系(非对齐说明)

manifest 对标的是「**可分享的 agent 定义**」,不是任何运行时的 Agent 对象。

OpenAI Agents SDK / Claude Agent SDK 里的 `Agent` 是**运行时代码对象**
(`tools` / `guardrails` 是可调用函数,`model` 可为实例),天生序列化不出来、
没有打包标准。我们的 manifest 是高一层的声明式配方。

**命名上向其靠拢以便映射,结构上保持 runtime 中立:**

| manifest(配方) | 解析后落到 runtime(成品) |
|---|---|
| `instructions` | → `instructions` |
| `runtime` + `model_hint` + 目标通道 | → 具体 `model` / Model 实例 |
| `effort` | → `model_settings.reasoning.effort` 之类 |
| `connectors[]`(指针)+ 导入授权 | → `mcp_servers[]`(活连接) |
| `skills[]` | → runtime 的 skill 物化(我们独有) |

manifest 是配方,runtime 对象是成品。配方中立,成品在导入/实例化那一刻按目标 runtime 生成。

---

## 9. 官方模板迁移

现状:`backend/valuz_agent/modules/agent_templates/` 下
`definitions.py`(静态 `TEMPLATES`)+ `agentTemplates.*` i18n key + `add_template` 服务。

迁移目标:

1. 三个官方模板(投研 / 小红书 / 世界杯)各转成一个带 `collection` 头的
   N-agent Agent Pack(预置在 `resources/` 下,文本用 `<Text>` 双语 map)。
2. `add_template(template_id, ...)` 收敛为 `import_agent_pack(内置包, ...)` —— 与用户导入同一条代码路径。
3. 废弃 `definitions.py` 的 `TEMPLATES` / `TemplateRoleDef` 与 `agentTemplates.*` i18n key。
4. `materialize_template_skills` 复用为导入流程的 skill 物化步骤(§6.3)。

> 迁移后:用户导入、官方模板、单 agent 分享,全部走 `import_agent_pack` 一条路径。

---

## 10. 不变量(实现须守)

- manifest 全是声明式数据,无逻辑、无 Python、无代码。
- 包里唯一可执行内容是 `skills/` 下用户自带脚本 —— 导入按未知来源代码对待 + 沙箱。
- **三不带**:不带 `provider_id`、不带任何密钥、不带连接器代码 / 本地路径代码。
- agent 按 `slug` 去重(幂等导入)。
- `collection` 仅画廊展示,**不落任何持久化的「队」记录**。
