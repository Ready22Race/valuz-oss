# Valuz Pack — 统一的导入导出格式（Agent 包 / Project 包）

> 定义一套**可分享、可移植、声明式**的打包格式（`.valuzpack`），
> 统一两类导出:**一组 agent**(agent 包)和**一个 project**(project 包)。
> 二者共用同一个 manifest schema、同一套归档与安全机制、同一条安装代码路径;
> 区别只在「装到哪」——一个不落库的展示分组(`collection`),还是一个落库的
> 一等项目(`project`)。
> 本文档是该格式的设计 spec(单一来源),实现以此为准。

状态: 统一格式已实现(schema v2, 2026-06)。早期 v1 `agent-pack` 包仍可读入;
早期 `.valuz-project`(v1 `project-pack`)格式**不再兼容**。

---

## 1. 为什么做 / 设计目标

把「一组 agent + 它们的装备(skill / connector)」、以及「一个完整 project
(团队 + 自动化 + 项目级配置 + 记忆)」做成可落盘、可分享的包,使得:

- 用户能把自己调好的 agent / 项目导出,发给别人;别人导入后**只需授权一下**
  (填 key / 走 OAuth)即可使用。
- 官方模板不再是 bespoke 的 Python 静态结构,而是**同一种格式**的预置内容。
- 单个 agent 的分享是 N=1 的退化情形,不需要单独格式。
- agent 包与 project 包**不是两种格式**,而是同一格式的两种「目标」。

### 核心建模决策(及其理由)

| 决策 | 理由 |
|---|---|
| **单位是 Agent,不是 Team** | 领域模型里**没有 team 表**。「团队」是 project membership 涌现的,task 的 lead/member 是运行时分配的。把 team 烘进格式 = 发明一个不存在的实体。 |
| 包 = **载荷**(`agents`/`skills`/`connectors`)+ **恰好一个目标**(`collection` XOR `project`) | 载荷是「装什么」,目标是「装到哪」。两类导出共用载荷与安装路径,只在目标上分叉。 |
| `collection` 是 `project` 的退化形态 | `collection` = 只有 agent、不落库、纯画廊展示;`project` = 落库的一等项目,额外带 members 句柄、automations、项目级 skill/connector、`memory/`。二者是**平级互斥的具名字段**,不发明合成父类型。 |
| 「组队」是**导入之后的独立动作**(对 agent 包) | agent 包导入只把 agent 放进 Agent Library;要不要部署进项目走现有 deploy 流程。project 包则直接重建项目并部署 members。 |
| manifest 是 **100% 声明式 JSON** | 无 Python、无代码、无密钥、无 `provider_id`。官方模板也是这份 JSON。 |
| runtime 中立 | 支持 claude_agent / codex / deepagents。格式不绑任何单一 SDK(详见 §8)。 |

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

Valuz Pack 是一个 zip 归档,扩展名统一为 `.valuzpack`(agent 包与 project 包同名):

```
investment-pro.valuzpack
├── manifest.json          ← 纯 JSON,无代码、无密钥、无 provider_id
├── skills/                ← 可选。embedded(用户拥有)skill 的文件
│   ├── dcf/
│   │   ├── SKILL.md
│   │   └── ...(skill 自带的资产/脚本)
│   └── comps/SKILL.md
└── memory/                ← 可选,仅 project 包。项目 memory 目录树
    └── ...
```

- `skills/` 装**用户拥有**的 skill;app 自带的(bundled)只在 manifest 里引用,不进包。
- `memory/` 只在 `project` 目标存在时出现;agent 包没有这个目录。
- **没有 `connectors/` 代码目录** —— 连接器是纯指针(见 §5)。
- 安全护栏(读入时):每文件 ≤ 5 MiB,整包 ≤ 50 MiB,≤ 2048 文件,拒绝 zip-slip /
  路径穿越 / 盘符逃逸;path-shaped 的 skill slug 被归一成单段安全名。

打包/解包逻辑只有一份(`modules/packs_common/archive.py`),agent 包与 project
包共用。

---

## 4. manifest.json 完整结构(schema v2)

```jsonc
{
  "schema_version": 2,
  "kind": "valuz-pack",

  // ── 载荷:装什么(两类包完全共用,复用同一条安装路径)──
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
      "connectors": ["my-research-api"] // 引用 connectors[].slug
    }
  ],
  "skills": [
    { "slug": "dcf", "source": "embedded", "name": <Text>, "description": <Text> }
    // source: embedded(skills/ 下有文件) | bundled(app 自带,仅引用)
  ],
  "connectors": [
    {
      "slug": "my-research-api",
      "display_name": <Text>, "description": <Text>,
      "transport": "stdio",            // http | sse | stdio
      "auth_type": "none",             // none | bearer | oauth
      "requires_credentials": false,   // true → 导入「待配置」托盘:补 key
      "requires_setup": false,         // true → 导入「待配置」托盘:需本机自部署
      "url": null, "command": "uv", "args": [...],
      "oauth_metadata": null, "setup_hint": null
    }
  ],

  // ── 目标:装到哪(collection XOR project,恰好一个;exclude_none 省略另一个)──

  "collection": {                      // agent 包 —— 纯展示,不落库
    "id": "investment-pro",            // 内置包的稳定标识;用户导出为 null
    "name": <Text>,
    "description": <Text>,
    "scenario": <Text>,                // 适用场景,画廊详情页用
    "icon": "gem"
  }

  // 或者:

  // "project": {                      // project 包 —— 落库的一等项目
  //   "name": "投研项目",
  //   "kind": "project",
  //   "icon": null,
  //   "instructions_md": <Text>,      // 项目指令(用户手写的 Markdown)
  //   "members": [                    // 项目内句柄 → 引用顶层 agents[] 的 slug
  //     { "agent_slug": "lead", "source_agent_slug": "inv-industry-analyst" }
  //   ],
  //   "automations": [ ... ],         // 自动化(扁平移植 valuz_automation 列)
  //   "skills": [ { "skill_path": "/abs/path" } ],   // 项目级启用的 skill 路径
  //   "connectors": [ { "slug": "my-research-api" } ] // 项目级 connector slug
  // }
}
```

**恰好一个目标**由 manifest 的 validator 强制:`collection` 与 `project` 必须有且只有一个。
`exclude_none` 序列化使未用的那个目标在 JSON 里**整块缺省**——agent 包没有
`"project"` 键,project 包没有 `"collection"` 键。

### `<Text>` 类型(i18n 统一的关键)

```
<Text> = "纯字符串"                            // 用户导出:当前语言
       | { "zh-CN": "...", "en-US": "..." }    // 官方包:双语
```

loader 把裸字符串当「所有语言同值」。官方与用户导出共用这一个 schema。

### project 目标的语义要点

- **members 是瘦句柄**:agent 定义本体只在顶层 `agents[]` 出现一次(按 slug 去重),
  member 仅 `{agent_slug, source_agent_slug}`。同一个 library agent 在项目里部署成
  两个不同句柄 = 一条 `agents[]` + 两条 member。`agent_slug` 是 automation 引用的键,
  导入时必须原样保住。
- **project.skills / project.connectors 是项目级配置**(启用的 skill 路径 + connector
  slug),与顶层载荷 `skills[]` / `connectors[]`(安装索引)是两回事。
- **memory** 由 `project.memory` 指针声明 + 包里的 `memory/` 目录树承载。打包器在
  真正写入了 memory 文件时,才把指针置为 `"memory"`(由打包器一处设置,保证
  指针 ⟺ 归档内容永不漂移);没有 memory 时省略该字段。恢复时按指针定位目录
  (带路径穿越防护),目录本身仍是文件的唯一真相。

---

## 5. 连接器可移植性规则

连接器是「指向一个 server 的指针」,**永不带代码**。判定准则:

> **可移植连接器 = 接收方导入后,只需「授权一下」(填 key / 走 OAuth)就能用。**
> 达不到这条的,不进可用集。

**✅ 可移植档(导入 + 授权即用)—— 导出主路径**
http / sse(一个 URL);OAuth / directory(导入走 OAuth 重新授权);catalog / builtin
(app 自带);npx / uvx / docker 包运行器型 stdio。

**⚠️ 不可移植档 —— 本地路径型 stdio**
`python /Users/me/my_server.py`、指向本地二进制 —— 依赖发送者的机器。
**不静默丢,降级成 `requires_setup` 声明**:agent 进库,该连接器挂在「待配置」
托盘里,提示「需在本机自行部署」。**绝不替用户搬运代码。**

> 反馈引导行动,不静默失败:丢掉等于偷偷改了 agent 的能力集。

---

## 6. 导入流程(系统吃复杂度,用户只做选择)

**共用前半段(载荷安装)**——agent 包与 project 包一致:

1. **读 manifest** → 预览。按目标分流:`collection` 走 agent 库导入,`project` 走项目重建。
2. **选目标模型通道**:按各 agent 的 `runtime` 给默认通道,可逐 agent 覆盖
   (`runtime` + `model_hint` → 具体 `provider_id` + `model`)。
3. **落地 skill**:`embedded` 从 `skills/` 物化;`bundled` 仅引用。按未知来源代码 + 沙箱。
4. **登记 connector**:定义写进 `valuz_connector`(密钥留空)。
5. **建 agent**:按 `slug` 去重(已存在则 skip)。
6. **待配置托盘**:汇总 `requires_credentials` / `requires_setup` 的待办,一次性呈现,
   **不阻塞导入**。

**project 包额外的后半段(项目重建)**:

7. **建项目行**(全新 id;用户选了文件夹则绑定,否则托管 cwd)。同名项目**跳过不覆盖**。
8. **还原 memory**:`memory/` → 项目记忆目录(best-effort)。
9. **重建 members**:逐个 deploy,保住项目内 `agent_slug` 句柄。
10. **重建 automations**;**还原项目级 skill 路径 / connector slug**(best-effort)。

> agent 包导入**不创建任何「队」记录**;组队是导入后的独立 deploy 动作。

---

## 7. 导出流程(逆过程 + 导出时反馈)

1. **收集 agent** → 写顶层 `agents[]`,抹掉 `provider_id`,`model` 降级为 `model_hint`。
   project 导出把 members 的源 agent 提升进 `agents[]`(按 slug 去重),member 留瘦句柄。
2. **收集 skill** → 用户拥有的 `embedded` 并把目录打进 `skills/`;app 自带的标 `bundled` 仅引用。
3. **收集 connector** → 按 §5 分档,**剥所有密钥**,只留指针 + `requires_*` 标志。
4. **写目标**:agent 导出写 `collection`(可选命名/描述/场景/图标);project 导出写
   `project`(含 instructions_md / members / automations / 项目级配置),并把 `memory/` 打进包。
5. **导出时反馈**:若含本地路径型 MCP,当场提示「将以『需手动配置』形式导出」。
6. 打 zip → `.valuzpack`。

---

## 8. 与运行时 SDK 的关系(非对齐说明)

manifest 对标的是「**可分享的定义**」,不是任何运行时的 Agent 对象。

| manifest(配方) | 解析后落到 runtime(成品) |
|---|---|
| `instructions` | → `instructions` |
| `runtime` + `model_hint` + 目标通道 | → 具体 `model` / Model 实例 |
| `effort` | → `model_settings.reasoning.effort` |
| `connectors[]`(指针)+ 导入授权 | → `mcp_servers[]`(活连接) |
| `skills[]` | → runtime 的 skill 物化 |

manifest 是配方,runtime 对象是成品。配方中立,成品在导入/实例化那一刻按目标 runtime 生成。

---

## 9. 版本与兼容

- **当前格式**:schema_version `2`,`kind: "valuz-pack"`,载荷 + `collection` XOR `project`。
- **读入兼容**:早期 v1 `agent-pack`(`kind: "agent-pack"`)仍被接受,读时 lift 成统一 collection 形态;
  内置官方包仍以 v1 `agent-pack` JSON 落盘(loader 直接读)。
- **不兼容**:早期 `.valuz-project`(v1 `project-pack`)**明确拒绝**(清晰报错,不静默误解析)。
- **代码归属**:统一 manifest 模型 + archive 在 `modules/packs_common`;
  `agent_packs.manifest` 仅保留 v1 `AgentPackManifest`(re-export 原子)。

---

## 10. 不变量(实现须守)

- manifest 全是声明式数据,无逻辑、无 Python、无代码。
- 包里唯一可执行内容是 `skills/` 下用户自带脚本 —— 导入按未知来源代码对待 + 沙箱。
- **三不带**:不带 `provider_id`、不带任何密钥、不带连接器代码 / 本地路径代码。
- **恰好一个目标**:`collection` 与 `project` 有且仅有一个(validator 强制)。
- agent 按 `slug` 去重(幂等导入);project 同名跳过不覆盖。
- `collection` 仅画廊展示,**不落任何持久化的「队」记录**;`project` 才落库。
- 打包/解包与 zip-slip / size-cap 防御只有一份(`packs_common`),两类包共用。
