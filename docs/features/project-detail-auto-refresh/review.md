# project-detail-auto-refresh Review

## 2026-06-26 PRD 第1轮 — 待修改

审查人：产品经理 Reviewer

意见：

- P0：无。

- P1「任务列表对象未收口」：PRD 在第 2、3、7 节混用“任务被触发”“lead 派发”“任务状态变化”。但产品概念里项目详情页的任务列表应对应顶层 lead-dispatch Task；lead 派发的 member sub-run、Automation run 记录、任务内部 session 不是同一种列表对象。若不改，开发会被迫自行决定到底刷新哪张列表。期望改法：明确本版只刷新 `project_id` 归属当前项目、当前用户可见的顶层任务条目；明确不包含 task 内部 member/subtask run、Automation 运行历史、会话详情消息流。

- P1「会话列表可见条件未定义」：PRD 说“别处新增的归属本项目会话自动出现”，但没有说明出现时机和排除规则。当前产品会过滤空白 `created` 会话和 `task_id != null` 的任务内部 session；如果 PRD 不写清，QA 可能要求刚打开新建对话草稿就出现，或把 task lead/member session 当作会话列表项。期望改法：定义为“项目会话在已有首条有效消息/非 created 状态、且 `task_id == null` 时出现在项目会话列表”，或由产品负责人明确采用其他规则。

- P1「状态语义与验收状态不一致」：PRD 使用“运行中 / 完成 / 失败”，但任务状态实际还存在 paused、stopped、blocked 等用户可见状态；“失败”到底映射 failed、blocked 还是 stopped 没有定义。第 7 条只验收“运行中 → 完成 / 失败”，遗漏暂停、停止、阻塞、恢复等状态变化是否自动更新。期望改法：在 PRD 中列出本版任务列表需要自动反映的状态集合、中文展示语义和不纳入范围的状态；如果 paused/stopped/blocked 也会出现在列表，就必须进入验收。

- P1「准实时 SLA 没有变成可验证验收」：第 6 节写“建议端到端 ≤5 秒，由开发最终确认”，第 7.1 又写“目标 5 秒内”，第 7.2 到 7.4 没有时延窗口。这个不是技术方案细节，而是用户体验和 QA 判定标准。期望改法：产品负责人拍板一个硬性验收口径，例如“正常联网、页面保持打开时，新增会话、新增任务、任务状态变化、空态切列表均需在 5 秒内可见”；如果某类事件只能 best-effort，也要单独写明验收口径。

- P1「权限 / 可见性边界未写入用户故事」：PRD 提到“我、定时任务、lead、另一窗口 / 另一设备”，但没有明确这些变化必须仍受当前登录用户与当前项目权限过滤约束。现有接口按 `user_id` 和 `project_id` 取数；自动更新若引入 SSE/轮询订阅，也必须保持同样边界。期望改法：在用户故事或范围中补一句“仅展示当前登录用户有权访问且归属当前项目的数据；不会展示其他项目、其他用户、任务内部隐藏 session”。

- P1「降级补偿行为过于模糊」：第 5 节写“自动更新通道异常时静默降级；恢复或下次进入页面时通过全量拉取补齐”，但没有定义“恢复”发生在用户仍停留当前页时是否必须自动补齐、多久补齐、失败期间是否继续保留旧数据。期望改法：补验收口径：通道恢复后当前页应触发一次全量刷新并补齐遗漏，或明确只有重新进入页面才补齐；两者只能选一个。

- P2「排序就位与不打断用户操作存在潜在冲突」：PRD 同时要求“按现有排序就位”和“不抢占滚动位置 / 不改变当前选中项”。如果新条目插入当前视口上方，或状态更新导致 `updated_at` 改变并重排，用户看到的列表内容可能跳动但滚动位置数值没变。期望改法：说明自动插入 / 重排时以滚动锚定为准，还是以严格排序为准；必要时要求保持当前可见行锚点不变。

- P2「验收缺少跨项目反例」：范围里说只处理“归属本项目”的会话 / 任务，但验收只写正向新增，没有写“其他项目新增不会出现在当前项目详情页”。期望改法：增加一条验收：用户停在项目 A 详情页，项目 B 新增会话或任务时，项目 A 的会话列表 / 任务列表不出现该条目。

待拍板项：

- 本版“任务列表”是否只指顶层 lead-dispatch Task？Automation run、member sub-run、task lead session 是否全部排除？
- 项目会话列表的出现时机是“session 创建即出现”，还是“首条有效消息落库后出现”？
- 自动更新 SLA 是否统一拍板为正常联网下 5 秒内可见？是否所有正向验收都适用？
- 任务状态集合如何映射到用户可见文案？paused、stopped、blocked 是否纳入本版自动刷新验收？
- 自动更新通道恢复后，当前页面是否必须自动全量补齐，还是只在重新进入页面时补齐？

处理（产品负责人）：

- P1「任务列表对象未收口」→ 已新增 §2，明确项目任务列表只指**归属当前项目的顶层 lead-dispatch Task**；显式排除 member/subtask sub-run、Automation run 历史、会话消息流。Automation 触发出的顶层任务纳入，run 记录本身不纳入。
- P1「会话列表可见条件未定义」→ 已在 §2 定义：`task_id == null` 且 `status != "created"` 且归属本项目的会话才出现；草稿态/任务内部 session 排除。
- P1「状态语义不一致」→ 已新增 §5：列表行状态直接取后端 `status`，沿用现有 StatusPill 文案（不新增/不改文案）；要求**任意 status 变化都自动反映**，不限定子集，覆盖 paused/stopped/blocked；验收以「进行中→完成」「进行中→停止/阻塞」为代表场景。
- P1「准实时 SLA 不可验证」→ 已在 §9 拍板统一硬性口径：正常联网+页面保持打开时，新增会话、新增任务、状态变化、空态切列表、恢复补齐均需 5 秒内可见。
- P1「权限/可见性边界缺失」→ 已在 §3 补「权限/可见性边界」：仅展示当前登录用户有权访问且归属当前项目的数据；自动更新通道沿用 `user_id`+`project_id` 过滤。
- P1「降级补偿模糊」→ 已在 §6 拍板：通道恢复后若用户仍停留当前页，**必须自动触发一次全量刷新补齐**（不依赖重进页面）；§9.6 给出可验证口径。
- P2「排序与不打断冲突」→ 已在 §7 明确「以保持当前可见行的滚动锚点为准，锚定优先于严格排序」。
- P2「缺跨项目反例」→ 已在 §9 新增验收 7：停在项目 A，项目 B 新增不出现在 A 的列表。

本轮结论：待修改 → 已逐条改稿，提交第 2 轮回审。

## 2026-06-26 PRD 第2轮 — 通过

审查人：产品经理 Reviewer

意见：

- P0：无。

- P1：无。

- P2「新增条目可见性的 QA 场景可再补一行」：§7 已明确锚定优先于严格排序，新条目可能需要用户回滚到顶部/对应位置才呈现；§9.1/§9.2 又写“5 秒内能在列表看到”。这轮不阻断，因为对象和锚定优先级已收口，但建议在测试用例或 PRD 验收备注中补充：新增条目验收默认列表处于顶部/排序位置可见；若用户当前视口不在插入位置，则验收“数据已进入列表且不扰动当前可见行”，不强制跳转到新条目。

- P2「恢复补齐 SLA 计时起点可再写死」：§6 和 §9.6 已明确通道恢复后当前页必须自动全量刷新并补齐，不再依赖重进页面。建议把“5 秒内补齐”的计时起点写成“检测到自动更新通道恢复后 5 秒内”，避免 QA 将其理解为“全量刷新完成后再 5 秒内”。

上轮问题闭环确认：

- 第 1 轮 P1「任务列表对象未收口」：已闭环。§2 明确项目任务列表只指归属当前项目的顶层 lead-dispatch Task，并排除 member/subtask sub-run、Automation run 历史、会话消息流。
- 第 1 轮 P1「会话列表可见条件未定义」：已闭环。§2 明确 `task_id == null` 且 `status != "created"`，排除空白草稿和任务内部 session。
- 第 1 轮 P1「状态语义与验收状态不一致」：已闭环。§5 明确状态展示直接取后端 `status`，沿用现有 UI 映射，任意 status 变化都需自动反映；§9 覆盖完成、停止、阻塞代表场景。
- 第 1 轮 P1「准实时 SLA 没有变成可验证验收」：已闭环。§9 统一拍板正常联网、页面保持打开时，正向自动出现/更新类验收 5 秒内可见。
- 第 1 轮 P1「权限 / 可见性边界未写入用户故事」：已闭环。§3 明确当前登录用户有权访问 + 当前项目过滤，自动更新通道沿用 `user_id` + `project_id`。
- 第 1 轮 P1「降级补偿行为过于模糊」：已闭环。§6 明确通道异常静默降级、保留旧列表；恢复后当前页必须自动全量刷新补齐；§9.6 可验收。
- 第 1 轮 P2「排序就位与不打断用户操作存在潜在冲突」：已闭环。§7 明确以当前可见行滚动锚点为准，锚定优先于严格排序。
- 第 1 轮 P2「验收缺少跨项目反例」：已闭环。§9.7 已新增项目 A / 项目 B 的反例验收。

待拍板项：

- 无阻断待拍板项。仅建议产品负责人在进入 PLAN / testcase 前采纳以上 P2 文案增强，降低 QA 解释成本。

处理：

- 本轮无 P0/P1，评审通过；可进入下一阶段。

## [2026-06-26] PLAN 第1轮 — 待修改

审查人：全栈开发 Reviewer

代码核对范围：

- PRD：`docs/features/project-detail-auto-refresh/prd.md`
- PLAN：`docs/features/project-detail-auto-refresh/plan.md`
- 前端真实链路：`frontend/packages/app/src/pages/ProjectDetailPage.tsx`、`frontend/packages/core/src/store/session-store.ts`、`frontend/packages/core/src/api/sessions-api.ts`、`frontend/packages/core/src/api/tasks-api.ts`、`frontend/packages/app/src/layout/ProjectLayoutBase.tsx`
- 后端真实链路：`backend/valuz_agent/api/routes/sessions.py`、`backend/valuz_agent/modules/sessions/service.py`、`backend/valuz_agent/modules/sessions/project_index.py`、`backend/valuz_agent/api/routes/tasks.py`、`backend/valuz_agent/modules/tasks/datastore.py`、`backend/valuz_agent/adapters/event_sse_adapter.py`、`backend/kernel/app/routes/events.py`

意见：

- P0：无。

- P1「锚定不跳动方案基于错误排序假设，不能证明满足 §9.5」：PLAN 多处把列表合并安全性建立在“两接口均 `created_at DESC`、不可变”“status 变化不重排”“稳定 key + 引用复用即可保持浏览器滚动锚点”上（PLAN §1、§3、§7.4、§8.5、§9）。代码核对后该前提不成立：`ProjectDetailPage.tsx` 的默认 `Tabs defaultValue="all"` 下，`ProjectAllList` 对任务使用 `sortAt: tk.updated_at`，对会话使用 `sortAt: s.updated_at`，并执行 `merged.sort((a, b) => b.sortAt - a.sortAt)`；而 `TaskDatastore.update_task_status()` 会在状态变化时写 `updated_at=now_ms()`。因此任务状态变化会让默认「全部」列表发生重排，稳定 `key={id}` 只会移动 DOM 节点，不能保证当前可见行不跳。仅靠浏览器 scroll anchoring 也没有覆盖“重排已有行”的语义。期望改法：PLAN 必须按真实排序补充可落地锚定策略，例如在数据合并前记录滚动容器内首个可见 item 的 `{kind,id,top}`，合并渲染后用 layout effect 校正 `scrollTop`；或明确在用户不在插入/排序位置时延迟呈现会改变顺序的新条目/重排行为。方案和自测必须覆盖默认「全部」tab、会话 tab、任务 tab，以及任务状态更新导致 `updated_at` 变化的场景；如果要把默认列表排序改成 `created_at`，这属于改变现有排序规则，需先回到 PRD/产品确认。

- P1「降级/恢复与 5s SLA 的轮询执行语义不完整」：PLAN 只写“4s tick + catch 静默 + 下一次成功 tick 整表回灌”，但没有规定请求超时、单飞、取消、旧项目响应防写入、两接口并行/独立失败处理。当前 `createFetchJson()` 对普通 REST 请求没有默认 timeout；若一个请求挂起而非 reject，`catch` 不会触发。若实现没有 single-flight，4s tick 可能积压请求；若实现 single-flight 但无 timeout，一个挂起请求会阻塞后续恢复补拉；若页面从项目 A 切到 B 时旧请求晚返回，缺少 generation/projectId 校验会把 A 的结果写进 B 的页面态；若顺序请求两个接口，一个慢/失败接口也可能拖过另一张列表的 5s SLA。期望改法：PLAN 必须把 `useProjectListAutoRefresh` 的执行契约写清楚：每个 projectId 作用域单飞；请求带 `AbortController` 并在卸载/id 变化时 abort；有小于或等于轮询间隔的超时或等价机制；每次响应写入前校验当前 projectId/generation；会话和任务使用 `Promise.allSettled` 或等价并行独立更新，单边失败不阻塞另一边；从失败转为成功时以本次成功整表结果作为 §9.6 的补齐点，并在测试里覆盖挂起、失败后恢复、切项目晚返回。

- P2「调研对现有 60s 新鲜度机制的归因不准」：PLAN §0.4 写 `ProjectLayoutBase` 停留期间有 “60s 兜底 interval + visibilitychange”，并据此说详情页会话列表最快也要 60s 才刷新。代码里 `ProjectLayoutBase.tsx` 的 60s interval 调的是 `refreshFinishedRuns()` 和 `refreshAutomations()`，不是 `fetchSessions()`，也不是详情页本地 `tasks`；`fetchSessions()` 只在挂载和进入 conversation 路由时触发，`fetchAllTasks()` 只在挂载和 `/tasks`/`/projects` 路由切换时触发。这个偏差不推翻“现有机制不满足 5s”的结论，但会误导实现去复用错入口。期望改法：修正 §0.4 的描述，明确现有自动刷新对详情页中心会话/任务列表基本没有覆盖。

- P2「新增 hook 的导出落点漏写」：PLAN 新增 `frontend/packages/core/src/hooks/use-project-list-auto-refresh.ts`，且 `ProjectDetailPage` 当前主要从 `@valuz/core` 导入 core 能力；`frontend/packages/core/src/index.ts` 已 `export * from "./hooks"`，但实际还需要在 `frontend/packages/core/src/hooks/index.ts` 增加 barrel export，否则若按现有导入风格使用会编译失败。期望改法：影响面补 `frontend/packages/core/src/hooks/index.ts`，或明确从相对路径导入。

- P2「测试计划缺少 ProjectDetailPage 级别的锚定/竞态覆盖」：PLAN 的单测集中在 hook 和 session-store，浏览器实测只笼统写“滚动锚不跳”。考虑到默认「全部」tab 的排序由 `ProjectAllList` 自己计算，且任务状态变化会改 `updated_at`，仅测 hook/store 不足以覆盖实际 UI 风险。建议补一个组件级或浏览器级用例：默认 all tab 下滚动到中段，任务状态更新并改变 `updated_at` 后首个可见行保持不变；另补项目 A→B 切换时 A 的晚返回不写入 B。

肯定项：

- 调研主体基本落在真实代码：`ProjectDetailPage`、路由注册、会话/任务 API、后端 `user_id + project_id` 过滤、task SSE 仅 per-task DB polling、kernel 全局流缺 `project_id` 这些链路均核对存在。
- 机制选型方向合理：在当前任务列表无项目级广播、全局 session stream 需反查 project/owner 的前提下，复用既有 REST 列表接口做页面作用域轮询，比新建项目级 SSE 更低风险，也天然沿用后端隔离。
- 数据隔离方向成立：`GET /v1/sessions?project_id` 经 `ProjectSessionRow.user_id/project_id/kind="chat"` 过滤，`GET /v1/projects/{project_id}/tasks` 经 `TaskDatastore.list_tasks(user_id, project_id)` 过滤；不新增事件载荷时，`user_id` 不需要下发给前端做二次过滤。
- api 契约、i18n、会话详情消息流的“不动”判断基本成立：现有 `api/openapi.yaml` 已有两条列表契约；本方案不新增用户可见文案；会话详情仍走 `sessionsApi.subscribeEvents`/`event_sse_adapter`，任务详情仍走 `useTaskEvents`/`tasksApi.eventsStreamUrl`。

结论：

- 本轮结论：待修改。
- 必须修改的 P0/P1：P1「锚定不跳动方案基于错误排序假设，不能证明满足 §9.5」；P1「降级/恢复与 5s SLA 的轮询执行语义不完整」。

## [2026-06-26] PLAN 第2轮 — 通过

审查人：全栈开发 Reviewer（第 2 轮独立评审）

代码核对范围：

- 文档：`docs/features/project-detail-auto-refresh/plan.md`、`docs/features/project-detail-auto-refresh/prd.md`、`docs/features/project-detail-auto-refresh/review.md`
- 前端真实链路：`frontend/packages/app/src/pages/ProjectDetailPage.tsx`、`frontend/packages/core/src/api/fetch-json.ts`、`frontend/packages/core/src/api/sessions-api.ts`、`frontend/packages/core/src/api/tasks-api.ts`、`frontend/packages/core/src/store/session-store.ts`、`frontend/packages/app/src/layout/ProjectLayoutBase.tsx`
- 后端真实链路：`backend/valuz_agent/modules/tasks/datastore.py`

意见：

- P0：无。

- P1：无。

- P2「编码时需把锚定 hook 接到真实滚动容器」：本轮 PLAN 的锚定策略已经从“稳定 key 即可”修正为“记录首个可见 item `{kind,id,top}`，重排后在 `useLayoutEffect` 校正 `scrollTop`”，且明确不改 `ProjectAllList` 的 `updated_at` 排序。代码核对后，项目详情页自身没有列表级 `overflow-y-auto`，实际滚动来自布局层 `ProjectLayoutBase` 的 `contentClassName="overflow-y-auto p-0"`。这不阻断 PLAN，但实现时 `useListScrollAnchor` 必须绑定到实际产生滚动的容器，或把详情页列表包成明确的滚动容器；否则对非滚动元素写 `scrollTop` 会让 §9.5 的浏览器实测失效。现有 PLAN 已把组件级/浏览器级锚定验证列入自测，可在编码阶段兜住。

- P2「锚点捕获时机要按 PLAN 严格实现」：PLAN §7.4 写了“passive scroll 监听 + 数据更新前”记录锚点，重渲染后 `useLayoutEffect` 校正。实现时不能只在数据变更后的 effect 里再取锚点，否则拿到的是新排序后的 top，无法抵消 `updated_at` 重排。建议测试里保留“默认 all tab 下滚到中段，任务状态变化刷新 `updated_at` 后首个可见行保持不变”的断言。

上轮 P1 闭环确认：

- 第 1 轮 P1「锚定不跳动方案基于错误排序假设，不能证明满足 §9.5」：已闭环。PLAN §0.1/§3/§7.4 已承认 `ProjectAllList` 在默认 all tab 按 `updated_at` 重排，且 `TaskDatastore.update_task_status()` 会写 `updated_at=now_ms()`；方案在不改排序规则的前提下改为滚动锚点校正，覆盖 all tab 的 `updated_at` 重排、chat tab 新会话插入、tasks tab 新任务插入，并补了 hook 单测、`ProjectDetailPage` 组件级测试和浏览器实测。

- 第 1 轮 P1「降级/恢复与 5s SLA 的轮询执行语义不完整」：已闭环。PLAN §4A 明确了每 projectId 单飞、每轮 `AbortController`、卸载/换 id abort、`intervalMs` 超时、generation/projectId 防旧响应、会话/任务 `Promise.allSettled` 并行独立失败、单边失败保留旧值、恢复后以本次成功整表结果补齐，并在 §9 自测中覆盖挂起、失败后恢复、切项目晚返回、allSettled 独立失败。

其他上轮 P2 闭环确认：

- `ProjectLayoutBase` 60s 新鲜度归因已修正：PLAN §0.4 明确 60s interval 刷的是 `refreshFinishedRuns()` / `refreshAutomations()`，不是详情页中心会话/任务列表。
- hook barrel export 已补入影响面：PLAN §5 写明新增 `frontend/packages/core/src/hooks/index.ts` export。
- `ProjectDetailPage` 级锚定/竞态测试已补入：PLAN §9 覆盖 all/chat/tasks 三 tab 与 A→B 晚返回。

结论：

- 本轮结论：approve / 通过，可进入编码。
- 残留 P0/P1：无。
