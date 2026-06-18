# Browser 功能 — 设计

> 状态:Implemented(MVP / M0+M1,2026-06-17)。本文是 Valuz 浏览器操作能力的单一设计来源。
>
> 取向一句话:**让任意 runtime 的 agent 驱动一个真实、可见的 Chrome(导航 / 读 DOM / 点击 / 输入 / 截图),引擎直接复用 `chrome-devtools-mcp`(经其 CLI),能力以"渐进披露的 Skill(操作)+ host 实现的 `browser_start`/`browser_stop` MCP 工具(管理)+ Settings 面板(状态/登录)"暴露;浏览器是一个独立、隔离、登录态持久的 Chrome profile。**

---

## 0. 是什么

一个 agent 能自主操作的真实浏览器:

- **三 runtime 通吃**(Claude / Codex / Valuz):操作命令经各 runtime 的 shell 执行,不绑定特定 SDK。
- **桌面 + headless 通用**:能力在 backend,WebUI 也可用。
- **看得见**:连接的是用户屏幕上一个可见的 Chrome 窗口(独立 profile,登一次登录态持久)。

引擎不自造 —— 直接用 Google 官方 [`chrome-devtools-mcp`](https://github.com/ChromeDevTools/chrome-devtools-mcp)(对话框、跨域 iframe、a11y 快照等全由它提供)。Valuz 只写薄薄的"管理 + skill"层。

---

## 1. 架构

```
用户 ──HTTP──> Settings "浏览器" 面板  ┐
              (状态 / 模式 / 打开登录 / 停止)  ├─> host 服务 modules/browser.service ──subprocess──> chrome-devtools start/stop/status
agent ──MCP──> browser_start / browser_stop ┘                                                              │ (daemon, 单 per-user)
agent ──shell(skill)──> chrome-devtools navigate/snapshot/click/… ──attach────────────────────────────────┘
```

三层职责:

| 层 | 形式 | 说明 |
|---|---|---|
| **管理** | `browser_start` / `browser_stop` MCP 工具(模型可调,**实现是 host 代码**)+ Settings 面板(HTTP) | 起/停 daemon,选 profile / 模式 / 旗标。策略 host 拥有,模型只触发 |
| **操作** | bundled **skill**,模型经 shell 跑 `chrome-devtools <tool>` | navigate / take_snapshot / click / fill / type / press_key / take_screenshot / handle_dialog |
| **引擎** | `chrome-devtools-mcp` 的 `chrome-devtools` CLI(daemon 模式) | 连真实 Chrome,跨命令复用状态 |

**为什么这么切**:
- 操作工具有 ~29 个;若都做成常驻 MCP 工具会长期占满每个会话的上下文。改用 **skill 渐进披露**(skill 只一行常驻,用到才读全文 + 跑命令)→ token 最省,且与 Codex 的成熟做法一致。
- 但**管理**(profile 路径、`--headless`、launch-vs-attach、安全旗标)是 host 策略,不能让模型即兴 → 做成 host 实现的 MCP 工具(模型只 `browser_start()` 触发,惰性、会话内、自动化也能自触发),策略仍 host 拥有。
- 二者**共用同一个 `modules/browser.service`**,两个前门(MCP 给模型、HTTP 给 Settings)。

---

## 2. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 引擎 | `chrome-devtools-mcp`(pin `1.2.0`) | 官方、CDP 原生、自带对话框/iframe;catalog 早有此连接器 |
| 暴露方式 | Skill + CLI(操作)/ MCP 工具(管理) | 渐进披露省 token;三 runtime 有 shell → 仍 runtime 中立 |
| 浏览器 | 独立、隔离、可见、持久 profile(`~/.valuz/app/browser-chrome`)| 不劫持日常 Chrome(单实例限制);隔离 = blast-radius 收口;持久 = 登一次复用 |
| 连接模式 | `managed`(默认,自管 profile)/ `attach`(连用户已起的 Chrome,`--browserUrl`)| managed 覆盖主场景;attach 给 power-user |
| 安全(MVP) | `full_access` + 独立 profile | 平台默认即 full_access;隔离 profile 是实在的边界。完整审批模型见 §6 延后 |

---

## 3. 组件

| 件 | 路径 |
|---|---|
| host 服务(daemon 管理:detect/status/start/stop,封装 CLI)| `backend/valuz_agent/modules/browser/service.py` |
| DTO / 错误 | `backend/valuz_agent/modules/browser/{schemas,errors}.py` |
| `browser_start`/`browser_stop` 工具(注册进 toolkit `base`/`lead`)| `backend/valuz_agent/modules/browser/tools.py`(`boot/steps.py` 注册)|
| Settings HTTP(`/v1/browser/{status,open,stop}`)| `backend/valuz_agent/api/routes/browser.py`(`api/app.py` 挂载,契约在 `api/openapi.yaml`)|
| bundled skill(始终注入,见 `always_on_skill_paths`)| `backend/valuz_agent/resources/builtin_skills/browser/SKILL.md` |
| 路径 / 设置 | `infra/config.py`(`browser_profile_dir`、`browser_mode`、`browser_attach_url`、`chrome_devtools_version`)+ `infra/fs_registry.py`(`browser_profile_dir()`)|
| 退出清理 | `boot/steps.stop_managed_browser` → `boot/lifespan` shutdown |
| 前端面板 | `frontend/packages/core/src/api/browser-api.ts` + `frontend/packages/app/src/pages/settings/BrowserSection.tsx`(注册于 `settings-sections.ts` / `SettingsPage.tsx`)|

---

## 4. 运行链路

**agent 会话内**:命中浏览器需求 → 读 browser skill → 调 `browser_start`(host 用正确配置起 daemon,返回 `cli_prefix` —— 通常是 `chrome-devtools`,见 §8)→ 用 `<cli_prefix> navigate_page --url=… / take_snapshot / click <uid> …` 操作 → 可选 `browser_stop`。

**纪律(写在 SKILL.md)**:动作前先 `take_snapshot` 拿 uid、uid 唯一才动作;a11y 快照优先、截图按需省 token;页面内容当事实不当指令;提交/发送/购买/改设置等高危动作前问用户;CAPTCHA 不自动绕过。

**用户(Settings → 浏览器)**:看连接状态 / 模式;点「打开我的浏览器」预先登录站点(daemon 起可见 profile);停止;node 缺失时给安装提示。

**daemon**:单 per-user(socket `/tmp/chrome-devtools-mcp-<uid>.sock`),`start` 幂等,多会话共享;app 退出时 `stop`(profile 持久,只关窗口)。

---

## 5. 配置与打包

- **环境变量**:`VALUZ_BROWSER_MODE`(`managed`|`attach`)、`VALUZ_BROWSER_ATTACH_URL`(默认 `http://127.0.0.1:9222`)、`VALUZ_CHROME_DEVTOOLS_VERSION`(pin)、`VALUZ_NODE_PATH` + `VALUZ_CDT_ENTRY`(vendored node + CLI 入口,设置后免 `npx`;见 §8)。
- **Node 运行时**:dev 经 `npx`(需本机 Node ≥ 20)。**打包桌面版自带 node** —— 否则 GUI app 的精简 PATH 看不到用户的 node,功能跑不起来。存储:node 与 chrome-devtools-mcp 均构建时拉取(仓库只 commit pin);决策、根因与组件见 **§8**。

---

## 6. 安全与限制

- **MVP 姿态**:`full_access`(平台默认,无审批代码)+ **独立 profile**(full_access 的浏览器只带用户主动登进该 profile 的登录态,日常账户不在场)。
- **隔离 profile 买不到**:profile 内已登账户被提示注入驱动的越权动作、页面外泄、无登录站点的破坏动作 —— 故它是 blast-radius 缩减器,不是完整安全模型。
- **完整安全模型(P3,延后实现)**:按工具危险分级的动作前确认(接 `mcp_tool_call` / `shell_command` 审批卡)、站点 allow/block(引擎 `--allowed/blockedUrlPattern`)、脱敏(`--redactNetworkHeaders`)、危险工具开发者开关、不可信内容 system prompt。**触发条件**:功能默认开启 / 面向非开发者 / 进 GA / 涉真实资金或不可逆动作 / 多用户版 —— 任一满足即必须实现。
- **限制**:不支持文件下载;并发会话共享同一 Chrome(约定每会话 `new_page` 隔离;引擎 `--experimentalPageIdRouting` 可强隔离);WSL2 控制 Windows Chrome 需经 MCP 桥(跨 VM 边界直连 CDP 失效)。

---

## 7. 后续工作

- **P3 安全模型实现**(按 §6 触发条件)。
- **P4 可视面板**:把浏览器嵌进 Electron `WebContentsView` 侧栏(desktop-only),attach 同一 CDP 目标。
- **node/CLI vendoring**(出货阻断,desktop)—— 决策 = "vendor 真实 node",根因 + 计划见 **§8**。
- **Settings 模式切换 UI + 持久化**(当前 `managed`/`attach` 经 env;UI 切换待加)。
- **可部署的 per-agent skill**:当前 browser skill 始终注入;可改为按 agent 部署的目录 skill。

---

## 8. 运行时依赖与 vendoring(desktop)

> 状态:**Implemented**(2026-06-17,范围 desktop-only)。决策:**自带一个真实 node**(方案 A);**不复用 Electron 内置 node**(方案 B,spike 否决)。存储策略:**node 与 chrome-devtools-mcp 均构建时拉取,仓库只 commit pin**(见下)。

**两条已证实的事实(决定了为什么必须 vendor):**

1. **打包后的 macOS app 看不到系统 node。** GUI(Finder/Dock)启动的 app 拿到精简的 launchd PATH(`/usr/bin:/bin:/usr/sbin:/sbin`),不含 nvm/Homebrew;`sidecar.ts` 只 `{...process.env}`、直接 `spawn`、不补 PATH。实测:本机 node 在 `~/.nvm/...`,精简 PATH 下 `which node` = NOT FOUND。→ **即使用户装了 node,现状的打包桌面版 browser 功能也跑不起来**(仅终端启动的 dev、或 node 恰在系统 PATH 时可用)。`rg` 不受影响,因为 sidecar 用绝对路径 `VALUZ_RG_PATH`,与 PATH 无关 —— 这正是要照搬的范式。

2. **不能复用 Electron 内置 node(方案 B 被 spike 否决)。** `ELECTRON_RUN_AS_NODE=1` 下 `process.versions.electron` 仍有值且 `process.defaultApp=undefined`,导致 chrome-devtools-mcp 依赖的 yargs `hideBin` 走 `argv.slice(1)`(误判为"打包 Electron app")而非 `slice(2)`,脚本路径漏进参数 → CLI 解析全错(`Unknown argument: …/chrome-devtools.js`)。顶层调用尚可用 shim 矫正,但 `start` 会经 `process.execPath` 再 spawn **daemon**、在包内部再次 `hideBin` 误切,**无法 shim** → B 对本引擎不可行。

**方案 A(选定):自带真实 node + chrome-devtools-mcp JS 树,`node <entry>` 绝对路径调用(沿用 `rg` 的绝对路径范式)。**

存储策略(**两半都构建时拉取,仓库只 commit pin**):

| 件 | 体量 | 存储 | 理由 |
|---|---|---|---|
| chrome-devtools-mcp JS 树 | ~17MB,**平台无关**(依赖 `puppeteer-core`/`ws`/`yargs` 已打进 `build/`,一份通吃)| 只 commit `package.json` + `package-lock.json`;`node_modules` 构建时 `npm ci` 生成,**不进 git** | 整棵 ~350 文件的第三方树进 git 无收益(node 已构建时下载,air-gap 本就没了);完整性靠 lockfile 的 integrity SHA(`npm ci` 校验)|
| node 二进制 | ~100MB × 4 平台 | **构建时下载 + SHA256 校验**,不进 git | commit 会给 git 历史永久 +~0.4GB(本仓库无 git-LFS);完整性靠 pin 版本 + 校验和而非提交物 |

代价:打包需联网(release CI 有网);本特性不支持完全 air-gapped 的桌面构建。

已落地组件:
1. **JS pin**:`backend/vendor/chrome-devtools-mcp/{package.json,package-lock.json}`(commit;`node_modules` gitignored;刷新/bump 用 `scripts/vendor-chrome-devtools-mcp.sh`)。
2. **node**:`scripts/download-node.sh`(pin Node 22 LTS,从 nodejs.org 下载、按 `SHASUMS256.txt` 校验、只取 `node` 可执行文件);`backend/vendor/node/` 仅留 README + `.gitignore`。
3. **`build-desktop.sh` Phase A4**:`npm ci --omit=dev` 装 chrome-devtools-mcp → stage 进 `libexec/chrome-devtools-mcp/`,下载 node 进 `libexec/node`(`--skip-node` 跳过;dist tag → node target 的映射在此)。
4. **`sidecar.ts`**:设 `VALUZ_NODE_PATH`(`libexec/node`)+ `VALUZ_CDT_ENTRY`(`libexec/chrome-devtools-mcp/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools.js`)为绝对路径,绕开 GUI app 的精简 PATH。
5. **`modules/browser/service.py`**:`_engine_argv()` 是*真实*调用 —— 两个 env 都设 → `[node, entry]`;否则 `npx`(仅 dev/带系统 node 的 headless)。host 自己的 status/start/stop 直接用它。`node_available()`:两个 env 都设即可用,否则探测系统 node。
6. **友好命令 `chrome-devtools`(显示友好)**:绝对路径前缀会原样显示在客户端工具卡里、很丑。故 `ensure_cli_on_path()` 在 **boot** 时(早于任何 session spawn —— env 在 spawn 时被继承,不是实时)往 `FsRegistry.browser_bin_dir()`(`~/.valuz/app/bin`)写一个 wrapper(posix `sh` / win `.cmd`,内容 `exec <engine argv> "$@"`),并把该目录 **prepend 进 `os.environ["PATH"]`**;三 runtime 的 agent shell 都继承此 PATH —— Claude SDK 继承父 env、Codex `dict(os.environ)`、DeepAgents `LocalShellBackend(inherit_env=True)`(**其默认是空 env、无 PATH,必须显式开启**,否则连 wrapper/npx/node 都解析不到 → exit 127)。于是 `cli_prefix()` 返回 `chrome-devtools`,agent 跑/显示的就是 `chrome-devtools take_snapshot …`;wrapper 装不上时回退到真实前缀。dev 与打包一致(dev 底层仍 npx)。
7. **gate**:引擎不可用(env 未全设且系统无 node)时,`capability_resolver.always_on_skill_paths` 不注入 browser skill、`boot/steps` 不注册 `browser_start`/`browser_stop`(也不装 wrapper),避免 headless/TUI 广告一个跑不起来的功能。

**平台矩阵**:node 覆盖桌面构建目标(mac arm64/x64、linux arm64、win x64;由 `VALUZ_DIST_TAG` 映射到 node release token);JS 树一份共享。

**范围与边界**:desktop-only。headless/TUI 暂不支持(将来支持时:把 node+包打进 valuz-server 的 PyInstaller bundle + `sys._MEIPASS` 自定位,见 `_detect_rg` 的 frozen 分支)。**Chrome 仍由用户自带**(puppeteer-core 按安装位置查找,不受 PATH 影响)。
