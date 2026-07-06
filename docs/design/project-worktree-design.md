# Project Worktree 设计方案

> 状态：P0 已实现（会话级后端闭环，见 §12；P1+ 未实施）
> 参考实现：Claude Code（源码调研结论见附录 A）
> 现状关联：`fs_registry.subrun_dir(mode="repo-worktree")`（将被任务级方案取代）、
> session cwd 决策链（`SessionService._resolve_session_cwd` → `CreateSessionRequest.cwd`）

worktree 让一次会话 / 一个任务在项目 git 仓库的**独立分支副本**中运行：agent 可以
放手改文件，主工作区不受影响；结果要么被采纳（合入），要么被整体丢弃。

---

## 1. 目标与非目标

**目标**

- 会话级：新会话可选"在独立 worktree 中运行"。
- 任务级：任务（含定时自动化）可配置 worktree 开关；**lead 与全部 member 共享同一个
  worktree**，成员之间不再做二次隔离。
- 生命周期自动化到"干净即删"为止：结束时无变更自动清理，有变更保留待用户处置。

**非目标（明确不做）**

| 不做 | 理由 |
|------|------|
| 帮用户提 PR / rebase / 自动 merge | 汇合决策留给人；平台最多做"一键 merge，冲突即中止"（P2） |
| 内置打包 git | 会用 worktree 的用户（项目本身是 git repo）机器上必有 git；检测 + 引导即可 |
| copy-tree 隔离非 git 项目 | 无 diff、无 merge，隔离价值损失大半、磁盘代价最高；CC 也没有 copy-tree |
| `valuz_project_worktree` 表 | git 本身是事实源，建表引入 DB↔git 对账问题（见 D4） |
| 成员级 per-member worktree（现有 `repo-worktree` 模式） | 被任务级共享 worktree 取代（见 §5） |
| hook 式 VCS 可插拔 / tmux 集成 | 用户画像不需要；CLI 形态需求 |

---

## 2. 核心决策（对齐结论）

| # | 决策 | 要点 |
|---|------|------|
| **D1 git 依赖** | 不内置，检测 + 特性门控 | `ProjectDetail.git`（现算）含 `git_available`；macOS 上 CLT 未装时执行 `git` 会弹系统安装框——探测须先 `xcode-select -p` / 检查已知路径，避免后台探测弹 GUI |
| **D2 非 git 项目** | 禁用 + 422，不静默降级 | worktree 语义 = 可 diff 可合并的隔离副本，降级成 mkdir 是骗用户。例外：`kind=chat` 托管目录**创建时即 `git init`**（目录归我们所有，成本≈0）；用户绑定目录提供显式"用 git 管理此项目"引导按钮，绝不擅自 init |
| **D3 最小闭环** | P0 不做 merge，只做"干净即删 + 保留 + 汇合提示" | 见 §3；一键 merge 是 P2 增量 |
| **D4 不建表** | git 为唯一事实源 | worktree 现状 = `git worktree list --porcelain`；`base_sha`/`origin` 存目录旁 sidecar `meta.json`；session 归属走既有的 `sessions.metadata["valuz"]` 快照（见 §6）；漂移无需对账 |
| **D5 上下文注入** | 必须注入，≤4 行 | 当前 worktree（分支/base）、主工作区路径、不要修改主工作区、提交留在本分支 |
| **D6 submodule** | 尽力而为，作用域 = 最内层 repo | 创建后检测 `.gitmodules` → `git submodule update --init --recursive`，失败不阻断只告知 agent；子层各自 repo 各自提交，跨 repo gitlink 联动不代劳 |
| **D7 monorepo / 子目录 root_path** | cwd 相对路径修正 | worktree checkout 整个 repo；session cwd = `<wt>/<relpath(root_path, git_root)>` |
| **D8 任务级 worktree** | 一个任务一个 worktree，lead + member 共享 | member 不再 per-run 建 worktree；自动化（定时任务）配置同一开关（见 §5） |
| **D9 云沙箱** | host 建、沙箱用；write-back 未通前远程禁用 | 挂载集须含 worktree path + git common dir 且绝对路径不变（prefix-preserving 投影层即为此设计）；kernel 云镜像须含 git（见 §9） |
| **D10 删除纪律** | 一切删除 fail-closed | `status --porcelain` 非空 / 有新 commit / git 命令失败 → 一律视为有变更，拒绝自动删 |

---

## 3. 核心闭环（最小流程）

会话级与任务级共用同一个生命周期，仅入口不同：

```
创建 ──► 运行 ──► 结束检测 ──┬─ 干净 ──► 自动删除（worktree + 分支 + sidecar），用户无感知
                              └─ 有变更 ► 标记保留，进入项目 Worktrees 面板
                                            ├─ 继续工作（以该 worktree 开新会话/任务）
                                            ├─ 丢弃（确认框展示未提交文件数+新提交数；
                                            │        git 状态核实不了则拒绝删 —— D10）
                                            └─ 汇合提示（只展示不代劳）：
                                               「分支 valuz/u-<slug> 领先 N 个提交，
                                                在主工作区执行 git merge valuz/u-<slug> 即可合入」
```

- **创建**：`git worktree add -B valuz/<origin>-<slug> <git_root>/.valuz/worktrees/<slug> <base>`；
  已存在则 fast-resume（直读 worktree `.git` 指针，不 fetch、不碰网络）。
- **结束检测** `has_changes(path, base_sha)`：`git status --porcelain` 非空 **或**
  `rev-list --count <base_sha>..HEAD > 0` **或任一命令失败** → 有变更（fail-closed）。
- **P0 平台不执行任何 merge**。一键 merge（ff / merge-commit，冲突即中止回滚）与
  "冲突 → 派 agent 会话解决"是 P2。

---

## 4. 会话级流程

```
① 前端「新会话」：project.git.is_repo && git_available 时显示开关（可选命名）
② POST /v1/sessions   body: worktree: { name?: string } | null
③ SessionService.create_session:
     base_cwd = _resolve_session_cwd(...)                 # 现有逻辑不动
     if req.worktree:
         wt = await worktree_service.get_or_create(project, slug, origin="u")
         cwd = wt.path / relpath(root_path, git_root)     # D7
         session.metadata["valuz"]["worktree"] = 快照（见 §6）
     kernel_client.create_session(cwd=cwd, ...)           # kernel 零改动
④ system_prompt_builder 注入 D5 的四行说明
⑤ 归档 hook：has_changes? → 走 §3 闭环
```

**resume**：恢复前校验 `metadata.worktree.path` 仍在 `git worktree list` 中；不在则
提示"该 worktree 已被删除"并回落主工作区 cwd。

cwd 由 host 决策、kernel 只消费字符串——**kernel 全程零改动**，符合 adapter seam 原则。

---

## 5. 任务级流程（含自动化）

**开关位置**：任务创建参数与自动化（定时任务）配置各加 `worktree: bool`（默认 off）。
自动化每次触发生成新任务 → 每次运行获得**独立的、以 task 为粒度的** worktree。

**运行形态**（对齐结论：subtask 不再二次隔离）：

```
任务启动（worktree=on）:
    wt = get_or_create(project, slug=f"task-{task_id}", origin="task")
    lead session cwd   = wt.path (+ relpath)
    member dispatch    = 同一个 cwd —— 成员直接在任务 worktree 中工作
任务 finish（completed | failed）:
    has_changes? → §3 闭环（干净自动删；有变更保留进面板）
```

- **取代 per-member 隔离**：现有 `Task.project_mode="repo-worktree"`（per-run
  `git worktree add`，dispatcher.py:150 → `fs_registry.subrun_dir`）**退役**。任务级
  worktree 本质上是"shared 模式 + 把共享 cwd 从主工作区搬进 worktree"——成员并行写
  同一 checkout 的冲突约束交给计划 DAG 的依赖关系，与现在 shared 模式在主工作区的行为
  完全一致，只是主工作区不再被波及。`isolated`（纯 mkdir scratch）模式保留，与本开关正交。
- **lead review→merge 闭环自然消失**：成员就在任务分支上提交，lead 审的是同一棵树，
  不存在成员分支合并问题；对外只剩一次"任务 worktree → 主分支"的汇合，与会话级同构。
- **任务协调文件不进 repo 树**：worktree 开启时，task briefs/manifests/run 元数据改放
  fs_registry 的 data_dir 任务目录（host 自有），不落在 worktree 内——否则未跟踪的
  `tasks/` 目录会让 `has_changes` 永远为真，"干净即删"失效。
- **自动化的清理语义**：origin=task 的 worktree 是自动清扫的合法对象（30 天 + D10
  双重检查，见 §8）；origin=u（用户命名）永不自动删。

---

## 6. 状态与归属（不建表的完整答案）

三层各司其职，无第二份可变状态，无对账：

| 层 | 载体 | 内容 | 性质 |
|----|------|------|------|
| **现状** | git（`worktree list --porcelain` + status/rev-list 现算） | 存在性、分支、ahead/dirty | 可变，唯一事实源 |
| **worktree 元数据** | sidecar `<git_root>/.valuz/worktrees/<slug>.meta.json` | `{name, origin, base_sha, created_at}` | 与目录共生死；service 删 worktree 时一并 unlink；泄漏无害（现状层兜底） |
| **session 归属** | `sessions.metadata["valuz"].worktree` 快照：`{name, branch, path, git_root, base_sha}` | 创建时写入 | **不可变**，随 session 永久保存——worktree 删了以后历史会话仍能还原"当时在哪个分支基于哪个 commit 跑的"（建表方案反而给不了） |

- 读取路径即现有链路：SessionDetail mapper 读 metadata → API `worktree` 字段 → 前端 badge。
- **反查**（面板"这个 worktree 有哪些会话"）：P0 经 `ProjectSessionRow` 索引拉项目会话
  后逐条过滤 metadata（本地单用户，零成本）；若成热路径，给 `ProjectSessionRow` 加
  `worktree_name` 列——注意它只是**查询索引**（创建时冗余写、删 worktree 不更新），
  不是状态表，不引入漂移。
- **主工作区 status 卫生**：首次创建 worktree 时向 `<git_root>/.git/info/exclude`
  幂等追加 `.valuz/`（repo 本地、不进用户 tracked 文件），避免 `.valuz/worktrees/`
  在主工作区显示为 untracked。
- 分支命名 `valuz/<origin>-<slug>`（origin ∈ `u` | `task`）：`valuz/` 前缀是命名空间，
  origin 前缀是自动清扫的双保险（meta.json 为准，分支名兜底——对齐 CC 用命名模式
  区分临时 worktree 的思路）。斜杠 slug 扁平化为 `+`（单射，避开 git ref D/F 冲突）。

---

## 7. 模块与原语

```
infra/git_worktree.py            # 纯 subprocess 封装，全部 asyncio.to_thread 调用
    validate_slug(slug)              # ≤64 字符、段级 [a-zA-Z0-9._-]、拒绝 ./.. —— 照抄 CC
    detect_git(path) -> GitInfo|None # rev-parse --show-toplevel / --git-common-dir；
                                     # macOS 先探 CLT 再执行（D1）
    get_or_create(git_root, slug, base_ref, origin) -> WorktreeInfo
                                     # fast-resume 直读 .git 指针；新建 worktree add -B；
                                     # 全程 GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS='' + stdin 关闭
    has_changes(path, base_sha) -> bool          # D10 fail-closed
    remove(git_root, path, branch)               # worktree remove --force + branch -D + unlink sidecar
    list_worktrees(git_root) -> list[WorktreeInfo]

modules/worktrees/service.py     # 业务编排：slug 生成、meta.json、per-git-root asyncio.Lock
                                 # （同 repo 并发 add/remove 有 git 锁竞争，串行化）
                                 # submodule 尽力初始化（D6）、.env/.env.local 复制（P1）、
                                 # info/exclude 写入、归档/任务结束的清理钩子
```

目录布局 `<git_root>/.valuz/worktrees/<flattened-slug>`，选 repo 内而非
`~/.valuz-oss/` 的理由：sandbox 挂载 project cwd 即天然覆盖 worktree 与主仓库
`.git`（放外部则 gitdir 双向指针要求两个 mount）；`.valuz` 已在文件树 `HIDDEN_NAMES`；
相对路径引用跨平台更稳。

**post-creation setup（P1）**：复制项目根 `.env` / `.env.local`（干净 checkout 跑不起来
的最痛点）；project 设置级 symlink 目录清单（`node_modules` 等，默认空）。完整
`.worktreeinclude` 语法是 P3。

---

## 8. 清理策略汇总

| 时机 | 对象 | 条件 |
|------|------|------|
| 会话归档 / 任务 finish | 本次运行的 worktree | `has_changes` 为假 → 自动删（D10） |
| 面板"丢弃" | 任意 | 确认框展示未提交文件数 + 新提交数；核实不了拒绝删 |
| 定期清扫（boot 周期任务，P2） | 仅 origin=task（meta.json 与分支前缀双验证） | mtime > 30 天 **且** `has_changes` 为假 **且** 无 unpushed commits；origin=u 永不自动删 |

---

## 9. Sandbox 形态

**本地 seatbelt**：repo 内布局 → project cwd 挂载天然覆盖，零改动。例外是 D7 子目录
场景（git root 在挂载范围外）：profile 构建时探测 `git rev-parse --git-common-dir`
并加入 allowlist（动态挂载 extension 机制已有）。

**远程云沙箱（AGS）**：形态 = **host 建、沙箱用**——worktree 的创建/删除/清理永远在
host 本地执行（数据源在本地），沙箱只在 worktree cwd 里干活。约束：

1. worktree 的 `.git` 是指回 `<git_root>/.git/worktrees/<name>` 的**绝对路径指针**，
   故投影集合必须同时含 worktree path 与 git common dir，且沙箱内绝对路径不变——
   现有 prefix-preserving 投影层（`sandbox_paths.py`，mount = prefix + realpath）
   正是为此设计，把 git common dir 加入集合即可；
2. kernel 云镜像内须有 git 二进制（agent 在沙箱内 commit）；
3. 云端 commit 落在挂载卷上，回到本地依赖 COS→local write-back——**该链路未通前，
   远程会话的 worktree 开关禁用**（灰显 + 说明），不做半可用。

---

## 10. API 契约（contract-first，`api/openapi.yaml` 先行）

```yaml
# ProjectDetail 增加（现算，不入库）
git: { is_repo: bool, git_available: bool, git_root: string, subdir: string|null } | null

# SessionCreateRequest 增加
worktree: { name?: string } | null            # null/缺省 = 不用

# SessionDetail / SessionListItem 增加（读自 metadata 快照）
worktree: { name, branch, path } | null

# 任务创建 / 自动化配置增加
worktree: bool                                 # 默认 false

# worktree 资源（现算，git 为源）
GET    /v1/projects/{id}/worktrees             # 列表：name/branch/ahead/dirty/origin/关联会话
DELETE /v1/projects/{id}/worktrees/{name}      # 丢弃（fail-closed）
POST   /v1/projects/{id}/worktrees/{name}/merge   # P2 一键 merge
```

---

## 11. 前端

- **新会话**：git 项目 + git 可用时显示"在独立 worktree 中运行"开关（可选命名）。
- **任务创建 / 自动化（定时任务）配置**：同一开关，文案"在独立 worktree 中运行此任务"。
- **会话/任务头部**：worktree badge（分支名，点击进面板）。
- **项目 Context Panel → Worktrees 区块**：列表（分支、ahead/dirty 现算、来源、关联
  会话）+ 三动作（继续 / 丢弃 / 汇合提示文案，P2 变一键 merge）。
- 类型全部由 openapi 生成（`make generate-types`）。

---

## 12. 分阶段落地

| 阶段 | 内容 | 验收 |
|------|------|------|
| **P0 核心闭环（后端）** | `infra/git_worktree.py` 原语 + D1 检测；`modules/worktrees/` service（无表、meta.json、info/exclude）；会话级集成（§4：创建/注入/快照/归档自动清理）；openapi：`SessionCreateRequest.worktree`、`ProjectDetail.git`、worktrees GET/DELETE | curl 建 worktree 会话 → agent 在隔离分支改文件 commit → 归档后干净的自动删、脏的保留且 GET 可见 ahead 数 |
| **P1 产品化 + 任务级** | 前端开关/badge/面板（继续/丢弃/汇合提示）；**任务级开关 + 自动化配置（§5，含 dispatcher 改造、per-member repo-worktree 退役、任务协调文件迁出 repo 树）**；`.env` 复制；chat 项目创建即 `git init`（D2） | 桌面端全流程可视化；定时任务在 worktree 中跑完，干净自动删 |
| **P2 汇合与治理** | 一键 merge（ff/merge-commit，冲突即中止回滚）；冲突 → 预置指令的 agent 会话；定期 stale 清扫（§8）；symlink 目录设置；绑定目录"git init 引导"按钮 | 脏 worktree 一键合回主分支；冲突场景派单成功 |
| **P3 远端与加固** | 云沙箱（§9，含 git common dir 投影 + 镜像验证，write-back 后解禁）；D7 子目录 sandbox allowlist；sparse-checkout；`.worktreeinclude` 完整语法；Windows 路径核验 | `make dev-sandbox` 与远程 kernel 下 worktree 会话可用 |

---

## 13. 风险表

| # | 风险 | 处置 |
|---|------|------|
| R1 | macOS 无 CLT 时探测 git 弹系统对话框 | D1：先 `xcode-select -p` / 已知路径，再执行 git |
| R2 | root_path 是 repo 子目录 | D7 relpath 修正；sandbox 需 git common dir allowlist（P3） |
| R3 | git 凭据提示挂死后台进程 | 全部 git 调用免交互三件套；base 优先本地 ref，避免 fetch |
| R4 | 同 repo 并发 add/remove 锁竞争 | per-git-root asyncio.Lock + `to_thread` |
| R5 | 任务协调文件污染 `has_changes` | §5：worktree 模式下协调文件放 data_dir，不进 repo 树 |
| R6 | 用户手动删了 worktree / sidecar 泄漏 | git 为源：面板与 resume 都以 `worktree list` 现状为准；孤儿 sidecar 清扫时顺带回收 |
| R7 | 磁盘膨胀（node_modules 等） | P1 symlink 设置项；默认不做 |
| R8 | submodule 初始化失败 / 老 git 行为差异 | D6 尽力而为不阻断；prompt 告知 agent 现状 |
| R9 | 误删有价值内容 | D10：所有删除路径共用 fail-closed `has_changes`；自动清扫仅限 origin=task 且双重验证 |

---

## 附录 A：Claude Code 调研要点（浓缩）

CC 的实现收敛在 `src/utils/worktree.ts`，三个入口：CLI `--worktree [name|PR#]`（启动前
建好再 chdir）、`EnterWorktreeTool`/`ExitWorktreeTool`（会话中切换，退出选 keep/remove）、
Agent/Workflow `isolation:"worktree"`（子 agent 隔离，结束时无变更自动删、有变更保留并
把 path/branch 回传父 agent）。

借鉴进本方案的要点：

- 布局 `<repo>/.claude/worktrees/<slug>` + 分支 `worktree-<slug>`，slug↔branch↔path
  单射（嵌套 slug 用 `+` 扁平化）→ 本方案 §6/§7；
- fast-resume 直读 `.git` 指针跳过 fetch；git 免交互环境变量 → §7/R3；
- 删除决策一律 fail-closed（status 非空/有新 commit/命令失败都算有变更）→ D10；
- 定期清扫只碰**临时命名模式**、用户命名永不自动删 → §8；
- `.worktreeinclude` 复制 gitignored 必需文件 + symlink 大目录 + hooksPath 指回主仓库，
  解决"干净 checkout 跑不起来" → §7 post-creation setup；
- **从不替用户 merge** → D3。

CC 没有的、valuz 因产品形态（多会话 GUI 平台）而不同的：worktree 是跨会话可见的项目
子资源（面板/列表/汇合 UX）；任务级共享 worktree（lead+member 协作同一分支）；
云沙箱投影。CC 没有 copy-tree（非 git 直接报错），本方案同样不做。
