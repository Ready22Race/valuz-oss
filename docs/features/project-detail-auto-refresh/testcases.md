# 项目详情页列表自动刷新 E2E 测试用例

> 来源 PRD：`docs/features/project-detail-auto-refresh/prd.md`
> Feature slug：`project-detail-auto-refresh`
> 设计阶段：编码前测试用例先行

## 1. 测试范围与验收口径

- 覆盖 PRD §9 的 7 条用户视角验收标准。
- 覆盖 PRD §2 的列表对象定义：项目会话列表、项目任务列表，以及明确排除对象。
- 覆盖异常/降级恢复、跨项目隔离、权限边界、锚定规则与回归点。
- 统一 SLA：正常联网且项目详情页保持打开时，正向自动出现/更新必须在 5 秒内对用户可见。
- 新增条目可见性口径引用 PRD §9：当列表处于新条目排序位置时，用户应在 5 秒内直接看到新条目；若当前视口不在插入位置，验收为“数据已进入列表且不扰动当前可见行”，不强制自动跳转到新条目，且以 PRD §7 的锚定优先。

## 2. 通用测试准备

- 测试用户：`U1`，具备项目 `Project A`、`Project B` 的访问权限；另准备无权限用户 `U2`。
- 项目数据：
  - `Project A`：用于主验证。
  - `Project B`：用于跨项目隔离。
  - `Project Empty`：初始无会话、无任务，用于空态切换。
- 浏览器环境：
  - `Browser-1` 登录 `U1` 并停留在目标项目详情页。
  - `Browser-2` 或后台触发工具用于“别处”创建会话、触发任务、修改任务状态。
- DB/日志验证：
  - DB 需能查询 session/task 的 `project_id`、`user_id`、`task_id`、`status`、创建时间、任务层级/类型、Automation run 记录。
  - 日志需能确认自动更新事件、轮询/SSE 请求、通道异常、通道恢复、恢复后一次全量刷新，以及没有前端报错。
- 计时口径：
  - 新增会话/任务：以 DB commit 或后端成功返回创建/触发结果的时间为 `T0`。
  - 任务状态变化：以后端成功写入新 `status` 的时间为 `T0`。
  - 通道恢复补齐：以日志中“检测到通道恢复”的时间为 `T0`。
  - 预期中的“5 秒内”均指 `T0 + 5s` 前满足用户可见或不可见要求。

## 3. E2E 用例

### E2E-AR-001：本项目符合规则的新会话在 5 秒内自动出现

- 覆盖验收：PRD §9.1
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 会话列表处于新会话排序位置可见，或记录当前可见行作为锚点。
  - 自动更新通道正常。
- 操作步骤：
  1. 在 `Browser-2` 或后台接口中，以 `U1` 创建一条归属 `Project A` 的会话。
  2. 确保该会话满足 PRD §2：`task_id == null` 且 `status != "created"`。
  3. 从创建成功/DB commit 时开始计时。
  4. 不刷新、不切换项目、不重进页面，观察 `Browser-1` 的项目会话列表。
  5. 查询 DB 确认 session 的 `project_id = Project A`、`user_id = U1`、`task_id is null`、`status != "created"`。
  6. 查看日志确认自动更新事件或刷新请求发生且没有前端错误。
- 预期结果：
  - 5 秒 SLA：`Browser-1` 在 `T0 + 5s` 内自动展示该新会话。
  - 新增条目可见性口径：若列表当前视口包含排序位置，新会话直接可见；若当前视口不在插入位置，数据已进入列表且当前可见行不被扰动，不强制跳转。
  - 无需任何用户手动刷新动作。
  - DB 与日志能证明该条目符合 PRD §2 的项目会话列表可见规则。

### E2E-AR-002：本项目定时任务触发出的顶层任务在 5 秒内自动出现

- 覆盖验收：PRD §9.2
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 自动更新通道正常。
  - 存在可触发 `Project A` 顶层 lead-dispatch Task 的定时任务/Automation 配置。
- 操作步骤：
  1. 通过定时触发或测试辅助入口触发 `Project A` 的任务。
  2. 记录顶层任务创建成功/DB commit 时间为 `T0`。
  3. 不刷新 `Browser-1`，观察任务列表。
  4. 查询 DB 确认新 Task 归属 `Project A`、属于顶层 lead-dispatch Task，且不是 member/subtask sub-run。
  5. 查看日志确认该任务来源为定时/Automation 触发，并有自动更新事件或刷新请求。
- 预期结果：
  - 5 秒 SLA：`Browser-1` 在 `T0 + 5s` 内能看到该顶层任务。
  - 新增条目可见性口径：若排序位置在当前视口则直接可见；若不在当前视口，任务已进入列表且当前可见行不跳动、不被挤走。
  - Automation run 历史记录本身不作为任务列表条目展示。
  - DB 与日志能证明展示的是顶层 lead-dispatch Task，而非运行历史或内部 sub-run。

### E2E-AR-003：本项目手动 kickoff 触发出的顶层任务在 5 秒内自动出现

- 覆盖验收：PRD §9.2
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 自动更新通道正常。
  - 存在可手动 kickoff 的项目入口或测试辅助命令。
- 操作步骤：
  1. 在 `Browser-2` 或后台工具中对 `Project A` 执行手动 kickoff。
  2. 记录顶层任务创建成功/DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 不操作，观察任务列表。
  4. 查询 DB 确认新增 Task 是 `Project A` 的顶层 lead-dispatch Task。
  5. 查看日志确认手动 kickoff 触发成功，且自动更新链路正常。
- 预期结果：
  - 5 秒 SLA：`Browser-1` 在 `T0 + 5s` 内看到该新任务。
  - 新增条目可见性口径：排序位置可见时直接出现；排序位置不在视口时，数据进入列表且不扰动当前可见行。
  - 不需要刷新页面、切换项目或重进页面。
  - DB/日志显示该条为项目顶层任务，符合 PRD §2 的任务列表对象定义。

### E2E-AR-004：lead 派发出的本项目顶层任务在 5 秒内自动出现

- 覆盖验收：PRD §9.2
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 自动更新通道正常。
  - 存在能产生顶层 lead-dispatch Task 的 lead 派发入口。
- 操作步骤：
  1. 在其它入口触发 `Project A` 的 lead 派发。
  2. 记录顶层 Task 创建成功/DB commit 时间为 `T0`。
  3. 不刷新 `Browser-1`，观察任务列表。
  4. 查询 DB 确认 Task 归属 `Project A` 且为顶层 lead-dispatch Task。
  5. 查看日志确认 lead 派发成功与自动更新链路。
- 预期结果：
  - 5 秒 SLA：`Browser-1` 在 `T0 + 5s` 内看到新顶层任务。
  - 新增条目可见性口径：排序位置在视口时直接可见；否则只要求数据进入列表且不扰动当前可见行。
  - member/subtask sub-run 不作为任务列表条目出现。
  - DB/日志与 UI 展示一致。

### E2E-AR-005：任务状态从进行中到完成时，行状态在 5 秒内自动更新

- 覆盖验收：PRD §9.3
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 列表中存在一条 `status = running` 或等价进行中状态的顶层任务 `Task A1`。
  - `Task A1` 行当前可见，或记录该行所在位置用于滚动后确认。
  - 自动更新通道正常。
- 操作步骤：
  1. 在后台或其它入口将 `Task A1.status` 更新为 `completed`。
  2. 记录状态写入成功时间为 `T0`。
  3. 不刷新 `Browser-1`，观察 `Task A1` 行 StatusPill。
  4. 查询 DB 确认 `Task A1.status = completed`。
  5. 查看日志确认状态变化事件或列表刷新请求发生。
- 预期结果：
  - 5 秒 SLA：`Browser-1` 在 `T0 + 5s` 内看到 `Task A1` 行状态变为现有 UI 对 `completed` 的文案和配色。
  - 可见性口径：状态变化发生在原行上；若该行当前不在视口，滚动回该行时应看到已更新状态，且自动更新过程不强制跳转。
  - 无需刷新页面。
  - 状态文案和配色沿用现有 StatusPill 映射，不新增或改变文案。

### E2E-AR-006：任务状态从进行中到停止/阻塞时，行状态在 5 秒内自动更新

- 覆盖验收：PRD §9.3
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 列表中存在两条进行中顶层任务 `Task A2`、`Task A3`，或同一条任务可重复重置状态。
  - 自动更新通道正常。
- 操作步骤：
  1. 将 `Task A2.status` 从进行中更新为 `stopped`，记录 `T0-1`。
  2. 观察 `Browser-1` 中 `Task A2` 行状态。
  3. 将 `Task A3.status` 从进行中更新为 `blocked`，记录 `T0-2`。
  4. 观察 `Browser-1` 中 `Task A3` 行状态。
  5. 查询 DB 确认两个任务的新状态。
  6. 查看日志确认状态变化事件或刷新请求发生。
- 预期结果：
  - 5 秒 SLA：`Task A2` 在 `T0-1 + 5s` 内更新为现有 UI 对 `stopped` 的展示；`Task A3` 在 `T0-2 + 5s` 内更新为现有 UI 对 `blocked` 的展示。
  - 可见性口径：状态更新不要求页面跳转；若行在当前视口则直接可见，若不在视口则滚动回原位置后看到已更新状态。
  - 页面不刷新、不清空列表、不改变其它任务行状态。
  - DB/日志与 UI 展示一致。

### E2E-AR-007：会话列表空态在第一条有效会话到达后 5 秒内自动切换为列表

- 覆盖验收：PRD §9.4
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project Empty` 详情页。
  - 会话列表显示现有空态。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口创建一条归属 `Project Empty` 的有效会话。
  2. 确保该会话满足 PRD §2：`task_id == null` 且 `status != "created"`。
  3. 记录创建成功/DB commit 时间为 `T0`。
  4. 不刷新 `Browser-1`，观察会话区域。
  5. 查询 DB 与日志确认新会话符合可见规则且自动更新链路触发。
- 预期结果：
  - 5 秒 SLA：会话区域在 `T0 + 5s` 内从空态自动切换为列表并展示该会话。
  - 新增条目可见性口径：空列表不存在滚动偏移，新条目应直接可见。
  - 不需要切换项目、重进页面或手动刷新。

### E2E-AR-008：任务列表空态在第一条顶层任务到达后 5 秒内自动切换为列表

- 覆盖验收：PRD §9.4
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project Empty` 详情页。
  - 任务列表显示现有空态。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口触发一条归属 `Project Empty` 的顶层 lead-dispatch Task。
  2. 记录创建成功/DB commit 时间为 `T0`。
  3. 不刷新 `Browser-1`，观察任务区域。
  4. 查询 DB 确认该任务是顶层任务，不是 sub-run 或 Automation run 历史记录。
  5. 查看日志确认自动更新链路触发。
- 预期结果：
  - 5 秒 SLA：任务区域在 `T0 + 5s` 内从空态自动切换为列表并展示该任务。
  - 新增条目可见性口径：空列表无旧视口锚点，新任务应直接可见。
  - 不需要任何手动刷新动作。

### E2E-AR-009：会话自动插入不跳动、不重置滚动、不清空 composer、不改变选中项

- 覆盖验收：PRD §9.5
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 会话列表已有多条数据，足以产生滚动。
  - 用户滚动到列表中部，记录当前首个可见会话 `Session Anchor`。
  - 选中或展开一条会话 `Session Selected`。
  - composer 中输入未提交文本，例如 `draft before auto refresh`。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口创建一条归属 `Project A` 且符合 PRD §2 的新会话。
  2. 记录创建成功/DB commit 时间为 `T0`。
  3. 在 `T0 + 5s` 内持续观察 `Browser-1`。
  4. 检查滚动位置、首个可见会话、composer 内容、当前选中/展开项。
  5. 查询 DB/日志确认新会话已进入数据源并触发自动更新。
- 预期结果：
  - 5 秒 SLA：新会话在 `T0 + 5s` 内进入会话列表数据。
  - 新增条目可见性口径：由于当前视口不在插入位置，不强制跳转到新会话；验收重点为数据进入列表且不扰动当前可见行。
  - `Session Anchor` 仍保持在相同可见位置附近，无明显跳动或滚动重置。
  - composer 文本保持为 `draft before auto refresh`。
  - `Session Selected` 仍保持选中/展开状态。
  - 页面无报错、无整页 reload。

### E2E-AR-010：任务自动插入或状态更新不跳动、不重置滚动、不清空 composer、不改变选中项

- 覆盖验收：PRD §9.5
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 任务列表已有多条数据，足以产生滚动。
  - 用户滚动到任务列表中部，记录当前首个可见任务 `Task Anchor`。
  - 选中或展开任务 `Task Selected`。
  - composer 中输入未提交文本，例如 `task draft before auto refresh`。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口触发一条归属 `Project A` 的顶层任务，记录 `T0-1`。
  2. 在其它入口修改一个已存在任务的状态，记录 `T0-2`。
  3. 在各自 `T0 + 5s` 内观察 `Browser-1`。
  4. 检查滚动位置、首个可见任务、composer 内容、当前选中/展开项。
  5. 查询 DB/日志确认新增任务和状态变化均已发生。
- 预期结果：
  - 5 秒 SLA：新增顶层任务在 `T0-1 + 5s` 内进入任务列表数据；状态变化在 `T0-2 + 5s` 内更新到对应行。
  - 新增条目可见性口径：若新任务排序位置不在当前视口，不强制跳转；保持当前可见行稳定。
  - `Task Anchor` 不发生明显跳动，滚动位置不被重置。
  - composer 文本保持为 `task draft before auto refresh`。
  - `Task Selected` 仍保持选中/展开状态。
  - 页面无报错、无整页 reload。

### E2E-AR-011：自动更新通道异常时静默降级，不报错、不清空已有列表

- 覆盖验收：PRD §9.6
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 会话列表和任务列表均已有至少一条数据。
  - 自动更新通道初始正常。
- 操作步骤：
  1. 记录当前会话列表和任务列表条目数量及可见内容。
  2. 通过测试环境手段制造自动更新通道异常，例如断开 SSE、让轮询接口临时返回网络错误，或阻断对应连接。
  3. 保持 `Browser-1` 停留在当前页面，不刷新。
  4. 观察页面是否出现错误弹窗、toast、空列表、loading 卡死或整页报错。
  5. 查看前端/后端日志记录通道异常。
- 预期结果：
  - 5 秒 SLA：通道异常发生后 5 秒内，用户不应看到报错弹窗或列表被清空；页面保持上次成功的会话/任务列表。
  - 可见性口径：本用例不要求新增条目出现，重点验证异常期间旧数据仍对用户可见。
  - 页面可继续浏览已加载数据，composer 和选中项不被清空。
  - 日志可记录内部异常，但 UI 不向用户暴露错误。

### E2E-AR-012：通道恢复后 5 秒内通过一次自动全量刷新补齐遗漏

- 覆盖验收：PRD §9.6
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - 延续 E2E-AR-011，`Browser-1` 仍停留在 `Project A` 详情页。
  - 自动更新通道处于异常状态，页面保留上次成功列表。
- 操作步骤：
  1. 在通道异常期间，从其它入口创建一条符合 PRD §2 的 `Project A` 会话。
  2. 在通道异常期间，从其它入口触发一条 `Project A` 顶层任务。
  3. 在通道异常期间，将一个已展示任务的状态更新为 `completed` 或 `blocked`。
  4. 查询 DB 确认以上遗漏变化均已成功写入。
  5. 恢复自动更新通道。
  6. 从日志中记录“检测到通道恢复”的时间为 `T0`。
  7. 不刷新 `Browser-1`，观察会话列表和任务列表。
  8. 查看日志确认恢复后触发了一次自动全量刷新。
- 预期结果：
  - 5 秒 SLA：从“检测到通道恢复”日志时间 `T0` 起，遗漏的新会话、新顶层任务、任务状态变化均在 `T0 + 5s` 内补齐到 `Browser-1`。
  - 新增条目可见性口径：排序位置在当前视口时直接可见；若不在当前视口，则数据进入列表且不扰动当前可见行。
  - 补齐依赖一次自动全量刷新，不要求用户重进页面或手动刷新。
  - 恢复补齐过程中不清空已有列表、不重复插入同一条目、不改变 composer/选中项。
  - DB 与日志能证明遗漏数据已存在，且补齐发生在恢复后的自动全量刷新中。

### E2E-AR-013：停在项目 A 时，项目 B 新增会话不出现在项目 A 会话列表

- 覆盖验收：PRD §9.7
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口创建一条归属 `Project B`、且满足 PRD §2 会话可见规则的会话。
  2. 记录创建成功/DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 在 `Project A` 详情页，等待至少 5 秒。
  4. 查询 DB 确认该会话 `project_id = Project B`。
  5. 查看日志确认 `Project A` 自动更新过滤条件包含当前 `project_id`。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，该 `Project B` 会话仍不得出现在 `Project A` 会话列表。
  - 新增条目可见性口径：无论排序位置是否可见，跨项目条目都不得进入 `Project A` 列表数据。
  - `Project A` 旧数据不被清空或误刷新为 `Project B` 数据。
  - DB/日志能证明跨项目过滤生效。

### E2E-AR-014：停在项目 A 时，项目 B 新增任务不出现在项目 A 任务列表

- 覆盖验收：PRD §9.7
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口触发一条归属 `Project B` 的顶层 lead-dispatch Task。
  2. 记录创建成功/DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 在 `Project A` 详情页，等待至少 5 秒。
  4. 查询 DB 确认该 Task `project_id = Project B`。
  5. 查看日志确认 `Project A` 自动更新过滤条件包含当前 `project_id`。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，该 `Project B` 任务仍不得出现在 `Project A` 任务列表。
  - 新增条目可见性口径：跨项目任务不得进入 `Project A` 列表数据，不存在“视口外已插入”的情况。
  - `Project A` 任务列表不被清空、不混入 `Project B` 数据。
  - DB/日志能证明跨项目过滤生效。

### E2E-AR-015：created 草稿态会话不应出现在项目会话列表

- 覆盖验收：PRD §2、§9.1 的排除反例
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页会话列表。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口创建一条 `Project A` 会话，但保持 `status = "created"`，不发送首条有效消息。
  2. 记录 DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 不刷新，等待至少 5 秒。
  4. 查询 DB 确认该 session `project_id = Project A`、`task_id is null`、`status = "created"`。
  5. 查看日志确认自动更新过滤未将其作为可见会话推送/返回。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，该草稿态会话不得出现在项目会话列表。
  - 新增条目可见性口径：因不符合 PRD §2，可见性口径为“不进入列表数据”，不受排序位置影响。
  - 已有会话列表保持不变，不被清空、不报错。

### E2E-AR-016：`task_id != null` 的任务内部 session 不应出现在项目会话列表

- 覆盖验收：PRD §2、§9.1 的排除反例
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页会话列表。
  - 自动更新通道正常。
  - 存在 `Project A` 的任务可产生内部 session。
- 操作步骤：
  1. 在其它入口触发或创建一条归属 `Project A`、但 `task_id != null` 的任务内部 session。
  2. 记录 DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 不刷新，等待至少 5 秒。
  4. 查询 DB 确认该 session `project_id = Project A`、`task_id != null`。
  5. 查看日志确认项目会话列表过滤条件排除了任务内部 session。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，该任务内部 session 不得出现在项目会话列表。
  - 新增条目可见性口径：因不符合 PRD §2，可见性口径为“不进入列表数据”。
  - 若该内部 session 关联任务状态有变化，只能体现在任务列表对应顶层任务行，不能新增为项目会话列表条目。

### E2E-AR-017：member/subtask sub-run 不应出现在项目任务列表

- 覆盖验收：PRD §2、§9.2 的排除反例
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 自动更新通道正常。
  - 存在可产生 member/subtask sub-run 的顶层任务。
- 操作步骤：
  1. 触发 `Project A` 的顶层任务，使其生成 member/subtask sub-run。
  2. 分别记录 sub-run 创建成功时间为 `T0`。
  3. 保持 `Browser-1` 不刷新，等待至少 5 秒。
  4. 查询 DB 确认 sub-run 归属 `Project A`，但类型/层级为 member 或 subtask sub-run。
  5. 查看日志确认任务列表查询或事件过滤只纳入顶层 lead-dispatch Task。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，member/subtask sub-run 不得作为独立行出现在项目任务列表。
  - 新增条目可见性口径：因不符合 PRD §2，可见性口径为“不进入任务列表数据”。
  - 顶层任务行可按其自身状态更新，但不展开出额外的 sub-run 列表行。

### E2E-AR-018：Automation run 历史记录不应出现在项目任务列表

- 覆盖验收：PRD §2、§9.2 的排除反例
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页任务列表。
  - 自动更新通道正常。
  - 存在会产生 Automation run 历史记录的 Automation。
- 操作步骤：
  1. 触发 `Project A` 的 Automation，使其产生 run 历史记录。
  2. 若 Automation 同时触发顶层任务，记录顶层任务创建时间 `T0-task`；记录 run 历史创建时间 `T0-run`。
  3. 保持 `Browser-1` 不刷新，观察任务列表至少 5 秒。
  4. 查询 DB 确认 run 历史记录与顶层 Task 是不同对象。
  5. 查看日志确认任务列表只返回顶层 Task，不返回 Automation run 历史记录。
- 预期结果：
  - 5 秒 SLA：若产生顶层任务，该任务应在 `T0-task + 5s` 内按 PRD §9 新增条目可见性口径出现；Automation run 历史记录在 `T0-run + 5s` 后不得作为任务行出现。
  - 新增条目可见性口径：只适用于符合 PRD §2 的顶层任务；run 历史记录不进入任务列表数据。
  - DB/日志能区分 Automation 作为任务来源与 Automation run 历史记录本身。

### E2E-AR-019：其它用户可见但当前用户无权访问的数据不应自动出现

- 覆盖验收：PRD §3 权限/可见性边界、§9.1、§9.2、§9.7
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - `U2` 无权访问 `Project A` 中 `U1` 不可见的数据，或存在仅 `U2` 可见的数据范围。
  - 自动更新通道正常。
- 操作步骤：
  1. 以 `U2` 在其它入口创建一条 `Project A` 会话，或触发一条仅 `U2` 可见的 `Project A` 顶层任务。
  2. 记录创建成功/DB commit 时间为 `T0`。
  3. 保持 `Browser-1` 不刷新，等待至少 5 秒。
  4. 查询 DB 确认数据归属 `Project A` 但 `U1` 无访问权限或不属于 `U1` 可见范围。
  5. 查看日志确认自动更新链路沿用现有接口的 `user_id + project_id` 过滤。
- 预期结果：
  - 5 秒 SLA：在 `T0 + 5s` 后，`U1` 的 `Project A` 会话列表/任务列表不得出现该无权访问数据。
  - 新增条目可见性口径：无权数据不得进入当前用户列表数据，不受排序位置影响。
  - 页面不报错、不清空已有数据。
  - DB/日志能证明权限过滤生效。

### E2E-AR-020：切换离开项目后，本项目自动更新停止；进入其它项目不被旧订阅污染

- 覆盖验收：PRD §6.6、§9.7 回归
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，先停留在 `Project A` 详情页。
  - 自动更新通道正常。
- 操作步骤：
  1. 将 `Browser-1` 从 `Project A` 切换到 `Project B` 详情页。
  2. 查看日志确认已停止或切换 `Project A` 的自动更新感知。
  3. 在其它入口创建一条 `Project A` 有效会话和一条 `Project A` 顶层任务，记录 `T0`。
  4. 保持 `Browser-1` 在 `Project B`，等待至少 5 秒。
  5. 观察 `Project B` 会话/任务列表。
  6. 查询 DB 确认新增数据均属于 `Project A`。
- 预期结果：
  - 5 秒 SLA：`T0 + 5s` 后，`Project A` 新增会话和任务不得出现在 `Project B` 列表。
  - 新增条目可见性口径：旧项目数据不得进入当前项目列表数据。
  - 日志显示项目切换后使用当前 `Project B` 的过滤条件，不存在旧项目订阅继续写入当前页。

### E2E-AR-021：重复事件或恢复补齐不造成重复行

- 覆盖验收：PRD §9.1、§9.2、§9.6 回归
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 自动更新通道正常。
- 操作步骤：
  1. 在其它入口创建一条符合 PRD §2 的 `Project A` 会话和一条顶层任务。
  2. 通过测试工具重复发送同一条变更事件，或在事件到达后再触发一次全量刷新。
  3. 记录原始创建成功时间为 `T0`。
  4. 等待至少 5 秒，观察会话列表和任务列表。
  5. 查询 DB 确认同一 session/task 只有一个业务对象。
  6. 查看日志确认重复事件或全量刷新发生。
- 预期结果：
  - 5 秒 SLA：新会话和新任务在 `T0 + 5s` 内进入列表，但每个业务对象只出现一行。
  - 新增条目可见性口径：若排序位置在视口则直接可见；若不在视口则数据进入列表且不扰动当前可见行。
  - 重复事件、轮询刷新、恢复补齐不能造成重复行或闪烁清空。

### E2E-AR-022：自动更新不改变现有排序、筛选和视觉样式

- 覆盖验收：PRD §4、§5、§7 回归
- 验证方式：Browser 实操验证；DB/日志双验证
- 前置条件：
  - `Browser-1` 登录 `U1`，停留在 `Project A` 详情页。
  - 会话列表和任务列表已有多条数据，能观察现有排序。
  - 自动更新通道正常。
- 操作步骤：
  1. 记录当前会话列表与任务列表排序规则下的前几条展示顺序。
  2. 在其它入口创建一条新有效会话，触发一条新顶层任务，并更新一个任务状态。
  3. 分别记录创建/状态写入时间为 `T0-session`、`T0-task`、`T0-status`。
  4. 在 `Browser-1` 观察自动更新后的列表顺序、筛选结果、StatusPill 文案与配色。
  5. 查询 DB/日志确认数据变化已发生且自动更新链路触发。
- 预期结果：
  - 5 秒 SLA：新会话、新任务、状态变化分别在对应 `T0 + 5s` 内体现。
  - 新增条目可见性口径：新条目按现有排序就位；若排序位置不在当前视口，则不强制跳转，锚定优先。
  - 自动更新不引入新的排序规则、筛选规则、状态文案或视觉样式。
  - 任务状态展示直接取后端 `status` 字段，并沿用现有 StatusPill 映射。

## 4. 覆盖矩阵

| PRD 验收/规则 | 覆盖用例 |
| --- | --- |
| §9.1 本项目符合 §2 的新会话 5 秒内出现 | E2E-AR-001 |
| §9.2 定时任务触发顶层任务 5 秒内出现 | E2E-AR-002 |
| §9.2 手动 kickoff 顶层任务 5 秒内出现 | E2E-AR-003 |
| §9.2 lead 派发顶层任务 5 秒内出现 | E2E-AR-004 |
| §9.3 进行中到完成 5 秒内状态更新 | E2E-AR-005 |
| §9.3 进行中到停止/阻塞 5 秒内状态更新 | E2E-AR-006 |
| §9.4 空态到有数据自动切换 | E2E-AR-007、E2E-AR-008 |
| §9.5 不跳动/不重置滚动/不清空 composer/不改选中项 | E2E-AR-009、E2E-AR-010 |
| §9.6 通道异常静默降级 | E2E-AR-011 |
| §9.6 恢复后 5 秒内一次自动全量补齐 | E2E-AR-012 |
| §9.7 跨项目隔离 | E2E-AR-013、E2E-AR-014、E2E-AR-020 |
| §2 排除 created 草稿态会话 | E2E-AR-015 |
| §2 排除 `task_id != null` 任务内部 session | E2E-AR-016 |
| §2 排除 member/subtask sub-run | E2E-AR-017 |
| §2 排除 Automation run 历史记录 | E2E-AR-018 |
| §3 当前用户权限过滤 | E2E-AR-019 |
| 恢复/重复事件回归 | E2E-AR-021 |
| 不改排序/筛选/视觉样式回归 | E2E-AR-022 |

## 5. 发布前质量报告记录模板

执行时每条用例需记录：

- 用例 ID：
- 执行环境：
- 执行人/时间：
- Browser 截图或录屏：
- DB 查询证据：
- 前端/后端日志证据：
- 实际耗时：
- 结果：通过 / 失败 / 阻塞 / 未覆盖
- Bug 等级：阻塞 / 严重 / 一般 / 优化建议
- 稳定复现步骤：
- 期望行为：
- 实际行为：
- 修复后回归结果：

---

## 6. 技术补充用例（开发）

> 来源：`docs/features/project-detail-auto-refresh/plan.md`（第 2 轮已收口）。
> 本章在 QA 第 1–5 章的用户视角 E2E 之上，补充**实现机制对应的技术验证点**，覆盖 plan.md §4A 执行契约、§4B/§7.4 锚定、§7 回归与 §9 自测要点。
> 每条标注**验证层级**（单测 / 集成 / 组件级 / E2E / 手动）。

### 6.0 机制对齐说明（先读，避免与 brief 措辞误配）

plan.md §1/§4 在 PRD §8 三选一中**选定方案 B：复用既有两个列表接口 + 页面作用域 4s 聚焦轮询**，**明确不引入新的项目级 SSE 通道**（方案 A 已否决）。因此本章把需求侧的几个通用术语映射到真实实现：

| 需求侧术语 | 本版真实机制（plan.md 落点） |
| --- | --- |
| 事件订阅建立 / 断开 | 轮询订阅生命周期：`useProjectListAutoRefresh` 的 `setInterval` + `visibilitychange`/`online` 监听挂载，effect cleanup 清 interval/`clearTimeout`/`abort`/移除监听（§4A.1–2、§7.5） |
| SSE 重连 / 降级恢复触发全量刷新 | `Promise.allSettled` 任一 rejected → 静默保留旧列表；下一轮 fulfilled → **整表结果回灌补齐**（§4A.5–6、§9.6） |
| event 载荷 project_id/user_id 过滤 | 本版**无新事件载荷**；过滤在既有列表接口（后端 `require_current_user_id()` + `project_id`）+ 前端 generation 校验 + `mergeProjectSessions` 同源断言（§0.2、§4A.4） |
| 锚定 | `useListScrollAnchor` 滚动位置校正（不改排序规则），覆盖 all tab 因 `update_task_status()` 刷新 `updated_at` 引发的重排（§4B、§7.4） |

> 提示：会话详情页的 `GET /v1/sessions/{id}/events/stream`（SSE 消息流）本版**完全不碰**，仅作回归项保护（TECH-AR-012），勿与列表自动刷新机制混淆。

### TECH-AR-001：聚焦轮询订阅的建立与拆除（hook 生命周期）

- 验证层级：**单测**
- 对应实现：plan §4A.1–2、§5（新增 `use-project-list-auto-refresh.ts`）、§7.5
- 文件：`frontend/packages/core/src/hooks/use-project-list-auto-refresh.test.ts`（fake timers + mock API）
- 验证点：
  - 挂载后建立 `4s`（`intervalMs` 默认 4000）`setInterval`，并注册 `visibilitychange` / `online` 监听。
  - 隐藏标签（`document.hidden`）暂停 tick；`visibilitychange→visible` 与 `online` 各立即补一次 fetch（且过单飞闸，见 TECH-AR-002）。
  - 卸载或 `projectId` 变化触发 effect cleanup：清 `setInterval`、`clearTimeout`、`controller.abort()`、移除两个监听——断言无僵尸 interval、无卸载后晚写（呼应 §7.5「僵尸 interval 累积」防护）。

### TECH-AR-002：单飞（single-flight）不积压请求

- 验证层级：**单测**
- 对应实现：plan §4A.1、§9 自测「单飞」
- 验证点：上一轮 `inFlightRef` 仍为真时，下一次 tick **跳过**、不发起重复请求、不积压；`visibilitychange→visible` / `online` 的即时补拉同样过单飞闸，并发场景下任一 projectId 同一时刻只有一组在途请求。

### TECH-AR-003：超时/挂起请求 abort 且不阻塞下一轮

- 验证层级：**单测**
- 对应实现：plan §4A.3、§9 自测「超时/挂起」【覆盖 P1-2】
- 验证点：mock 一个永不 resolve 的 fetch，断言 `intervalMs` 后 `setTimeout(() => controller.abort())` 触发，请求以 `AbortError` 进入失败分支；该挂起请求最多占用一个间隔，**不阻塞**下一轮成功补拉。成功/失败后 `clearTimeout` 被调用。

### TECH-AR-004：降级静默 + 恢复后一次整表补齐（轮询替代「SSE 重连」）

- 验证层级：**单测 + 集成/E2E**
- 对应实现：plan §4A.5–6、§9.6；对应 QA `E2E-AR-011`/`E2E-AR-012`
- 说明：本版无 SSE 重连；「降级恢复」即 `allSettled` rejected → 静默保留旧列表，下一轮 fulfilled → 整表覆盖。
- 单测验证点：第 1 轮某接口 reject → 列表保留旧值、不清空、不抛错、不弹 toast；第 2 轮 resolve → 以**本次整表结果**覆盖该列表（含失败期间遗漏的新增/状态变化），一次补齐、不留 cursor/gap。
- 集成/E2E 验证点（与 `E2E-AR-012` 联动）：异常期间从别处新建会话/触发顶层任务/改任务状态，恢复后以「检测到恢复」（`online`/`visibilitychange→visible` 立即补拉，否则 ≤4s 下一 tick）为 `T0`，遗漏变化在 `T0 + 5s` 内整表补齐，**不重复插入同一条目**。

### TECH-AR-005：allSettled 会话/任务独立失败互不阻塞

- 验证层级：**单测**
- 对应实现：plan §4A.5、§9 自测「allSettled 独立失败」
- 验证点：会话 reject + 任务 fulfilled（及反向）→ 成功一侧照常写入（`mergeProjectSessions` / `onTasks`），失败一侧保留上次成功结果；单边失败不拖垮另一侧 5s SLA。

### TECH-AR-006：project_id / user_id 过滤——既有列表接口（无 event 载荷）

- 验证层级：**集成（后端）**
- 对应实现：plan §0.2、§7.2；对应 QA `E2E-AR-013`/`E2E-AR-014`/`E2E-AR-019`
- 说明：本版不新增 event，过滤复用既有接口；后端零改动，此条为**回归保护**而非新逻辑。
- 验证点：
  - `GET /v1/sessions?project_id`：`project_index.list_session_ids(project_id, user_only=True)` 生成 `WHERE user_id == require_current_user_id() AND project_id == ? AND kind == "chat"`——断言排除别用户、别项目、以及 `task_lead`/`task_subtask` 内部 run（对应 §2 排除 `task_id != null`）。
  - `GET /v1/projects/{id}/tasks`：`TaskDatastore.list_tasks(user_id, project_id)` 生成 `WHERE project_id AND user_id ORDER BY created_at DESC`——断言别项目/别用户任务不返回，且 member sub-run（`valuz_task_session(kind="subtask")`）不进此列表（对应 §2 排除 sub-run）。

### TECH-AR-007：generation/projectId 防旧响应（A→B 切项目竞态）

- 验证层级：**单测**
- 对应实现：plan §4A.4、§7.2；对应 QA `E2E-AR-020`
- 验证点：发起 `projectId=A` 请求后，把 `projectId` 切到 `B`；A 的晚返回因 `myGen !== genRef.current`（或 `projectIdAtRequest !== currentProjectId`）校验失败而**被丢弃**，不写入 B 的 store / 不调 B 的 `onTasks`。`onTasks` 侧 `tasks[].project_id === projectId` 同源断言作为兜底也需覆盖。

### TECH-AR-008：mergeProjectSessions 子集合并 + 同源断言

- 验证层级：**单测**
- 对应实现：plan §5（`store/session-store.ts` 新增）、§7.2–3；对应 QA `E2E-AR-013`/`E2E-AR-015`/`E2E-AR-016`/`E2E-AR-021`
- 文件：`frontend/packages/core/src/store/session-store.test.ts`
- 验证点：仅替换 `project_id === projectId` 子集——其它项目行不动、未变对象**引用复用**（减 re-render）、新增 upsert、消失行剔除；重复 id 不产生重复行（对应 `E2E-AR-021`）；`project_id` 同源断言**拒写**异项目行；草稿/任务内部 session 过滤口径（`status !== "created" && task_id == null`）在列表过滤层保持（对应 `E2E-AR-015`/`E2E-AR-016`）。

### TECH-AR-009：mergeTasks 原位合并替换整表覆盖

- 验证层级：**单测 / 组件级**
- 对应实现：plan §5（`ProjectDetailPage.tsx` 把 `setTasks(res.tasks)` 改 id 键控原位合并）；对应 QA `E2E-AR-021`
- 验证点：既有行**原位更新**（status/updated_at）、新行按 `created_at` 序就位、消失行剔除、同 id 不重复行；首拉（L721-735）与轮询合并幂等（整表 + id 键控），不因首拉/轮询叠加产生重复或闪烁清空。

### TECH-AR-010：锚定 hook useListScrollAnchor 滚动校正

- 验证层级：**单测（jsdom + 受控 rect/scrollTop）**
- 对应实现：plan §4B、§7.4、§5（新增 `use-list-scroll-anchor.ts`）；对应 QA `E2E-AR-009`/`E2E-AR-010`
- 文件：`frontend/packages/core/src/hooks/use-list-scroll-anchor.test.ts`
- 验证点：
  - 已下滚时，列表头部插入新行 / 重排已有行后，`useLayoutEffect`（keyed on `dataKey`）在绘制前执行 `scrollTop += (top' - anchorRef.top)`，**首个可见行视觉 top 不变**。
  - 顶部豁免：`container.scrollTop <= 阈值(~8px)` 时**不校正**，新条目在顶部自然可见（满足 §9.1/§9.2 新条目可见）。
  - 锚点行被删除 → 回退到下一个仍存在的可见候选，再退化到不校正，**不抛错**。

### TECH-AR-011：ProjectDetailPage 三 tab 锚定 + all tab 重排

- 验证层级：**组件级（render + mock store/api）**
- 对应实现：plan §7.4「三 tab 覆盖」、§3「排序事实」；对应 QA `E2E-AR-010`/`E2E-AR-022`
- 文件：`frontend/packages/app/.../ProjectDetailPage.test.tsx`
- 验证点：
  - **默认 all tab**（`ProjectAllList` 客户端 `sort by updated_at`）：下滚到中段，某任务状态更新致 `update_task_status()` 写 `updated_at = now_ms()` 触发重排后，断言锚点行 DOM 位置/`scrollTop` 不变（首个可见行不跳）。
  - **chat tab**（`updated_at` = 创建时间不可变，不重排）、**tasks tab**（`created_at DESC` 不重排，状态变化仅行内更新）各跑一遍「新条目插表头 + 下滚不跳」。
  - 自动更新**只写列表数据**，不触碰 composer 输入 / 选中项 / 展开态（对应 `E2E-AR-009`/`E2E-AR-010` 的不清输入、不改选中）。

### TECH-AR-012：不破坏会话详情消息流（SSE 回归）

- 验证层级：**单测/集成 + 手动**
- 对应实现：plan §7.1
- 验证点：静态断言本版 diff **不 import / 不触碰** `event_sse_adapter` / `subscribe_events` / `chat-store.attach` / `session-stream` / `useSessionEvents` / `useTaskEvents`；既有会话详情页 SSE 消息流相关测试全绿；手动在会话详情页确认消息流、工具卡片正常推送，不受列表轮询影响。

### TECH-AR-013：不破坏侧边栏 RECENTS / TASKS（全局 store 不被收窄）

- 验证层级：**单测 + 手动**
- 对应实现：plan §7.3；旁证 QA `E2E-AR-020`
- 验证点：`mergeProjectSessions` 子集合并后，全局 `useSessionStore` 仍含**所有项目**会话（不被 `set({sessions})` 整库覆盖收窄成单项目子集）；`useTaskStore`（跨项目侧栏）不被详情页轮询触碰；手动确认进入项目详情页停留期间，侧边栏 RECENTS / TASKS 不被清空或错置。

### TECH-AR-014：三道质量门作为提交前 gate

- 验证层级：**集成闸（本地/CI 命令）**
- 对应实现：plan §9「集成闸」；`CLAUDE.md` §Verification
- 验证点：
  - `make test-all && make typecheck && make lint` 三道全过，作为 PR 提交前硬 gate（不 `--no-verify`、不跳测试）。
  - 本版无 `api/openapi.yaml` / i18n 改动 → **无需** `make generate-types` / `gen_types.py`；若 CI 检出 openapi 或 locale 漂移即视为误改，须回退或补生成。
  - 基线口径：本版为**前端零后端改动**，typecheck/lint 关注前端无新增错误；既有后端 mypy 基线与前端 vitest 预存失败不归因于本改动，按「改动文件无新增失败」判定，不为预存失败卡提交。

## 7. 技术补充用例覆盖矩阵

| plan.md 机制 / 验证方向 | 技术用例 | 关联 QA E2E |
| --- | --- | --- |
| §4A.1–2 轮询订阅建立/断开（生命周期、监听、cleanup） | TECH-AR-001 | E2E-AR-020 |
| §4A.1 单飞不积压 | TECH-AR-002 | E2E-AR-021 |
| §4A.3 超时/挂起 abort 不阻塞 | TECH-AR-003 | E2E-AR-011/012 |
| §4A.5–6 降级静默 + 恢复整表补齐 | TECH-AR-004 | E2E-AR-011/012 |
| §4A.5 allSettled 独立失败 | TECH-AR-005 | E2E-AR-011 |
| §0.2 后端 user_id+project_id 过滤 | TECH-AR-006 | E2E-AR-013/014/019 |
| §4A.4 generation 防旧响应（切项目竞态） | TECH-AR-007 | E2E-AR-020 |
| §5 mergeProjectSessions 子集合并 + 同源断言 | TECH-AR-008 | E2E-AR-013/015/016/021 |
| §5 mergeTasks 原位合并（替换整表覆盖） | TECH-AR-009 | E2E-AR-021 |
| §4B/§7.4 useListScrollAnchor 滚动校正 | TECH-AR-010 | E2E-AR-009/010 |
| §7.4 三 tab 锚定 + all tab 重排 | TECH-AR-011 | E2E-AR-010/022 |
| §7.1 不破坏会话详情 SSE 消息流 | TECH-AR-012 | （回归保护） |
| §7.3 不破坏侧边栏 RECENTS/TASKS | TECH-AR-013 | E2E-AR-020 |
| §9 集成闸（make test-all/typecheck/lint） | TECH-AR-014 | （提交前 gate） |

