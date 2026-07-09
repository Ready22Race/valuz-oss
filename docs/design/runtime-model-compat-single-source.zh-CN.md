# Runtime/Model 兼容性 —— 单一真值、前端 dumb-render、可用性由执行目标声明

> 状态：设计（提案）。英文版见 [runtime-model-compat-single-source.md](runtime-model-compat-single-source.md)。
>
> 一句话方向：**"哪个 runtime 能跑这个 model" 只在一个地方推导（`runtimes_for`），
> 物化到每条 model 行（`LLMModel.runtimes`），前端原样渲染。** 前端不再从
> `protocol`/`provider_kind` 重新推导兼容性。另外，**"这个 runtime 到底能不能跑"
> 由执行目标（真正运行 kernel 的宿主）声明**，而不是在 API 宿主上探测——因为在
> 控制面/执行面分离的部署里，runtime 二进制在沙箱里，不在 API pod 里。

本文是 OSS 侧契约。贡献方 overlay（往通道列表里加 gateway/catalog 通道的
`LLMProvider`）在自己贡献的行上声明 `runtimes`，并从自己的执行目标绑定一个
runtime 可用性 provider；OSS 提供推导规则、单一物化点、前端管线、可用性 port
以及默认的本地实现。

---

## 1. 为什么

Runtime↔model 兼容性目前在**三处**各自实现，已经漂移：

| 实现 | 位置 | codex 规则 |
|---|---|---|
| **权威** | `modules/settings/model_options.py:runtimes_for` | 任何非订阅通道，只要说 `openai-response` → codex |
| 前端重算 #1 | `packages/core/src/hooks/use-composer-providers.ts` | 非订阅 + `canDriveAny(["openai-response"])` → codex |
| 前端重算 #2 | `packages/core/src/api/runtime-compat.ts` | codex **仅**限 `provider_kind==="system"` 或 `codex-subscription` |

`runtimes_for` 已经实现了正确规则（用户自带的 OpenAI 兼容 Responses 端点——例如
Volcengine Ark——可以通过 kernel 合成的 `[model_providers.harness]` 块、
`wire_api="responses"` 来驱动 codex；见 `backend/kernel/src/runtimes/codex/runtime.py`）。
它已原样暴露在 `GET /v1/settings/model-options`（`ModelOption.runtimes`）上，默认
配置卡片（`ModelSection.tsx`）和 onboarding（`ConnectStep.tsx`）已经在 dumb-render
这个字段。

但 composer、项目 agent picker、以及 provider 列表的"可用于"徽章**没有**读这个
字段——它们经由上表两个前端实现从 `compatible_protocols`/`provider_kind` 重算。
两个前端实现彼此、以及与后端都不一致，于是：

- 一个已测通的自定义 `openai-response` provider **不显示"OpenAI Codex"徽章**
  （`runtime-compat.ts` 要求 `provider_kind==="system"`），尽管它能跑 codex。
- 合法声明了 `runtimes=("codex",)` 的贡献通道，会因为不同界面用了不同实现而被
  丢弃或误标。

根因：那次"服务端解析 model-options + dumb-render pickers"的迁移只接进了*部分*
picker。providers 的 list/detail 接口仍然把 `LLMModel.runtimes` 留空，逼着其它
所有界面自己重算。

**第二个、部署形态相关的缺口：** `is_runtime_available`（`adapters/runtime_registry.py`）
探测的是 **API 宿主**的 PATH / bundled 二进制里的 codex。打包桌面版里这个宿主
*就是*跑 turn 的地方，所以正确。但在 kernel 跑在独立沙箱的分离部署里，API pod
的 PATH 是错的度量对象——pod 可能没有 codex 而沙箱有（反之亦然）。
`modules/system/service.py` 里的 `_runtimes_available` 已经放弃探测、直接返回静态
集合，正说明了这一点。

## 2. 现状（权威归属图）

- **权威 —— model→runtime 推导：** `runtimes_for(protocols, provider_kind)` 与
  `build_model_options`（`modules/settings/model_options.py`）。
- **权威 —— runtime↔协议能力 + 宿主可用性：** `RUNTIME_REGISTRY` 与
  `is_runtime_available`（`adapters/runtime_registry.py`），镜像 kernel
  `src/runtimes/factory.py:ALLOWED_PROTOCOLS_BY_RUNTIME`。
- **刻意镜像（成对维护）：** `providers/service.py:_derive_compatible_protocols`
  ↔ `provider_resolver._resolve_api_protocol`；OSS registry ↔ kernel factory ↔
  前端 `runtime-protocols.ts`。
- **生产环境死代码：** `runtime_registry.supports_protocol` —— 只有测试在调；
  真正的 runtime↔协议闸门是会话启动时的 kernel factory。
- **已经 dumb-render `runtimes` 的界面：** `ModelSection.tsx` 默认配置 picker；
  `onboarding/ConnectStep.tsx`。
- **仍在重算（待改造）的界面：** `AgentModelPicker.tsx`、`ConversationsHomePage.tsx`、
  `ProjectDetailPage.tsx`、`ConversationPage.tsx`（都经 `useComposerProviders`）；
  `ModelSection.tsx` 的 runtime 切换 guard 与"可用于"徽章（经
  `isProviderRuntimeCompatible`/`compatibleRuntimes`）。

## 3. 设计

### 3.1 在每个 model 界面物化 `runtimes`

`LLMModel.runtimes` 是已声明的 wire 字段（`modules/providers/schemas.py` 与
`packages/shared/src/types/provider.ts`，`string[] | null`）。今天
`_row_to_list_item` / `_row_to_detail`（`modules/providers/service.py`）在
user/builtin 行上刻意留 `None`、靠 picker 推导。改为用同一条规则填充：

```python
compatible = _derive_compatible_protocols(row)
ch_runtimes = tuple(runtimes_for(compatible, provider_kind=row.provider_kind))
models = [
    LLMModel(id=m.id, label=m.label, runtimes=(m.runtimes or ch_runtimes))
    for m in _resolve_models(row)
]
```

- 贡献方声明的 per-model `runtimes` 仍然优先；只填 `None`。因此
  `build_model_options` 行为不变（`m.runtimes` 现在非 `None`，值与原先推导一致），
  `provider_resolver` 的贡献行路径也不变。
- `GET /v1/providers`（list + get）现在带上权威 `runtimes`，前端所有界面无需第二个
  接口即可读取。

这是**增量**改动：字段与其 `null` 语义都已存在；仍读 `null` 分支的旧客户端照常工作。

### 3.2 前端：dumb-render `runtimes`，删除重算

- `use-composer-providers.ts:useComposerProviders` —— 改为在 **model 级**按
  `m.runtimes?.includes(runtimeFilter)` 过滤。删除 `canDriveAny` /
  `canDriveAnthropic` / `CODEX_PROTOCOLS` / `DEEPAGENTS_PROTOCOLS` 及订阅 kind 的
  runtime 逻辑（订阅排除已由 `runtimes_for` 编码）。保留
  `providerHasUsableCredentials`（凭证闸门，非 runtime 闸门）。把 channel 的
  runtimes 附到 `default_model` 兜底行，使仅凭证的 anchor 也能正确过滤。
- `runtime-compat.ts` —— 把 `isProviderRuntimeCompatible` / `compatibleRuntimes`
  重写为 `provider.models[].runtimes` 的并集；`CompatProvider` 类型加入 `models`。
  删除 `speaksAnyProtocolFrom` 以及 `ALLOWED_PROTOCOLS_BY_RUNTIME` 的兼容用途。
  消费者（`ModelSection.tsx` 的 runtime 切换 guard + 徽章）签名不变。
- `runtime-protocols.ts` —— **保留**，供 New-Session / Edit-Capabilities 的
  **协议选择下拉**（`defaultProtocolFor` / `isProtocolAllowed`）使用，那是选择要
  *配置*哪种 wire。它不再被兼容性过滤引用。

结果：composer、agent picker、徽章、默认配置、onboarding 全部读同一个后端字段；
新增 runtime 时只需改一处（`runtimes_for`）。

### 3.3 Runtime 可用性由执行目标声明

新增一个 port，让真正运行 kernel 的环境声明它能启动哪些 runtime：

```python
# ports/runtime_availability.py
class RuntimeAvailabilityPort(Protocol):
    def available_runtimes(self) -> set[str] | None:
        """执行环境里可启动的 runtime 集合。
        ``None`` → 回退到本地宿主探测（打包桌面版）。"""
```

- `ports/extensions.py` 增加 `ext.runtime_availability: RuntimeAvailabilityPort | None = None`。
- `is_runtime_available(runtime_id)` 先咨询它：若 `available_runtimes()` 返回集合，
  则可用性 = 是否在集合内（跳过二进制探测）；若返回 `None`（或 `ext` 未绑定），
  保留现有 PATH / bundled / `CODEX_BIN_OVERRIDE` 探测。`GET /v1/runtimes` 与
  `tools_agent_proposal` 自动跟随。
- **默认（OSS 单跑 / 打包桌面版）：** 未绑定 → 本地探测 → 与今天完全一致。

权威能力是执行镜像的 manifest；这个 provider 就是把该 manifest 声明给控制面
（例如构建期、按 image digest 索引），而不是在 picker 热路径上做逐会话实时探测。

## 4. 契约影响（`contracts/COMPATIBILITY.md`）

| 变更 | 类别 | 说明 |
|---|---|---|
| `GET /v1/providers` list+get 现在填充 `LLMModel.runtimes` | evolving（增量） | 字段与 `null` 语义已存在；旧客户端读 `null` 分支 |
| 新增 `ext.runtime_availability` port + `RuntimeAvailabilityPort` | new / stable | 可选；未绑定 = 当前本地探测行为 |
| `LLMProvider.list/resolve`、`RUNTIME_REGISTRY`、kernel `ALLOWED_PROTOCOLS_BY_RUNTIME` | 不变 | — |

`supports_protocol` 可删除或标记 deprecated（无生产调用方）。

## 5. 改动清单

后端：
- `modules/providers/service.py` —— 在 `_row_to_list_item` / `_row_to_detail` 填 `runtimes`。
- `ports/runtime_availability.py`（新增）+ `ports/extensions.py` —— port 与 `ext` 槽位。
- `adapters/runtime_registry.py` —— `is_runtime_available` 咨询 `ext.runtime_availability`。

前端：
- `packages/core/src/hooks/use-composer-providers.ts` —— model 级 `runtimes` 过滤；删推导。
- `packages/core/src/api/runtime-compat.ts` —— `models[].runtimes` 并集；删推导。
- （`packages/core/src/api/runtime-protocols.ts` —— 不变；范围收窄到协议下拉。）

## 6. 迁移 / 顺序

1. 3.1 + 3.2 一起上（后端填 + 前端读）—— 自洽、wire 上增量。
2. 3.3（port + 默认本地实现）单独上 —— 在 overlay 绑定前无行为变化。
3. 发布前契约回归（`make test-contract`）绿。

3.2 依赖 3.1（前端需要已填充的字段）。3.3 独立。

## 7. 测试

- `_row_to_list_item` / `_row_to_detail` 对 `anthropic` / `openai-completion` /
  `openai-response` / 双协议 / 两种订阅 kind（codex-subscription → `["codex"]`、无
  deepagents）都正确填 `runtimes`。
- 绑定 `ext.runtime_availability` 后的 `is_runtime_available`：集合命中 → 可用；
  未命中 → 不可用带原因；未绑定 → 本地探测不变。
- `useComposerProviders` 只按 `m.runtimes` 过滤；订阅排除仍成立（由后端 runtimes 驱动）。
- `runtime-compat.compatibleRuntimes` = `models[].runtimes` 并集；已测通的自定义
  `openai-response` provider 显示 codex 徽章。

## 8. 下游（overlay）职责

不放进 OSS，但在此写明，避免 seam 意图含糊：

- 往通道列表加 gateway/catalog 通道的贡献方 `LLMProvider`，在自己的行上声明
  `LLMModel.runtimes`。尤其是意在驱动 codex 的 `openai-response` 卡，声明
  `runtimes=("codex",)` 与 `compatible_protocols=["openai-response"]`（与今天贡献一张
  系统 gateway Responses 卡的方式一致）。OSS 原样消费。
- kernel 跑在独立沙箱的 overlay，从自己的执行目标（沙箱镜像声明的 runtime 集合）
  绑定 `ext.runtime_availability`，使 codex 当且仅当沙箱镜像装了它时才报告可用——
  与 API 宿主的 PATH 无关。

## 9. 非目标

- 不改 kernel factory 作为会话启动时 runtime↔协议最终闸门的地位。
- 不改 `web_search` 对非订阅 codex key 的强制禁用（kernel 侧）。
- 协议选择 UI（`runtime-protocols.ts`）仍是前端职责；它是*配置*辅助，不是兼容性来源。
