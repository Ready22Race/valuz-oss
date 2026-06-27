# 技术方案：项目详情页列表自动刷新

> Feature slug：`project-detail-auto-refresh`
> 对应 PRD：`docs/features/project-detail-auto-refresh/prd.md`（第 2 轮已过评审，本方案不改 PRD）
> 撰写：前后端开发 ｜ 状态：第 2 轮（已按「全栈开发 Reviewer」第 1 轮逐条修订）

---

## 0. 调研结论（先于一切，全部基于真实代码）

### 0.1 项目详情页 + 两张列表的真实数据链路

**项目详情页主组件**
- `frontend/packages/app/src/pages/ProjectDetailPage.tsx` → `ProjectDetailPage`，路由 `/projects/:id`（`const { id = "" } = useParams()` at L469）。路由注册在 `frontend/packages/core/src/edition/registries/desktop-routes.ts`（`id:"project-detail", path:"/projects/:id"`），组件映射在 `frontend/packages/app/src/routes/route-registry.ts`。

**详情页中心是一个 `Tabs defaultValue="all"`（L1625），三个 tab 各自独立计算顺序——这是 §7 锚定方案的核心前提：**
- **all tab（默认）`ProjectAllList`（L330-462）**：把会话与任务合并后**在客户端重排**：
  ```ts
  // L349-374
  const merged = [
    ...sessions.map(s => ({ kind:"chat", id:s.id, …, sortAt: s.updated_at })),
    ...tasks.map(tk => ({ kind:"task", id:tk.id, …, sortAt: tk.updated_at })),
  ];
  return merged.sort((a, b) => b.sortAt - a.sortAt);   // 最近活动在前
  ```
- **chat tab `ProjectRecents`（L161-259）**：直接 `sessions.map(...)` 按入参数组顺序渲染，**不做客户端再排序**。
- **tasks tab `ProjectTasks`（L288-326）**：直接 `tasks.map(...)` 按入参数组顺序渲染，**不做客户端再排序**。

**会话列表（chat / all 两 tab 的会话来源）**
- 数据源：全局 `useSessionStore`（`frontend/packages/core/src/store/session-store.ts`）的 `sessions`。
- 过滤（在**前端**做）：ProjectDetailPage.tsx:493-500
  ```ts
  allSessions.filter(s => s.project_id === id && s.status !== "created" && s.task_id == null)
  ```
  —— 与 PRD §2 可见规则**完全一致**（排除 `created` 草稿、排除 `task_id != null` 任务内部 session）。
- 取数 API：`frontend/packages/core/src/api/sessions-api.ts:434` → `sessionsApi.list(projectId)` → `GET /v1/sessions?project_id={id}`（内部 `createFetchJson` 的 `fetchJson(path)`，当前**不接收第二个 init 参数**）。
- `SessionListItem` 字段（`frontend/packages/shared/src/types/session.ts`）：`id / project_id / name / status("created"|"running"|"idle"|"failed"|"cancelled"|"archived") / task_id / updated_at …`。**会话列表的 `updated_at` 即其创建时间**（ProjectDetailPage.tsx:358-360 注释明确：`A session's list updated_at is its creation time`），因此**会话的 `sortAt` 实际不可变**。

**任务列表（顶层 lead-dispatch Task）**
- 数据源：**组件本地** `const [tasks, setTasks] = useState<Task[]>([])`（L515）。
- 取数：ProjectDetailPage.tsx:721-735 的 `useEffect`，`tasksApi.listTasks(id)`，**依赖数组为 `[id]`——只在进入/切换项目时取一次，停留期间不再刷新**（这正是 PRD 描述的缺陷根因），`setTasks(res.tasks)` 整表覆盖。
- 取数 API：`frontend/packages/core/src/api/tasks-api.ts:178` → `tasksApi.listTasks(projectId)` → `GET /v1/projects/{id}/tasks`（同样基于 `fetchJson(path)`，**当前不接收 init 参数**）；`Task` 接口（tasks-api.ts:12）含 `id / project_id / title / status:string / created_at / updated_at`。

### 0.2 后端取数接口（已按 user_id + project_id 过滤）

**会话列表** `GET /v1/sessions`：
- `backend/valuz_agent/api/routes/sessions.py:183` → `list_sessions(project_id, q)`
- → `backend/valuz_agent/modules/sessions/service.py` `SessionService.list_sessions()`
- → `backend/valuz_agent/modules/sessions/project_index.py:70` `list_session_ids(project_id, user_only=True)`：`WHERE user_id == require_current_user_id() AND project_id == ? AND kind == "chat"`（`user_only` 即排除 `task_lead`/`task_subtask` 内部 run）
- → `adapters/kernel_client.py` `list_sessions(user_id, ids=...)` → kernel `sessions` 表。

**任务列表** `GET /v1/projects/{project_id}/tasks`：
- `backend/valuz_agent/api/routes/tasks.py:204` → `list_tasks(project_id, user_id=Depends(require_current_user_id))`
- → `backend/valuz_agent/modules/tasks/datastore.py:49` `TaskDatastore.list_tasks(user_id, project_id)`：
  ```python
  select(TaskRow).where(TaskRow.project_id == project_id, TaskRow.user_id == user_id)
                 .order_by(TaskRow.created_at.desc())
  ```
  → **后端任务列表按 `created_at DESC` 返回**（不可变排序键）；`TaskRow`（models.py:51，表 `valuz_task`）`status ∈ {draft, active, paused, stopped, completed, blocked, abandoned}`，顶层 task = `valuz_task` 一行；member sub-run 在 `valuz_task_session(kind="subtask")`，不进此列表。
- **关键：`TaskDatastore.update_task_status()`（datastore.py:122-129）在状态变化时写 `.values(status=status, updated_at=now_ms())`**——所以任务每次状态流转 `updated_at` 都会被刷新成当前时刻。

→ **两个接口都已是「user_id + project_id 过滤的全量列表」，正是本版需要的对象，无需新增/改动接口。**

### 0.3 SSE / event 通道现状

- 会话级 SSE：`GET /v1/sessions/{id}/events/stream`（sessions.py:252）→ `adapters/event_sse_adapter.py` `iter_events_sse()`，**按单个 session 订阅**，服务会话详情页消息流。
- kernel events 表（`backend/kernel/.../models.py` `EventModel`）字段 `id / user_id / session_id / type / data / timestamp`——**只有 session_id，没有 project_id**。
- kernel 全局流：`backend/kernel/app/routes/events.py:27` `stream_all_events`，host 侧封装为 `adapters/kernel_client.py` `subscribe_all_events()`——**每帧只带 `session_id`，不带 project_id/user_id，且目前没有任何 host route 消费它**。
- **task 列表无推送通道**：`tasks.py` 注释明确 “Task events don't have an in-memory broadcast subscriber (unlike kernel events). DB polling at 500ms is cheap.”；`valuz_task.status` 由 host 维护，kernel 全局 tap 根本不携带 task 生命周期。

### 0.4 现有「会话新鲜度机制」是否可复用（已按 Reviewer 第 1 轮 P2 修正归因）

存在新鲜度骨架，但**对详情页中心的两张列表基本零覆盖**。逐条核对 `ProjectLayoutBase.tsx`：
- 挂载触发：`fetchSessions()`（L282）、`fetchAllTasks()`（L286）各一次。
- 路由切换触发：`fetchSessions()`（L298，进入 conversation 路由时）、`fetchAllTasks()`（L307，`/tasks`·`/projects` 路由切换时）。
- **停留期间的 60s `setInterval`（L396-399）调用的是 `refreshFinishedRuns()` + `refreshAutomations()`，并不是 `fetchSessions()`、更不是详情页本地 `tasks`。** （前一稿误写成「60s 兜底刷会话」，与代码不符，此处更正。）
- `fetchAllTasks()` 喂的是**侧边栏跨项目** `useTaskStore`（task-store.ts），与详情页本地 `tasks`（ProjectDetailPage L515 useState）不是同一份状态。
- `frontend/packages/core/src/hooks/use-running-runs.ts` `useRunningRuns`：单例 `POLL_MS=10000` 轮询 `/v1/runs?status=running`，隐藏时跳过——服务侧边栏 RECENTS 的「运行中」态，不刷详情页列表。

→ **修正后结论：详情页停留期间，中心会话列表只能等到下次进 conversation 路由 / 重挂载才刷新，中心任务列表（本地 `tasks`，依赖数组 `[id]`）停留期间根本不刷新。现有任何 interval 都没覆盖到它们。** 本版复用其 visibility-aware 轮询**模式**，新增一个页面作用域、满足 5s SLA 的聚焦轮询即可，无需后端新通道。

---

## 1. 核心技术说明（拍板）

在 PRD §8 三选一中**选定「复用既有新鲜度机制 + 页面作用域聚焦轮询」**，不引入新的 SSE 项目级通道。原因：①任务列表后端无内存广播、`valuz_task.status` 为 host 态，任何 SSE 方案都仍须为任务轮询；②`subscribe_all_events()` 是只带 `session_id` 的全局火管、需逐帧反查 project/owner、无现成 host route，跨项目泄漏风险高；③直接复用**已按 `user_id+project_id` 过滤**的两个现有列表接口，跨项目隔离（§9.7）天然成立、零契约改动。

实现由两块组成：

1. **取数层**：新增页面作用域 hook `useProjectListAutoRefresh(projectId, { onTasks })`，仅在详情页挂载且标签可见时，每 **4s** 拉两接口；执行契约（单飞 / abort / 超时 / generation 防旧响应 / `Promise.allSettled` 独立失败 / 恢复整表补齐）见 **§4A**。会话经 id 键控的 `mergeProjectSessions` upsert 进 `useSessionStore`；任务经 `onTasks` 回调 id 键控原位合并进 ProjectDetailPage 本地 `tasks`。
2. **呈现层（锚定）**：**前一稿「两接口 `created_at DESC` 不可变 → status 变化不重排 → 稳定 key + 引用复用即可保锚」的前提经代码核对不成立**——默认 all tab 的 `ProjectAllList` 在客户端按 `sortAt = updated_at` 重排，而 `update_task_status()` 会把任务 `updated_at` 写成 `now_ms()`，**任务状态变化会让默认 all 列表重排**，稳定 `key={id}` 只移动 DOM、不能保证可见行不跳。因此**在不改任何排序规则**（PRD §4 硬约束）前提下，引入**滚动位置校正锚定**：合并渲染前记录滚动容器内首个可见 item 的 `{kind,id,top}`，提交后用 `useLayoutEffect` 在浏览器绘制前校正 `scrollTop`，详见 **§4B / §7.4**。

失败静默吞掉、保留旧列表；恢复后下一次成功 tick 以**整表结果**回灌（§9.6）。

---

## 2. 调用链（前端订阅 → 后端 → kernel）

本版**不新增后端路径**，复用既有两条取数链：

```
[ProjectDetailPage 挂载 useProjectListAutoRefresh(id, { onTasks })]
   │  每 4s（可见时）/ visibilitychange→visible / online 立即触发；同一 projectId 单飞 + AbortController + 超时
   ├─(A 会话) sessionsApi.list(id, { signal })  ──HTTP──▶ GET /v1/sessions?project_id=id
   │     └ routes/sessions.py:list_sessions → SessionService.list_sessions(project_id)
   │        ├ project_index.list_session_ids(project_id, user_only=True)   # user_id+project_id+kind=chat
   │        └ kernel_client.list_sessions(user_id, ids=…) ─▶ kernel sessions 表
   │     ◀── {sessions:[SessionListItem]} ──（generation 校验通过）── mergeProjectSessions(id, items) ─▶ useSessionStore
   │
   └─(B 任务) tasksApi.listTasks(id, { signal }) ──HTTP──▶ GET /v1/projects/{id}/tasks
         └ routes/tasks.py:list_tasks(project_id, user_id)
            └ TaskDatastore.list_tasks(user_id, project_id)  # WHERE project_id AND user_id ORDER BY created_at DESC
         ◀── {tasks:[Task]} ──（generation 校验通过）── onTasks(mergeTasks(items)) ─▶ ProjectDetailPage 本地 tasks
```

说明：kernel events 表/SSE 仍只用于会话详情页消息流（本版不碰）。“session 是否已发首条消息” 由后端 `status` 体现（`created → running/idle`），前端过滤 `status !== "created"` 决定其在列表的出现时机；轮询天然能感知该状态翻转。

---

## 3. 数据模型 / 事件载荷

本版**不定义新事件类型、不改数据模型**。变化经由“整表取数 + 客户端 diff”感知，载荷即两个现有列表 DTO：

- 会话：`SessionListItem` `{ id, project_id, status, task_id, name, updated_at, … }`。变化感知点：新 id 出现 = 新会话；`status` 由 `created→running/idle` = 进入可见集；`status` 其它流转 = 行内更新。会话列表 `updated_at` = 创建时间（不可变），故会话间排序稳定。
- 任务：`Task` `{ id, project_id, title, status, created_at, updated_at, … }`。新 id = 新任务；`status` 任意流转（active/paused/stopped/completed/blocked …）= 行内 StatusPill 更新，**同时 `update_task_status()` 会把 `updated_at` 改为 `now_ms()`**。

排序事实（**这是与前一稿最关键的更正**）：
- 后端两接口排序键确为不可变（会话 by id 集合、任务 `created_at DESC`）；**chat tab、tasks tab 不做客户端再排序**，所以这两个 tab 状态变化只触发**行内**更新、不重排。
- **但默认 all tab 的 `ProjectAllList` 在客户端按 `sortAt = updated_at` 重排**；任务 `updated_at` 随状态变化被刷新 → **默认 all 列表会因任务状态变化重排**。§7.4 的锚定方案必须覆盖这一重排，不能只依赖稳定 key。

---

## 4. 实现机制选型（PRD §8 拍板 + 5s SLA）

| 方案 | 会话列表 | 任务列表 | 跨项目隔离 | 降级/恢复 | 契约/面 | 结论 |
|---|---|---|---|---|---|---|
| A 新建项目级 SSE | 复用 `subscribe_all_events()` 逐帧反查 project/owner | **仍须轮询**（无广播） | 需自写过滤，易漏 | 需 reconnect+cursor 自写 | 新 kernel route + openapi + host 消费者 | ✗ 复杂、风险高、SLA 无增益 |
| B 轮询既有两接口 | `GET /v1/sessions?project_id` | `GET /v1/projects/{id}/tasks` | **后端已强制** | 失败吞掉、成功 tick 整表回灌 | **零后端改动** | ✓ 选定 |
| C 纯复用现有新鲜度 | 进 conversation 路由才刷 | 详情页根本不刷 | — | — | — | ✗ 不满足 5s |

**选 B**。**5s SLA**：轮询间隔 **4s**（常量可调）。最坏：变化发生在某 tick 之后 → 下一 tick ≤4s 检测 → 本地 127.0.0.1 查询往返 ~数十 ms + 渲染 < 5s。隐藏标签暂停；`visibilitychange→visible` 与 `online` 立即补一次 fetch。

### 4A. `useProjectListAutoRefresh` 执行契约（修订 P1-2：降级/恢复/竞态语义补全）

签名：`useProjectListAutoRefresh(projectId: string, opts: { onTasks: (tasks: Task[]) => void; intervalMs?: number })`，`intervalMs` 默认 4000。Hook 内维护：

1. **每 projectId 作用域单飞（single-flight）**：用一个 `inFlightRef`（boolean）。tick 触发时若上一轮仍在进行则**跳过本次**，不积压请求。`visibilitychange→visible` / `online` 的即时补拉同样过单飞闸。
2. **AbortController + 卸载/换 id 即 abort**：每轮 tick 新建 `AbortController`，把 `signal` 传给两个请求。effect cleanup（卸载或 `projectId` 变化）`controller.abort()`，并清 `setInterval`、移除 `visibilitychange`/`online` 监听。
   - 落点：**扩展 `sessionsApi.list` / `tasksApi.listTasks` 的签名，新增可选第二参数 `init?: { signal?: AbortSignal }`**，透传给底层 `fetchJson(path, init)`。`createFetchJson` 已经 `fetch(url, { ...init, headers })` 转发 `init`（fetch-json.ts:108），所以**只需把 signal 串到 API 方法签名**，无需改 `createFetchJson` 本体。
3. **≤ 轮询间隔的超时**：`createFetchJson` 对普通 REST **无默认 timeout，挂起请求不会 reject，`catch` 不会触发**（fetch-json.ts 仅 catch 网络层 `TypeError`，对“连上但不返回”无能为力）。故每轮 tick 起一个 `intervalMs` 的 `setTimeout(() => controller.abort(), intervalMs)`（abort 会让 fetch 以 `AbortError` reject，进入失败分支），成功/失败后 `clearTimeout`。**保证一个挂起请求最多占用一个间隔，不会阻塞下一轮恢复补拉。**
4. **generation / projectId 防旧响应（A→B 切项目）**：用一个单调递增的 `genRef`（每次 effect 因 `projectId` 变化重建时 `gen` 也随之变化，或显式 `genRef.current++`）。发起请求前捕获 `myGen = genRef.current`；响应回来写入 store / 调 `onTasks` **之前**校验 `myGen === genRef.current && projectIdAtRequest === currentProjectId`，不等则**丢弃**。这样从项目 A 切到 B 后，A 的晚返回不会写进 B 的页面态。`onTasks` 回调侧再加一层项目归属断言（合并前校验 `tasks[].project_id === projectId` 同源）兜底。
5. **会话 / 任务并行且独立失败（`Promise.allSettled`）**：
   ```ts
   const [sRes, tRes] = await Promise.allSettled([
     sessionsApi.list(projectId, { signal }),
     tasksApi.listTasks(projectId, { signal }),
   ]);
   if (stale()) return;                       // generation 校验
   if (sRes.status === "fulfilled") mergeProjectSessions(projectId, sRes.value.sessions);
   if (tRes.status === "fulfilled") onTasks(mergeTasks(tRes.value.tasks));
   // 任一 rejected：静默（不弹错、不清空），各自保留上次成功结果
   ```
   单边失败/超时**不阻塞**另一边写入，也不拖垮另一张列表的 5s SLA。
6. **从失败转成功 = §9.6 补齐点**：因为每轮都是**整表**取数，无需显式区分“恢复 tick”。某接口本轮 `fulfilled` 即用**本次成功的整表结果**覆盖该列表（含失败期间遗漏的新增/状态变化），一次补齐。计时起点 = “检测到恢复”：`online`/`visibilitychange→visible` 立即补拉，否则 ≤4s 的下一 tick，整体 < 5s。
7. **首拉与轮询的关系**：ProjectDetailPage L721-735 既有的 `tasksApi.listTasks(id)` 首拉保留（首屏全量，PRD §6.1）；会话首屏仍由 `ProjectLayoutBase` 既有 `fetchSessions()` 提供。Hook 只负责**停留期间**的持续刷新，与首拉不冲突（都走整表 + id 键控合并，幂等）。

### 4B. 锚定（修订 P1-1：滚动位置校正，不改排序规则）

PRD §4 硬约束「本版不改动列表的排序规则」——**不把 all tab 默认排序改成 `created_at`**。在保留 `ProjectAllList` 现有 `sort((a,b)=>b.sortAt-a.sortAt)` 的前提下，用**滚动锚点校正**消除重排/插入造成的可见行跳动。机制见 §7.4，三个 tab 全覆盖。

---

## 5. 影响面

**新增**
- `frontend/packages/core/src/hooks/use-project-list-auto-refresh.ts` —— `useProjectListAutoRefresh`：按 §4A 的执行契约实现（单飞 / AbortController / 超时 / generation / `Promise.allSettled`）。
- `frontend/packages/core/src/hooks/index.ts` —— **补 barrel export**：新增 `export * from "./use-project-list-auto-refresh";`（该文件是逐 hook 显式罗列的 barrel，`core/src/index.ts` 的 `export * from "./hooks"` 依赖它；不补则按现有 `@valuz/core` 导入风格使用会编译失败）。【采纳 Reviewer P2】
- `frontend/packages/core/src/hooks/use-list-scroll-anchor.ts`（或同等内联实现）—— `useListScrollAnchor(scrollContainerRef, dataKey)`：§7.4 的滚动位置校正 hook（捕获首个可见 item → `useLayoutEffect` 校正 `scrollTop`）。
- `frontend/packages/core/src/store/session-store.ts` 增 `mergeProjectSessions(projectId, items)`：**仅**替换 `project_id===projectId` 的子集（移除该项目已消失行、upsert 新增/变更行、**保留其它项目行不动**），复用未变对象引用（减 re-render）。避免现有 `fetchSessions(id)`（session-store.ts:43 `set({sessions})` 整库覆盖）把全局 store 收窄成单项目子集而打断侧边栏 RECENTS。

**改动**
- `frontend/packages/core/src/api/sessions-api.ts` / `tasks-api.ts`：`list(projectId, init?)` / `listTasks(projectId, init?)` 增可选 `init?: { signal?: AbortSignal }`，透传 `fetchJson(path, init)`（§4A.2）。其余调用点不传 init，行为不变。
- `frontend/packages/app/src/pages/ProjectDetailPage.tsx`：
  - 挂载 `useProjectListAutoRefresh(id, { onTasks: mergeTasksInPlace })`；把 L727 的 `setTasks(res.tasks)` 整表覆盖改为 id 键控原位合并（`mergeTasks`：既有行原位更新、新行按 `created_at` 序就位、消失行剔除）。L721-735 首拉逻辑保留。
  - 给 tab 内每个列表行 `<li>` 加 `data-anchor-key={`${kind}-${id}`}`（`ProjectAllList` L392/L407、`ProjectRecents` L184/L199、`ProjectTasks` L308），并为滚动容器接 `useListScrollAnchor`（§7.4）。

**不动**
- `api/openapi.yaml`：**无需改动**（不新增/不改 endpoint 与 schema）。
- i18n：**不涉及**（PRD §7 复用现有 loading/空态/StatusPill 文案，无新增用户可见字符串）。
- 后端：**零改动**（两接口已满足；不碰 kernel events / SSE / event_sse_adapter）。
- `createFetchJson` 本体：不改（已转发 `init`，超时在 hook 侧用 abort 实现）。

---

## 6. 已知 trade-off

1. **轮询非真推送**：稳态下每 4s 最多 2 个本地请求（单飞下不积压）。本地优先 + 隐藏暂停下成本可忽略；换来零后端面、跨项目隔离零自写、降级恢复语义天然。
2. **检测延迟上限 ~间隔**：4s 间隔 → 最坏接近但 < 5s。若未来 SLA 收紧到 2s 可调小常量。超时也用同一 `intervalMs`，最坏一个挂起请求占满一个间隔。
3. **整表取数而非增量 diff**：每 tick 拉全量列表。项目列表规模小（本版不分页，PRD §4），整表 diff 成本极低；好处是恢复即整表回灌、无 cursor/gap 隐患。
4. **锚定校正引入一次 layout 读写**：每次会改顺序的数据更新都在 `useLayoutEffect` 里读 `getBoundingClientRect` + 写 `scrollTop`（同步、绘制前完成，用户不可见跳动）。规模小、频率低（≤每 4s 一次且仅在列表确实变化时），成本可忽略；代价是引入一处 DOM 测量逻辑需测试覆盖（§9）。
5. **锚定优先于严格排序**：当用户已滚动到列表中段时，新插入/重排的条目不会把可见行顶走（§7.4 只在“用户不在顶部”时校正）；新条目按 PRD §7/§9 口径在用户回到顶部/排序位置时呈现——这是 PRD 已拍板的取舍，非缺陷。

---

## 7. 回归点（不破坏既有行为）

1. **不破坏会话详情消息流**：本版只新增列表层轮询，**完全不碰** `event_sse_adapter` / `subscribe_events` / `chat-store.attach` / `session-stream` / `useSessionEvents` / `useTaskEvents`。
2. **不破坏跨项目隔离**：轮询直接调用后端已按 `require_current_user_id()` + `project_id` 过滤的接口；§4A.4 generation 校验 + `mergeProjectSessions(projectId, …)` 子集合并 + `project_id` 同源断言，三重保证别项目数据绝不写进当前视图（§9.7）。
3. **不破坏侧边栏 RECENTS / TASKS**：用 `mergeProjectSessions` 子集合并而非 `set({sessions})` 整库覆盖，全局 `useSessionStore` 仍含所有项目会话；`useTaskStore`（跨项目侧栏）不被详情页轮询触碰。
4. **锚定不跳动（§9.5）——滚动位置校正方案（替换前一稿失效的“稳定 key 即可”论断）**：
   - **真实排序事实**：默认 all tab 的 `ProjectAllList` 客户端 `sort` by `updated_at`，`update_task_status()` 写 `updated_at=now_ms()` → **任务状态变化会重排 all 列表**；仅靠 `key={id}` 只会移动 DOM 节点，不能保证可见行不跳，浏览器原生 scroll anchoring 也不覆盖“重排已有行”。
   - **方案：滚动锚点校正（不改排序规则）**：
     1. 给三处列表行 `<li>` 加 `data-anchor-key={kind-id}`；滚动容器接 `useListScrollAnchor(ref, dataKey)`（`dataKey` = 当前 tab 的合并数据指纹，如行 key 序列的 hash 或 `sessions.length+tasks.length+各行 updated_at`）。
     2. hook 持续（passive scroll 监听 + 数据更新前）记录滚动容器内**首个可见 item** 的 `{ key, top }`（`top` = 该行 `getBoundingClientRect().top - container.getBoundingClientRect().top`）到 `anchorRef`。
     3. 数据变化触发重渲染后，`useLayoutEffect`（keyed on `dataKey`，在浏览器**绘制前**同步执行）按 `anchorRef.key` 用 `[data-anchor-key]` 选择器找回该行，读其新 `top'`，执行 `container.scrollTop += (top' - anchorRef.top)`，把锚点行钉回原视觉位置。
     4. **顶部豁免（满足 §9.1/§9.2 新条目可见）**：若 `container.scrollTop <= 阈值(~8px)`（用户在顶部），**不校正**——让新条目/重排自然在顶部呈现；仅当用户已下滚时才校正（锚定优先，§7.5 trade-off）。
     5. 锚点行若已被删除：回退到下一个仍存在的可见候选，再退化到不校正。
   - **三 tab 覆盖**：
     - **all tab**：任务状态变化引发 `updated_at` 重排 → 校正保可见行不动；新会话/新任务插入表头 → 顶部豁免时可见、下滚时不顶走。
     - **chat tab**：会话顺序稳定（`updated_at`=创建时间不可变），但新会话插表头 → 同样由锚点逻辑处理。
     - **tasks tab**：`created_at DESC` 稳定、状态变化只行内更新不重排；新任务插表头 → 同锚点逻辑。
   - 轮询**只写列表数据**，不触碰 composer 输入 / 选中项 / 展开态（独立组件态）。
5. **HMR/泄漏**：hook 在 effect cleanup 清 `setInterval`、`clearTimeout`、`controller.abort()` 并移除 `visibilitychange`/`online` 监听，规避 `ProjectLayoutBase` 注释记录过的“僵尸 interval 累积”问题。

---

## 8. 逐条对应 PRD §9 验收（技术达成方式）

- **§9.1 别处新建符合 §2 的本项目会话 5s 内可见**：4s 轮询 `GET /v1/sessions?project_id=id`；新会话发首条消息后 `status` 由 `created→running/idle`，进入前端过滤集（`status!=="created" && task_id==null`），`mergeProjectSessions` upsert。检测 ≤4s + 本地往返/渲染 < 5s；列表在顶部时新条目直接可见（§7.4.4 顶部豁免）。
- **§9.2 别处触发的本项目顶层任务 5s 内出现**：4s 轮询 `GET /v1/projects/{id}/tasks`；新 id 经 `mergeTasks` 入表。
- **§9.3 任务状态任意流转 5s 内更新**：每 tick 取回最新 `Task.status`，id 命中 → 原位更新该行 → StatusPill 重渲染（沿用 `status-tone.ts`，不改文案）；all tab 中 `updated_at` 变化引发的重排由 §7.4 锚定校正吸收。覆盖 active/paused/stopped/completed/blocked 全集。
- **§9.4 空态→有数据自动切换**：列表数据来自 store/state，首条数据到达即由现有渲染条件从空态切列表；同 4s 检测窗口。
- **§9.5 自动更新不跳动/不清输入/不改选中**：见 §7.4——滚动锚点校正（覆盖 all tab 重排）+ 顶部豁免 + 轮询只写列表数据。
- **§9.6 降级静默 + 恢复后自动整表补齐**：`Promise.allSettled` 任一 rejected/超时 → 静默、保留旧列表（§4A.5）；恢复后下一次该接口 `fulfilled` 即以**整表**结果覆盖（§4A.6），遗漏一次补齐。计时起点 = “检测到恢复”：`online`/`visibilitychange→visible` 立即补拉，否则 ≤4s 下一 tick，整体 < 5s。
- **§9.7 跨项目隔离（停在 A，B 新增不出现在 A）**：轮询参数即当前 `id=A`，后端强制 `user_id + project_id=A` 过滤；§4A.4 generation 校验 + `mergeProjectSessions(A, …)` 子集合并保证 A→B 晚返回不写错页面。

---

## 9. 自测要点（编码阶段）

**单测 — hook 执行契约（`use-project-list-auto-refresh.test.ts`，fake timers + mock API）**
- 4s 触发；隐藏标签暂停、`visibilitychange→visible` / `online` 即时补拉。
- **单飞**：上一轮未结束时下一 tick 跳过、不积压。
- **超时/挂起**：mock 一个永不 resolve 的 fetch，断言 `intervalMs` 后 `controller.abort()` 触发失败分支，且**不阻塞**下一轮成功补拉。【覆盖 P1-2 挂起】
- **失败后恢复**：第 1 轮 reject → 列表保留旧值、不清空、不抛；第 2 轮 resolve → 以整表结果回灌补齐。【覆盖 P1-2 失败后恢复】
- **切项目晚返回**：发起 A 请求后把 `projectId` 切到 B，A 的晚返回因 generation 校验**被丢弃**，不写入 B。【覆盖 P1-2 切项目晚返回】
- **allSettled 独立失败**：会话 reject、任务 fulfilled（及反向）→ 成功一侧照常写入、失败一侧保留旧值。
- 卸载/换 id：清 interval + clearTimeout + abort + 移除监听（无泄漏、无晚写）。

**单测 — store（`session-store.test.ts`）**
- `mergeProjectSessions`：子集替换、其它项目不动、引用复用、新增 upsert、消失行剔除、`project_id` 同源断言拒写异项目行。

**单测 — 锚定（`use-list-scroll-anchor.test.ts`，jsdom + 受控 rect/scrollTop）**
- 已下滚时，列表头部插入新行 / 重排已有行后，首个可见行的视觉 top 不变（`scrollTop` 被正确校正）。
- 顶部豁免：`scrollTop≈0` 时不校正，新条目可见。
- 锚点行被删除 → 回退候选 / 不校正，不抛错。

**组件级 — ProjectDetailPage 锚定 / 竞态（`ProjectDetailPage.test.tsx`，render + mock store/api）【采纳 Reviewer P2】**
- **默认 all tab** 下滚到中段，某任务状态更新导致 `updated_at` 变化触发重排后，**首个可见行保持不变**（断言锚点行 DOM 位置/`scrollTop`）。
- chat tab / tasks tab 各跑一遍“新条目插入 + 下滚不跳”。
- **项目 A→B 切换**：A 的晚返回不写入 B 的列表（断言 B 视图不含 A 的条目）。

**集成闸**
- `make test-all && make typecheck && make lint` 全过（无 openapi/i18n 改动，无需 `make generate-types` / `gen_types.py`）。

**浏览器实测（CLAUDE.md 要求 UI 改动先在浏览器验证）**
- 两窗口/两入口造会话与任务，停在 A 详情页观察 5s 内出现/状态更新；**默认 all tab 下滚到中段，从另一窗口改某任务状态（触发 `updated_at` 重排），确认首个可见行不跳**；断网→恢复验证整表补齐；切到 B 验证 A 不串项目。
