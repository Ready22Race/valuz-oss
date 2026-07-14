# 事件投递统一化 —— 干掉轮询,不丢功能

[English](event-delivery-unification.md)

> 今天桌面客户端为了保持实时 UI 状态,在 SSE 之上叠了 **五套独立的轮询机制**。
> 本文档设计用 **一个模式** 取代它们全部 —— 一条 durable、可按游标续传、
> backfill-then-live 的事件流 —— 并在三个 scope(**用户 / 会话 / task**)上实例化。
> 客户端最终 **零稳态轮询**:每个实时视图都变成事件流的纯投影(projection),
> 所有"漏帧对账"都下沉到服务端、发生在流的内部。
>
> **硬性要求:功能零回归。** §6 是一张详尽的功能对照表 —— 当前每一个轮询提供的
> 行为,都映射到重构后保留它的机制。凡是让 §6 少掉任何一行的改动,都不算完成。
>
> 配套文档:[architecture.md](../architecture.md)(§7 实时更新)、
> [data-service-architecture.md](data-service-architecture.md)(durable 事件存储)、
> [notifications.md](notifications.md)、
> [task-attention-and-reliability.md](task-attention-and-reliability.md)。

---

## 1. 原则

**一份日志、一个游标、一个 reducer —— 投影出来,而不是轮询出来。**

系统其实已经具备根治所需的两个原语:

1. **一个全局单调游标。** `events.id` 是单一自增主键,在 durable 存储的 *所有*
   session 上单调,且 `events.user_id` 是一等索引列
   ([`models.py:118-140`](../../backend/kernel/src/adapters/sqlalchemy_store/models.py))。
   "用户 X 在游标 N 之后的每一个事件"就是一条高效查询 —— 只是今天还没暴露出来。
2. **一个被验证过的 backfill-then-live SSE 循环。** `iter_events_sse`
   ([`event_sse_adapter.py:602-709`](../../backend/valuz_agent/adapters/event_sse_adapter.py))
   已经在做:按游标 backfill 持久化事件,再跟 live bus,用 `seq` 对边界去重,
   2 秒限流补读一次,15 秒心跳。durable 行 id 由 `PersistThenBroadcastSink` 盖进
   live 帧,所以 backfill 与 live 共用同一个游标空间。

这些轮询之所以存在,只是因为上述原语被绑死在 **单个 `session_id`** 上,以及
**`created → running` 这条生命周期边是一次 DB 行改写、不是事件**。把 scope 抬高、
把生命周期放进日志,每个轮询就都能从"推送"里投影出来。

---

## 2. 现状(绝不能回归的东西)

五个轮询 + 两条 SSE 路径 + 两套 *并行* 的 chat 实现。它们都真实存在、都承载行为,
下面的设计必须逐一保留。

### 2.1 五个轮询

| # | 轮询 | 位置 | 频率 | 实际在干什么 |
|---|------|------|------|-------------|
| P1 | **会话状态** | [`ConversationPage.tsx:5113-5260`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | 2 秒 | 桥接 `created→running`:一个不是客户端发起的 session(定时任务触发、刚创建、离开再回来)没有"它开始了"的推送信号,于是 GET `/v1/sessions/{id}` 直到 `running`(→ 订阅 SSE)或 terminal(→ 对账)。single-flight 保护;在 running/terminal/handleSend/unmount 时停。 |
| P2 | **`events?after_seq` 对账** | [`ConversationPage.tsx:5168-5183`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | 一次性 | 恢复一个在**一个轮询间隔内又开始又结束**的 turn(即时失败、缓存秒回),其 terminal SSE 帧落在"拉历史"和"订阅"之间的缝里。一次 `listEvents(sid, maxSeq)`;若 DB 有游标之后的行,就开一个 replay 订阅。 |
| P3 | **桌面内联事件轮询** | [`ConversationPage.tsx:3761-3822`](../../frontend/packages/app/src/pages/ConversationPage.tsx) | 500 毫秒 | 桌面会话在自己的 SSE 流([`:3914-3943`](../../frontend/packages/app/src/pages/ConversationPage.tsx))**之外**又跑一条 500ms `listEvents` 轮询做 gap-fill + idle 对账。(webui 没有 —— 见 §2.3。) |
| P4 | **running 列表** | [`use-running-runs.ts:14,32`](../../frontend/packages/core/src/hooks/use-running-runs.ts) | 10 秒 | 模块级单例轮询 `runsApi.list({status:"running"})` → `RunSummary[]`。驱动侧边栏 running 计数徽标和 Activity 页。`document.hidden` 时跳过;`refreshRunningRuns()` 在一个 session 刚被创建后强制立即 + 1.5 秒补一发(覆盖 `created→running`)。 |
| P5 | **finished 列表** | [`ProjectLayoutBase.tsx:387-442`](../../frontend/packages/app/src/layout/ProjectLayoutBase.tsx) | 60 秒 + 边沿 | `runsApi.list({status:"finished"})` → 与 live runs 合并(按 `session_id` 去重、最新在前)成每个项目的侧边栏 chat/run 列表。在 mount + 1.5 秒重试(以 `liveRunIds` 变化为键)时触发,外加一个 60 秒的可见性门控安全网。 |

同一家族中相邻的流(仅作背景;P 列表才是目标):

- **Task 事件**([`tasks.py:367-448`](../../backend/valuz_agent/api/routes/tasks.py))—— `/v1/tasks/{id}/events/stream?after_seq=`,但实现成 **服务端 0.5 秒 DB 轮询、无 broadcast bus**,15 秒心跳,5 秒 terminal linger。客户端 [`use-task-events.ts`](../../frontend/packages/core/src/hooks/use-task-events.ts) 以 500ms 重连,遇 `stream_end` 关闭,`keepAlive` 让已完成 task 的流继续开着等 `deliverable_updated`。另有 **3 秒 `getTask` 轮询**([`TaskDetailPage.tsx:462-468`](../../frontend/packages/app/src/pages/TaskDetailPage.tsx))拉 run/team/status 元数据。
- **通知**([`use-notifications.ts`](../../frontend/packages/core/src/hooks/use-notifications.ts))—— SSE(`/v1/notifications/stream`)+ **60 秒 REST 兜底**。
- **Activity feed**([`use-activity-feed.ts`](../../frontend/packages/core/src/hooks/use-activity-feed.ts))—— `/v1/activity`,**4 秒 head-poll** + keyset 分页拉更旧的页。

### 2.2 两条 SSE 路径(正确性地板)

- **会话级流。** `createSessionStreamController`
  ([`session-stream.ts:61-188`](../../frontend/packages/core/src/agent/session-stream.ts)):
  逐帧追踪 `lastSeq`,用 `after_seq` 续传,backoff `[1,2,4,8,16]s`,之后进 `error`
  态需手动 `reconnect()`。底层是 `subscribeEvents`
  ([`sessions-api.ts:576-651`](../../frontend/packages/core/src/api/sessions-api.ts))
  → `GET /v1/sessions/{id}/events/stream?after_seq=N`。
- **全局流。** `GET /v1/events/stream`
  ([`events.py:29-64`](../../backend/kernel/app/routes/events.py))—— 给 host 聚合器
  用的进程内 live-only fan-out。**无 `after_seq`、无 user 过滤、无 backfill。**
  今天不是一条 per-user 可回放日志。

轮询为何与 SSE 共存 —— **live 队列是设计上"满则丢"而非阻塞**
([`event_stream.py`](../../backend/kernel/app/event_stream.py)、
[`session_bus.py`](../../backend/kernel/src/core/session_bus.py):"慢消费者绝不能
拖住 runtime 的 emit 路径……需要完整记录的消费者去读 DB")。SSE 是尽力而为的延迟
优化;`events` 表才是系统账本。轮询就是客户端对着这份账本做的对账。

### 2.3 两套并行的 chat 实现(重要)

- **webui** 用干净的 Zustand reducer
  [`chat-store.ts`](../../frontend/packages/core/src/store/chat-store.ts):
  单一 `_ingest`/`reduce` 漏斗(`:336-650`),attach = 历史回放 + live SSE 走
  *同一个* reducer(`:140-194`)。这是模板。
- **desktop** [`ConversationPage.tsx`](../../frontend/packages/app/src/pages/ConversationPage.tsx)
  (5000+ 行)**不用** `chat-store` —— 它用本地 `useState` 重写了 SSE、`appendEvent`、
  gap-fill、`buildTurns`,以及全部五/三个轮询。loading UI(`Stop` 按钮、
  `LogoShimmer`、"已处理 X 秒"计时器)是 `isBusy = deriveTurnActive(sending, status)`
  ([`conversation-loading.ts:45-48`](../../frontend/packages/app/src/pages/conversation-loading.ts))
  = `sending && !isTerminalSessionStatus(status)` —— 一个本地乐观标志,被本地镜像的
  session 状态 AND 门控。

桌面分叉既是轮询的最大来源,也是最大的简化机会:把它收敛到 reducer 上,本身就是
根治的一部分。

### 2.4 run 生命周期事件:只有一半在日志里

事件词表是**封闭枚举**
([`events.py:10-59`](../../backend/kernel/src/core/events.py)):

- ✅ `user_message` —— 持久化,turn 开始时发
  ([`orchestrator.py:562-573`](../../backend/kernel/src/core/orchestrator.py))。
- ✅ `session_update{status}` —— 持久化,turn 结束时发
  ([`orchestrator.py:602-607`](../../backend/kernel/src/core/orchestrator.py))。
- ✅ `session_idle` / `session_error` —— runtime 的 terminal 信号。
- ❌ **没有 `session_created`。** session 创建不发任何事件。
- ❌ **没有 `run_started`。** `created → running` 是 `session.status="running";
  save_session()` —— 一次**行改写**
  ([`orchestrator.py:528-529`](../../backend/kernel/src/core/orchestrator.py)),
  不是事件。

因此 `GET /v1/runs` 是对 `sessions.status` 的**投影**
([`runs/service.py:157-243`](../../backend/valuz_agent/modules/runs/service.py):
`_RUNNING={running,paused}`、`_FINISHED={idle,completed,stopped,blocked,failed}`,
外加 `_effective_status` 里的 task 状态叠加),不是对日志的聚合。**这个缺口就是
列表被轮询的全部原因** —— 列表相关的状态迁移推不出去,因为它们不是事件。

---

## 3. 根因

五个轮询归结为 **两个缺失的能力**,而不是五个问题:

1. **订阅之前没有推送。** SSE 是 per-session 的,要开它得先知道 session 在跑 ——
   但"它开始了"没有推送通道。P1/P4 存在纯粹是为了轮这条边。(鸡生蛋。)
2. **生命周期不在日志里。** `created→running` 和 session 创建是行改写,所以跨
   session 的列表状态(P4/P5)和快 turn 边沿(P2)只能靠定时重读 DB 恢复。

P3 是桌面分叉在 #1/#2 之上加的冗余。

补上这两个能力,五个轮询就无事可做了。

---

## 4. 目标架构

**一个模式、三个 scope、两份日志、一个客户端 reducer。** 传输层 SSE(已定:复用现有
基于 fetch 的 SSE 栈;不引入 WebSocket)。

### 4.1 Scope —— 控制面 vs 数据面

一个用户同一时刻常有 **多个并发 session**(并行聊天,以及一个 task 的 **lead + N 个
member 子 run** 同时流式)。这个事实逼出了拆分:

| Scope | 过滤键 | 日志 | 生命周期 | 载荷 | 取代 |
|-------|--------|------|----------|------|------|
| **控制面** | `user_id` | kernel `events` | **常驻,1 条连接** | **仅**生命周期(`session_created`、`run_started`、`session_update`/`idle`/`error`、todos 摘要)—— **无 token delta** | P1、P4、P5 |
| **会话数据面** | `session_id` | kernel `events` | **按需**(在看的聊天) | 全量 transcript,含 `*_delta`、tool 卡片 | P2、P3 |
| **Task 数据面** | `task_id`(+ 下钻 `session_id`) | host `valuz_task_event` | **按需**(在看的 task) | plan/dispatch/review 叙事(+ 下钻时某 member 的 transcript) | task 0.5 秒轮询、`getTask` 3 秒 |

边界为何这么划:

- **控制面必须是用户级**,因为 N 个并发 run 不可能各自被预订阅(根因 #1)。一条
  user-scoped 流是*唯一*能同时看见它们全部的东西,也正是它为*任意* session 推送
  `created→running` 边 —— 从而灭掉 P1 的鸡生蛋。
- **控制面排除 delta 是硬约束,不是优化。** 在 task fan-out 下一个用户有
  `lead + M 个 member` 同时流式;若控制面带 delta,就是把 `M+1` 条 token firehose
  挤到一条连接上,而它服务的状态(徽标、列表)一个 delta 都不需要。
- **task member 是第三个 scope,而且它已作为一等 `task_id` 日志存在**
  (`valuz_task_event`,由 `valuz_task_session` / `LiveMemberRegistry` 索引)。task
  详情视图要看 lead + 全部 member —— 所以"数据面 = 一个 session"太窄了;它是
  `{session_id, task_id}`。
- **控制面保持单日志、单游标**(仅 kernel session 生命周期)。task 叙事留在按需的
  task 流里,这样我们绝不把两个自增序列合并进一个控制游标。属于某个 task 的列表行,
  在客户端 reduce 里用现成的 `get_task_links_by_session_ids` join
  ([`datastore.py:399-420`](../../backend/valuz_agent/modules/tasks/datastore.py))
  **折叠到该 task 之下** —— 这是投影的事,不是传输的事。

```
  ┌──────────────────── 一条常驻连接 ─────────────────────────────────────────┐
  │  GET /v1/stream?after=<cursor>        (user_id scope, 仅生命周期)          │
  └───────────────────────────────────┬────────────────────────────────────────┘
                                       ▼
                         useEventLogStore  (单一 _ingest / reduce)
        ┌───────────────┬──────────────┼───────────────┬──────────────────┐
        ▼               ▼              ▼               ▼                  ▼
   runningRuns     finishedRuns    会话状态          通知              activity
   (member 经 session→task join 折叠到其 task 下)

  ┌── 按需,只给屏幕上的东西开 ──────────────────────────────────────────────┐
  │  GET /v1/sessions/{id}/events/stream?after_seq=N     (在看的聊天)          │
  │  GET /v1/tasks/{id}/events/stream?after_seq=N        (在看的 task)         │
  └───────────────────────────────────┬────────────────────────────────────────┘
                                       ▼  同一个 _ingest,同一个 reducer
                    per-session transcript · task plan/叙事
```

### 4.2 三者共用的那一个模式

每个 scope 都是 `iter_scoped_events_sse(scope_filter, type_projection)` 的一次
实例化,镜像今天的 `iter_events_sse`:

1. backfill 客户端游标之后的持久化行(`WHERE <scope> AND id > after ORDER BY id`)。
2. 跟该 scope 的 live bus/tap。
3. 用 durable `seq` 对 backfill↔live 边界去重。
4. 限流补读(2 秒)+ 心跳(15 秒)。

客户端侧是一个 `_ingest` 漏斗(把 `chat-store` 的 reducer 泛化)喂给各视图 selector。
**从客户端视角这就是纯推送;所有对账是服务端在流内部做的限流 backfill。**

---

## 5. 改动

### 5.1 后端

1. **发 `run_started` 生命周期事件**(补上根因 #2),持久化,加入 `EventType` 枚举 +
   SSE 翻译表
   ([`event_sse_adapter._translate_kernel_event`](../../backend/valuz_agent/adapters/event_sse_adapter.py))
   + `api/openapi.yaml`。在 `created→running` 边
   ([`orchestrator.py:528`](../../backend/kernel/src/core/orchestrator.py))发出,
   携带 `{session_id, title, project_id, task_id, current_todo, updated_at}` 供列表
   投影用。覆盖客户端从未发起的定时任务 run。(`session_created` 刻意**不发** ——
   会话列表继续由 REST + mutation 驱动;见 §7 范围。)
2. **跨 session 读。** 新增 `StorePort.get_events_after_for_user(user_id,
   after_seq, limit)` —— 把 session-scoped 的
   [`get_events_after`](../../backend/kernel/src/adapters/sqlalchemy_store/store.py)
   去掉 `session_id` 过滤。给 `events` 加 **`(user_id, id)` 复合索引**(今天只有
   `user_id` 单列 + `(session_id, …)`)—— 可逆 Alembic 迁移。
3. **user-scoped live tap。** 把 `GlobalQueueTap`
   ([`events.py`](../../backend/kernel/app/event_stream.py))泛化成按 user 过滤的
   fan-out(tap 已经收到 `(session_id, event)`;event 带 `user_id`)。
4. **`iter_user_events_sse`** —— §4.2 的模式在 user scope、仅生命周期投影。挂
   `GET /v1/stream?after=<cursor>` 到 host,按调用者鉴权(基于 fetch 的 SSE 带
   `Authorization`)。
5. **把 task 流收敛到同一模式。** 给 `valuz_task_event` 加 broadcast bus/tap,用同样
   的 backfill-then-live 循环替掉 0.5 秒服务端 DB 轮询
   ([`tasks.py:367-448`](../../backend/valuz_agent/api/routes/tasks.py))。把
   `getTask` 3 秒元数据轮询折进同一条流上的 task 生命周期事件。
6. **守住 durable-seq 不变量。** 权威游标是 **durable** 存储的 `id`;绝不把
   `kernel.db` 缓冲 seq 泄漏到线上(WriteThroughStore `authority=durable` +
   `PersistThenBroadcastSink`)。加契约测试:user-stream 帧 `seq` == durable 行 `id`;
   backfill↔live 去重成立。

### 5.2 前端

1. **`UserStreamController`** —— 把 `createSessionStreamController`
   ([`session-stream.ts`](../../frontend/packages/core/src/agent/session-stream.ts))
   泛化成一条 app 级连接到 `/v1/stream?after=<cursor>`,一个全局 `lastSeq` 持久化到
   `localStorage`,同样的 `[1,2,4,8,16]s` backoff + 续传。
2. **`useEventLogStore`**(Zustand)—— 一个 `_ingest` 漏斗,reducer 从
   [`chat-store.reduce`](../../frontend/packages/core/src/store/chat-store.ts)
   照搬。持有 `bySession`(transcript/streaming/status/todos)+ 派生的
   `runningRuns`/`finishedRuns`(member 经 session→task join 折叠)。
3. **一切变 selector,轮询删除:**
   - `selectTurnActive(sessionId)` 取代 `deriveTurnActive(sending, status)` ——
     loading = "日志里这个 session 有一个未终结的 `run_started`",纯投影。从根上
     打断 `sending`↔SSE-open 的耦合。
   - `selectRunningRuns()` / `selectFinishedRuns()` 取代 P4 + P5。
   - per-session transcript = `selectSession(id)`。
4. **收敛桌面分叉。** 桌面 `ConversationPage` 和 webui `ChatPage` 都消费该 store;
   删桌面内联 SSE + P3 500ms 轮询 + P1 2s 状态轮询 + P2 `reconcileFinishedTurn`。
   5000 行的文件坍缩成 selector。

### 5.3 冷启动 hydration

保留"冷启动用 REST 快照,之后跟流"—— 但只作为**冷启动、不是稳态轮询**。客户端持久化
游标;重连时发 `after=<cursor>`,服务端回放缺口。首次打开用**有界** backfill(近窗);
深层 per-session 历史仍按需懒加载,走现有 `listEvents(id, 0)`(reducer 对回放和 live
的摄入完全一致)。全局 tape 只扛生命周期 + 近期活动,所以即便历史很大也保持廉价。

---

## 6. 功能对照表 —— 一样都不能丢

当前每一个轮询/流提供的行为,以及重构后保留它的机制。**这张表就是验收标准。**

| # | 当前行为(来源) | 由谁保留 | 备注 |
|---|-----------------|----------|------|
| **A. 会话视图** ||||
| A1 | 实时 token/thinking delta、tool-call 卡片 | 会话数据面流(`session_id`,全量载荷) | 传输不变;delta 留在 session 流,绝不上控制面 |
| A2 | 打开时历史回放(`listEvents after=0`) | 同一调用,由共享 reducer 摄入 | webui 已如此;桌面收敛过来 |
| A3 | 刷新/离开再回来的中途续流 | 会话流以 `after=maxSeq` 打开 | 同一 `after_seq` 游标;保留 controller backoff |
| A4 | **快 turn**(一个窗口内 created→running→idle)能渲染(P2) | `run_started` + terminal 都是控制面上的持久化生命周期事件;会话流打开时从 `after` backfill | P2 修补的那条缝不复存在 —— 流从游标打开、服务端 backfill,客户端无需对账 |
| A5 | **定时任务 / 外部启动**的 session 被接管(P1) | 控制面为任意 session 推 `run_started` → 客户端开会话流(或在看的那条已开) | 鸡生蛋消失;无状态 GET 循环 |
| A6 | loading UI(Stop / LogoShimmer / 已处理 X 秒)准确,不卡死,不因漏 terminal 帧被搁浅 | `selectTurnActive` = tape 里有未终结的 run;terminal 帧从(从未关闭的)会话流到达,否则服务端 backfill 补上 | 取代 `deriveTurnActive(sending,status)`;对 status 的 AND 门控被吸收,因为 status 本身也是投影 |
| A7 | todos 实时更新 | `session.todos.update` 事件(不变)+ 控制面 todos 摘要 | |
| A8 | 中断/停止 → `stop_reason` 盖章 | reducer terminal 处理(`chat-store.reduce` session.idle/run.failed) | 原样搬进 `useEventLogStore` |
| A9 | 漏帧正确性(满则丢队列 → DB 对账)(P3) | 每条 scoped 流**内部**的服务端限流 backfill(2 秒补读) | 对账下沉到服务端;客户端停止轮询,但正确性地板完全相同(同一 DB、同一游标) |
| A10 | 重连/backoff 韧性 | `UserStreamController` + 会话 controller,同样 `[1,2,4,8,16]s` | |
| A11 | turn 结束时排队输入 drain | reducer streaming true→false 边沿 → `refreshQueue`(不变) | |
| **B. 侧边栏 / 列表** ||||
| B1 | running 计数徽标(P4 `count`) | tape 上的 `selectRunningRuns().length` | |
| B2 | 每项目 chat/run 列表,running+finished 合并、最新在前(P5) | `selectRunningRuns` + `selectFinishedRuns`,同样按 `session_id` 去重合并 | 合并逻辑从 `ProjectLayoutBase` 移进 selector |
| B3 | `document.hidden` 跳 tick(省电) | 不适用 —— 没有 tick 可跳;流空闲即廉价(仅心跳)。可选:隐藏时暂停流 | 严格更好:根本没有周期性唤醒 |
| B4 | 创建 session 后的立即 nudge(`refreshRunningRuns`) | 控制面上推 `session_created`/`run_started` | nudge 是为了抢 10 秒周期;现在没有周期 |
| B5 | `RunSummary` 富化(title、current_todo、last_output、last_event、updated_at) | 由 `run_started` 携带 + 从后续生命周期/todos 事件 reduce;深字段按需懒取 | 契约:`run_started` payload 必须带列表渲染字段(设计钉项 §8) |
| **C. Activity 页** ||||
| C1 | running + finished + task lead 交织 | 同样的 selector + 控制/task 面上的 task 生命周期;task lead 经 join 折叠 | |
| C2 | head-poll 拉新 + keyset 分页(旧) | live head 走流;**旧页 keyset 分页仍走 REST**(`/v1/activity`) | 历史分页不是轮询 —— 是按需翻页;原样保留 |
| **D. 通知** ||||
| D1 | 收件箱流(snapshot/added/updated/resolved) | 把通知事件折进控制面,或保留专用流 | 已是 SSE;收敛或保留皆可 —— 两种都无回归 |
| D2 | 未读徽标 | 通知条目上的 selector(store 不变) | |
| D3 | 60 秒 REST 兜底 | 由可回放的控制面取代(无丢帧缺口需兜),或作为廉价双保险保留 | |
| **E. Task 详情** ||||
| E1 | plan DAG 面板实时 | task 数据面流(`task_id`,`valuz_task_event`)backfill-then-live | 用 bus 取代 0.5 秒服务端 DB 轮询 |
| E2 | dispatch/review 叙事事件 | 同一 task 流(事件集不变) | |
| E3 | member 进度(多个 member session) | task-scoped 流携带全部 member 的生命周期;`LiveMemberRegistry` 不变 | 多 member 由 task scope 承接,不进控制面 firehose |
| E4 | 下钻某个 member/lead transcript | 按需为该 member 开 `session_id` 数据面流 | 复用同一会话流 |
| E5 | 完成后跟进(`keepAlive` → `deliverable_updated`) | task 流在已完成 task 上保持开着(现有 `keepAlive` 语义) | 保留 |
| E6 | `getTask` 3 秒元数据(run/team/status) | task 流上的 task 生命周期事件 | 元数据推送取代 3 秒轮询 |
| **F. 横切** ||||
| F1 | 多窗口独立 | 每个窗口持有自己的控制 + 数据流 | 可选后续:leader 选举共享流(如今 `use-running-runs` 单例的做法) |
| F2 | 临时沙箱:沙箱死了仍能读历史(remote 模式) | 历史读经 `DataService`/`DataServiceReadClient`,不变;活着时 live delta 走流 | scoped 流从 durable backfill,正是今天那条沙箱无关路径 |
| F3 | durable-seq 权威(绝不泄漏 `kernel.db` 缓冲 seq) | 契约测试 + `authority=durable` 不变量(§5.1.6) | |
| F4 | 流上的鉴权 | 带 `Authorization` 头的 fetch-based SSE(现有 `fetchEventSource`) | 仍不用原生 `EventSource` |

**验收:** 只有当 A1–F4 每一行都被证明保留(UI 行按 [CLAUDE.md](../../CLAUDE.md) 浏览器
验证,后端行以契约测试),这次改动才算完成。

---

## 7. 迁移 —— 增量,同一终态

### 已确认范围(本轮)

承诺目标是 **会话 + 列表五个轮询(P1–P5)**,面向 **OSS 本地优先** 部署。锁定的决定:

- **`run_started` payload** = `{session_id, title, project_id, task_id,
  current_todo, updated_at}`(B5 渲染的最小集)。写 selector 前在 `openapi.yaml`
  钉死。
- **`session_created` 不发。** 会话*列表*继续由 REST + mutation 驱动(低频:
  创建/删除/改名);控制面只推 run 生命周期。少一个新事件类型,tape 更瘦。
- **`seq = durable events.id`**(已验证 per-store 全局单调)。现在不加专门 `seq` 列
  —— 未来路径见 §8 游标契约。
- **SaaS 减压阀(§9.4)暂不做**:OSS 就是 1~3 条连接。§9 里唯一保留为硬闸门的是
  **no-DB-hold 不变量**(§9.2)。

阶段(不做大爆炸式重写;每阶段可发布,收敛到 §4):

1. **后端,暂无消费者。** `run_started` 事件;`get_events_after_for_user` +
   `(user_id,id)` 索引;`iter_user_events_sse` + `GET /v1/stream`;契约测试(F3 +
   no-DB-hold §9.2)。客户端零变化。
2. **列表先行(最低风险)。** `UserStreamController` + `useEventLogStore` + 列表
   selector。把 P4/P5 翻成 selector;**删 P4、P5**。验证 B1–B5、C1。
3. **收敛会话视图。** 桌面 `ConversationPage` + webui `ChatPage` 消费 per-session
   selector;**删 P1、P2、P3**;`sending` 变 selector。验证 A1–A11。

终态(本轮):**一条常驻控制流 + 按需 per-session 数据流;一个 store;一个 reducer;
会话+列表面客户端零轮询;所有对账在服务端流内部。**

### 暂缓(不在本轮)

- **task 流收敛**(E1–E6):给 `valuz_task_event` 加 bus + backfill-then-live,替掉
  0.5 秒 task 轮询 + 3 秒 `getTask` 轮询。task 数据面 scope(§4.1)本就容得下,只是
  这次不切。
- **通知 / activity** 折进控制面(D1/D3、C2)。
- **SaaS 扩展**(§9.4 减压阀、专门 `seq` 列、Redis fan-out)。

---

## 8. 风险、取舍、待钉的开放问题

- **冷启动 / backfill 有界。** 大历史用户的 `after=0` 回放必须限窗 + 懒加载 per-session,
  否则就是全表过滤 —— 所以 `(user_id,id)` 索引是硬要求,且控制面必须排除 delta。
- **`run_started` payload 契约。** 列表投影(B5)依赖 `run_started` 携带渲染字段
  (title/project/task/current_todo)。开工写 selector 前,在 `openapi.yaml` 里钉死
  确切 payload。
- **Member `user_id`(已核实)。** member 子 run 在 task 所有者名下创建 ——
  `build_member_session(..., user_id=task_row.user_id)` 然后
  `kernel_client.create_session(user_id, member_session)`
  ([`dispatcher.py:197-250`](../../backend/valuz_agent/modules/tasks/dispatcher.py))。
  所以 member **会**流进用户控制面,列表 reduce **必须**用
  `get_task_links_by_session_ids` join 把它们折叠到其 task 之下 —— 否则侧边栏会冒出
  一堆 member 行。这是 B2/C1 的 load-bearing 不变量,不是优化。
- **控制面单游标决策。** 我们刻意让控制面只挂一份日志(kernel session 生命周期),
  从而只有一个游标。被否决的替代方案 —— 把 session + task 两份日志合进一条控制流 ——
  需要双游标 envelope(`after_session=X&after_task=Y`);此处记为"考虑过、为简单性
  否决"。
- **游标契约(不要硬编码 `events.id`)。** 设计依赖的是一个抽象、可续传、
  per-durable-store 的**单调 `seq`**,而不是"PK 是全局自增整数"。今天
  `seq == durable events.id`、两者重合;wire 契约本就用 `seq`(跨存储身份是
  `event_uid`,永远不是 `seq` —— 见 [[valuz-event-seq-two-stores]])。把它定成一条
  契约:*`seq` 是 per-store 的单调值,持久化时由 DB 赋值,盖到 live 帧上*。若未来
  durable 后端(分片/分区 PG、UUID 主键、雪花 id)使 PK 不再单调,就加一列专门的
  `seq BIGINT` —— 爆炸半径是"这一列怎么填",**不是**架构。per-session 数据面流无论
  如何免疫(per-session 单调性随手可得);只有控制面的单游标依赖 store-全局的单调
  `seq`,而即便这点也能优雅降级成一个专门的低频生命周期序列或 REST 重播种。
- **Live-only delta(`seq: null`)不回放。** 重连可能丢分词粒度,但绝不丢里程碑
  (里程碑都持久化)。与今天一致;可接受。
- **两套 chat 实现收敛为一。** 这是对一个 5000 行文件的大 diff;这也正是重点 ——
  桌面分叉正是大部分轮询和分歧所在。

---

## 9. 部署与连接扩展性

一条常驻的 user 级 SSE 连接,用"一条挂着的连接"取代了"短、快速返回的轮询"。自然的
担忧是——"这会不会在 SaaS 部署上造成服务端连接压力?"——这担忧是合理的,但方向多半
与直觉相反。本节是诚实的账。

### 9.1 "轮询快速返回 = 更轻" 通常是反的

轮询每次请求短,但**每一 tick 都付全套请求成本,无论有没有变化**:TLS、auth 中间件、
路由、一次 DB 查询、序列化。单是桌面 500ms 轮询,一个打开的会话就是 ~120 次/分钟的
纯浪费;叠上 2s 状态 + 10s runs + 60s finished,一个活跃用户就是一条**恒定 QPS 地板**,
正比于 `用户数 × 轮询频率`,与是否有活动无关。

在 **async ASGI** 服务器(FastAPI/uvicorn)上,一条空闲 SSE 连接是一个**停在 I/O 上的
协程**——几 KB 内存,不占线程、不占 CPU,外加 15s 一次心跳小写。它的成本正比于**真实
事件量**,不是时间。所以这笔交易是**用更高的稳态连接数,换低得多的稳态 QPS / DB 负载**
——而在 async 架构上,连接数是便宜那根轴,QPS/DB 是贵那根轴。对常见的空闲/等待场景,
统一流比现在的轮询**便宜得多**,不是更贵。

### 9.2 load-bearing 不变量:流绝不能长持池化 DB session

真正能把 SaaS 后端打垮的失效模式,不是"连接多"——而是**每条连接在整个生命周期里钉住
一个池化 DB session**(`N` 条流 ⇒ 长持 `N` 个 DB 连接 ⇒ 连接池耗尽)。这就是
[[diagnosis-leak-vs-occupancy-lesson]] 那类连接池白屏(一次 SSE 僵尸泄漏)。

现状的 `iter_events_sse` **没有**踩这个雷,新的 user 级流必须继承同样的纪律:

- **backfill 是逐次离散调用**——`list_events_after → _history_reader().get_events(...)`
  ([`event_sse_adapter.py:626`](../../backend/valuz_agent/adapters/event_sse_adapter.py)):
  开 → 读 → 关,每次 backfill 一次,读与读之间绝不长持。
- **live 路径是内存队列 tap**(`subscribe_session_events`),不是 DB 游标。

所以连接数永远不会被放大成 DB 连接数。**契约测试:一条空闲 SSE 连接持有零个池化
DB session。** 它与 F3 的 durable-seq 测试并列,作为硬性闸门。

### 9.3 真正会疼的地方:只在多租户 SaaS

对 **OSS 本地优先目标**(一个用户、一个后端进程),这是**非问题**——总共 1~3 条连接,
而且净服务端负载比它取代的轮询*更低*。连接压力只在**商业多租户 SaaS 版**才成立,而
每个压力点都有标准解法:

| 压力点 | 成因 | 缓解 |
|--------|------|------|
| **fd / socket 上限** | `N 用户 ×(1 控制 + K 数据)`条常驻 socket 吃满文件描述符 / 临时端口 / LB 连接上限 | 调 ulimit + LB max-conn;**空闲回收**(§9.4)把并发压到*活跃*用户而非*全部*用户 |
| **LB / 代理空闲超时 + 缓冲** | nginx / ALB / Cloudflare 在 ~60s 砍空闲连接;有些 CDN 缓冲 `text/event-stream` | 15s 心跳(已有)压在超时线内;对 `text/event-stream` 关闭代理缓冲 |
| **进程内 tap 的跨副本扇出** | live tap 在进程内;多副本部署时用户的连接必须落到持有其事件的进程 | 按 `user_id` 粘性路由,或用 **Redis pub/sub tap** 做跨副本扇出——属 SaaS overlay;OSS 单租户永不触及 |
| **慢消费者占内存** | 客户端读得慢,事件在服务端堆积 | 已由"满则丢 + DB 对账"背压覆盖(§2.2) |

### 9.4 要主动装的减压阀

三个杠杆,都因为流可按游标续传而很廉价:

1. **空闲回收 + 游标续传。** tab 隐藏或超过一段无活动就关掉控制流;聚焦时用
   `after=<cursor>` 重开(亚秒级)。这把并发连接数从"每个登录用户"降到"每个*活跃*
   用户"——SaaS 最大的杠杆。
2. **每用户连接合并。** 一个用户一个浏览器只开**一条**控制流(leader 选举 /
   `BroadcastChannel`),而非每 tab 一条——正是今天
   [`use-running-runs`](../../frontend/packages/core/src/hooks/use-running-runs.ts)
   已在用的单例套路。
3. **no-DB-hold 不变量 + 契约测试**(§9.2)——防止连接数变成 DB 连接数。

### 9.5 净结论

- **OSS:** 严格*低于*它取代的轮询的服务端负载(QPS / DB 大降);连接数个位数。无需担忧。
- **SaaS:** 连接数上升,但 async 架构本就是干这个的(C10K);真正要做的是 fd 上限、
  LB 超时、跨副本扇出——都是标准运维项,不是架构缺陷。只要 §9.2 守住,连接多**并不**
  等于资源耗尽。
