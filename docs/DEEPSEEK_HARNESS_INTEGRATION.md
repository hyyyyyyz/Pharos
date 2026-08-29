# DeepSeek Harness 与 Pharos Durable Harness 集成

> 状态：**已选型，H1.5 实现中，尚未部署。** 当前已完成 Pharos H1 durable kernel code gate、
> DSH 固定源码与 no-tool profile、严格官方 wire transport，以及真实 Loader + deterministic fake adapter
> 的本地与远端 CI code gate；产品执行路径仍使用进程内 fake gateway，尚未把 DSH handle 接入 StepExecutor，也未通过
> cancel/recovery、生产 operator canary、72 小时 soak 或生产恢复演练。本文不能被解释为生产就绪声明。

本文规定如何在不改变 Pharos 控制面和安全边界的前提下，使用已 vendor 的官方 DeepSeek Harness
（简称 DSH）作为单个 Agent Attempt 的受限执行器。它不是把 DSH 变成 Pharos 的业务工作流引擎，
也不是开放一个可执行代码环境。

相关 source of truth：

- [`HARNESS_ARCHITECTURE.md`](HARNESS_ARCHITECTURE.md)：Pharos Run/Step/Attempt、策略、持久化和 API；
- [`HARNESS_IMPLEMENTATION_PLAN.md`](HARNESS_IMPLEMENTATION_PLAN.md)：阶段门与当前交付状态；
- [`HARNESS_WORKFLOWS.md`](HARNESS_WORKFLOWS.md)：Daily、Discovery、Project 的业务 Artifact contract；
- [`PHASE-HARNESS-KERNEL.md`](PHASE-HARNESS-KERNEL.md)：H0/H1 代码与 operator 证据；
- [`PHASE-HARNESS-DSH-WIRE.md`](PHASE-HARNESS-DSH-WIRE.md)：H1.5 wire/Loader canary 证据与剩余边界；
- vendor 上游：[README](../vendor/deepseek-harness/README.md)、[SAFETY](../vendor/deepseek-harness/SAFETY.md)、
  [architecture](../vendor/deepseek-harness/docs/architecture.md)。

## 1. 上游来源、许可证与风险

Pharos 在 `vendor/deepseek-harness/` 保存官方源代码快照，来源与可复核身份如下：

| 项目 | 固定事实 |
| --- | --- |
| 上游仓库 | [`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness) |
| 固定 commit | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| 上游版本 | `0.1.2-alpha.1` |
| 快照日期 | 2026-08-30 |
| 许可证 | MIT；传递依赖与 notices 仍以 vendor 快照中的 `LICENSE`、`THIRD_PARTY_NOTICES.md` 和 package notices 为准 |
| 供应链方式 | `scripts/vendor-deepseek-harness.sh` 直接 fetch 并校验完整 commit 后复制，移除复制 checkout 的 `.git`；生产运行时不安装依赖或下载 plugin |

MIT 只说明许可证许可范围，不代表安全性、API 稳定性或 Pharos 的产品承诺。上游 README 明确这是
developer preview，允许不兼容变更；上游 SAFETY 明确它未经安全审计、不是 production-ready，并且
可执行模型生成的代码/命令、加载第三方插件、访问网络/进程/凭据/文件。那些能力正是 Pharos
不能默认继承的风险面。

因此集成原则是：

- vendor 是可审阅的源码输入，不是自动获得能力的安装包；
- Pharos 只构造固定、最小的 DSH profile，生产不加载用户 patch、用户插件或任意 bundle；
- 上游 Session 的 durable log 只作为单个 Attempt 的执行证据和恢复辅助，不取代 Pharos DB；
- DSH 版本、配置 hash、sidecar binary/package hash 和协议版本必须进入 Attempt provenance；
- 任何上游升级都重新做许可证、依赖、协议、攻击面和回滚审阅，不能只更新目录。

当前源码门证据（2026-08-30）：在不提供用户 HOME/API key 的清理环境中以 frozen lock、
`--ignore-scripts` 安装后，`pnpm run build` 成功；SDK protocol/client/server 共 7 个文件、130 项测试通过。
`.github/workflows/harness-runtime.yml` 会在 vendor/运行时边界变化时复验来源、build 与 SDK contract。
这一组 source gate 证据只证明固定源码可构建和官方 wire 基线成立；safe profile 另由机器可读 policy、
effective-config 审计和 shutdown smoke 验证。父进程 transport 还通过真实 Loader/外置 fake adapter 验证了
receipt→running→turn→step→request→stream→assistant→idle→shutdown→EOF→exit 0→process-group empty→reap
的完整因果链；这些证据仍不证明
StepExecutor 集成、生产隔离或 operator gate 已通过。

## 2. 两层架构与控制权

集成后的边界如下：

```text
Pharos API / dispatcher / policy / usage / publication
                 │
                 │  private local stdio JSON-RPC v1
                 ▼
       one DSH sidecar process per active Agent Attempt
                 │
       one DSH Session: prompt projection, model turn,
       zero-tool v1 output, bounded session events
```

### 2.1 Pharos 是唯一 durable 控制真相

以下对象只能由 Pharos DB 的短事务、状态服务和配置 head 管理：

`Run`、`Step`、`Attempt`、`Event`、`Artifact`、`Approval`、`Usage`、Workflow Definition、
configuration revision/head、owner scope、lease、retry、pause/cancel、publication 和领域表。

Pharos 决定：是否允许启动、使用哪个 Workflow/Role/Capability、预算/期限/并发、输入 Artifact、
approval 状态、是否继续/重试/等待/`indeterminate`、是否发布，以及是否回滚。sidecar 不能通过
JSON-RPC 修改这些事实，也不能直接写 SQLite、领域表、blob、凭据或配置。

### 2.2 DSH Session 是单个 Agent Attempt 的内部执行日志

DSH Session 只承载该 Attempt 所需的 prompt projection、模型请求/响应、受控 tool call/result 和
session lifecycle。它可以帮助恢复或诊断一次 Agent turn，但：

- Session ID 不等于 Run、Step 或 Attempt ID；Pharos 保存显式映射；
- Session event 不得直接成为业务 Event、Artifact 或领域记录；发布必须由 Pharos validator 和
  idempotent publication service 完成；
- DSH 的 compaction、workflow、goal、subagent、job、schedule 或自由 session branching 不得改变
  Pharos DAG、状态、预算、owner 或 approval；
- DSH Session 的完成、错误或断开必须由 Pharos 映射成 typed Attempt outcome；未知外部模型送达仍为
  `indeterminate`，不能因为 Session 没有 response 就声称未计费；
- Session 原始事件按隐私 retention 处理；不得把 raw chain-of-thought、secret、完整私有输入或
  完整 provider response 复制进 Pharos Event/Artifact。

### 2.3 不采用 DSH workflow 作为业务工作流

Pharos 的 `daily.ingest`、`daily.issue`、`literature.discovery`、`project.research_cycle` 和
`harness.canary` 仍由 Pharos Workflow Registry、SQL 状态机、fan-in/reduction 和 publication
contract 定义。DSH 只执行其中标为 `agent` 的一个 Step Attempt；它不能加载用户提供的 workflow，
不能自行创建业务 child Run，也不能把 DSH workflow record 写成 Pharos Run。

## 3. 所有权矩阵

| 责任/数据 | Pharos durable Harness | DSH sidecar / Session | 约束 |
| --- | --- | --- | --- |
| Run/Step/Attempt 状态 | 唯一 owner | 只读映射 | DSH 不可改变状态；所有转换走 `HarnessStateService` |
| lease、heartbeat、reaper、retry | 唯一 owner | 不持有跨重启业务 lease | sidecar 退出由 Pharos 判定 abandoned/indeterminate |
| Workflow/Role/Capability allowlist | 唯一 owner | 启动时收到冻结快照 | DSH tool registry 只能是 Pharos 下发的子集 |
| prompt/context | 生成并哈希 Context Pack | 仅消费本 Attempt projection | 原始完整上下文不回写 Pharos Event |
| 模型策略与 provider accounting | Pharos ModelGateway facade/Usage 唯一 owner | DSH adapter 执行已批准 provider 请求并返回 usage/request metadata | 预算在调用前 reserve，结果未知进入 reconciliation |
| Approval/policy/entitlement | 唯一 owner | 无权自行询问或授予 approval | `ask` 必须停在 Pharos `waiting_for_approval` |
| tool Action/Observation | schema、授权、审计、发布由 Pharos owner | 只可发起 allowlisted action | 每个 action 经 JSON-RPC、大小/超时/风险校验 |
| Session event log | 保存受限摘要、cursor、hash 和引用 | 保存 Attempt 内部 log | Session 不是业务状态；按 retention 删除正文可保留 tombstone |
| Artifact/lineage/publication | 唯一 owner | 可返回候选 typed output | 未通过 validator 不得 publish；不可直接写领域表 |
| secrets/credentials | 后端 credential resolver | 只接收短时、最小、不可回显的 provider handle（如确有需要） | token/key 不进 Session 或 RPC payload/log |
| filesystem/process/network | 定义部署隔离与 provider allowlist | 只有模型 adapter 的批准 endpoint egress | 无 model-facing shell、subprocess、FS、web/network tool 或 MCP |
| DSH version/config | 记录 hash/provenance | 按固定 profile 启动 | vendor 更新需阶段门与可回滚版本 |

## 4. 官方 wire 与 Pharos 包装协议

DSH `0.1.2-alpha.1` 的官方 SDK 协议只在父进程与 child stdin/stdout 之间传输。stdout 仅允许 JSON-RPC
帧，日志写 stderr；不监听公网端口。传输采用 newline-delimited JSON-RPC 2.0。Pharos 第一版 adapter
必须先复用官方 wire，并在父进程增加严格 schema、Attempt 绑定、frame/buffer 上限与 deadline；不能把
下文的扩展草案误写成上游已经支持。

### 4.1 上游官方协议（H1.5 第一版）

| 方法/通知 | 方向 | 固定语义 |
| --- | --- | --- |
| `initialize` | Pharos → DSH | 一次性设置 `cwd/provider/model/reasoningEffort?/maxTokens?` |
| `session/prompt` | Pharos → DSH | 向一个 `sessionId` 提交 content blocks，返回 `messageId` |
| `shutdown` | Pharos → DSH | flush/dispose 并退出 |
| `session.event` | DSH → Pharos | 完整 Session event envelope |
| `session.status` | DSH → Pharos | Session agent 的 `idle/running` |
| `subagent.started/finished` | DSH → Pharos | 上游子 Agent 通知；初始 Pharos profile 禁用 subagent，因此收到即 fail closed |

Pharos 在发送前验证额外字段、cwd/session/provider/model/预算；每个 Attempt 使用 fresh Session 且首版只发
一次 prompt，只收该 Session 的事件并按 cursor/hash 去重。`session/prompt` 只确认 message 入队，官方 wire
没有 prompt→assistant response 的强绑定，因此只有在无并发 follow-up、收到一致的 `assistant/message`、
`turn/end` 与 idle 边界时才生成候选 outcome；歧义必须 fail closed。上游 server 自身没有 Run、Step、
Attempt、Artifact、Approval 或 Pharos Usage 语义，这些全部留在父进程。

### 4.2 Pharos 扩展握手（后续草案，尚未实现）

父进程启动时发送一次 `pharos/hello`，sidecar 只能返回能力协商结果：

```json
{
  "jsonrpc": "2.0",
  "id": "hello-1",
  "method": "pharos/hello",
  "params": {
    "protocol": "pharos.dsh.stdio@1",
    "run_id": "run-id",
    "step_id": "step-id",
    "attempt_id": "attempt-id",
    "workflow_key": "harness.canary",
    "workflow_version": 1,
    "role_key": "canary_agent",
    "context_sha256": "...",
    "allowlisted_capabilities": [],
    "max_frame_bytes": 262144,
    "deadline_epoch_us": 0
  }
}
```

响应必须包含 `protocol`、sidecar package/source hash、profile id、session id、supported methods 和
effective capability IDs。版本、attempt、context hash、profile 或 allowlist 不匹配时 fail closed。

### 4.3 Pharos 扩展方法（后续草案，尚未实现）

| 方法 | 方向 | 语义 |
| --- | --- | --- |
| `pharos/hello` | Pharos → DSH | 绑定一次 Attempt、协议和固定 capability snapshot |
| `pharos/start` | Pharos → DSH | 创建一个 Session；只接受已验证 Context Pack 引用/受限内容 |
| `pharos/turn` | Pharos → DSH | 请求一次 bounded model turn；不能循环至预算外 |
| `pharos/tool_result` | Pharos → DSH | 返回 Pharos 已执行的 typed Observation 或 typed denial |
| `pharos/cancel` | Pharos → DSH | 请求在安全边界停止；不撤销已发生副作用 |
| `pharos/flush` | Pharos → DSH | 要求 Session durable checkpoint/flush（若 profile 启用持久 Session） |
| `pharos/close` | Pharos → DSH | 正常关闭并返回 summary/cursor/hash |
| `pharos/event` | DSH → Pharos | 有界的 session phase/tool/usage/status notification；不是业务 Event |
| `pharos/tool_call` | DSH → Pharos | typed action proposal；必须经 Pharos policy/approval/executor |
| `pharos/final` | DSH → Pharos | typed Agent output candidate + provenance；不能直接 publish |
| `pharos/error` | DSH → Pharos | typed protocol/runtime error，不返回 stack/secret/raw CoT |

扩展协议明确不提供 shell、terminal、filesystem、subprocess、sandbox、E2B、code-runtime、MCP、插件安装、
自修改、任意 URL、工作流编辑、child-agent spawn、session export 或通用 `execute` 方法。未知方法、
未知字段、重复/过大 frame、错误 session/attempt、超时和 sequence gap 都是协议错误并 fail closed。

只有官方 wire 的纵切证明 typed capability/approval 无法在父进程安全表达时，才实现上述方法；届时它们
必须由一个薄、固定、受测的 Pharos DSH plugin 提供并升级 protocol hash。H1.5 初始 canary 不需要它们。

### 4.4 Attempt 事件顺序与崩溃语义

H1.5 首版只使用官方 wire。Pharos 在调用 `session/prompt` 前冻结 input Artifact/Context Pack hash、
reserve Usage，并为该 Attempt 启动一个全新的 Session；一个 Session 只接收一个 prompt，避免上游协议缺少
prompt 与 assistant final 一一绑定标识所造成的归属歧义。父进程仅接受该 Session 的有界通知，观察到
合法 final/idle 后校验输出、落库并执行有界 `shutdown`；首版不开放任何 tool。

`pharos/turn`、`pharos/tool_result`、`pharos/flush` 等属于 §4.3 的未来扩展草案，当前实现不得发送。
若以后确有 typed capability 或 checkpoint 需求，必须通过独立 ADR、固定 plugin 与新 protocol hash 引入；
sidecar 始终不知道 Pharos DB transaction，也不能把“已发送”当作“已结算”。

发生以下情况时：

- sidecar 在请求前退出：Attempt 可按 typed startup/protocol error 重试；
- 请求可能已发出但响应未确认：Pharos 标记 Attempt/Step `indeterminate`，等待 provider 对账或显式决定；
- tool action 已由 Pharos publication service 提交：重放按稳定 idempotency key 返回既有 receipt；
- sidecar 无响应、协议违规或 frame 超限：父进程终止它，保留最小诊断和 session cursor/hash，不能继续
  接收迟到消息覆盖 Attempt 终态；
- cancel：官方 wire 没有 per-session cancel；Pharos 记录持久 cancel request，并对本 Attempt child 执行有界
  shutdown/TERM/KILL/reap。若 provider 请求可能已送达则结果仍为 `indeterminate`，不能声称远端已取消；
  已发生的外部副作用不回滚。

## 5. 安全 allowlist / denylist

### 5.1 v1 allowlist

首版生产 profile 只允许：

- 一个固定 DSH SDK/headless 入口和一个 per-Attempt child process；
- Pharos 注入的固定 system contract、Context Pack 和 output schema；
- deterministic fake model（canary）以及经 Pharos ModelGateway 解析的受控 provider adapter；
- 零个默认 model-facing tool；未来 capability 必须先注册 Pharos typed Action/Observation、owner
  resolver、风险级别、超时、输出上限、幂等语义和 approval contract；
- 最大 turn/tool/frame/stdio buffer/Session bytes/wall time，并受 Pharos 剩余预算和 deadline 取交集；
- ephemeral、private temporary directory 仅供 sidecar 自身运行时需要，默认不可见、不可持久化为用户 artifact；
- 固定 locale、timezone、model profile 和 vendor/package hash；禁止读取项目目录的 profile/patch。

已提交的 base safe profile 还显式关闭内建 provider adapter、provider retry、settings、credentials 与 HMR，
并已通过外置 `pharos-fake` bundle 验证 LLM 注册 seam。它不能删除官方 server 在
`initialize(provider="deepseek-official")` 时动态挂载 fallback 的代码路径；严格父进程必须只允许当前阶段的
provider，生产容器还必须独立限制 egress。profile 校验通过不等于获得 OS 隔离或真实模型 entitlement。

首个真实 Loader 纵切已用 deterministic fake model + 零 capability 证明握手、单 turn、text output、usage、
wrong-model reject 与 clean shutdown；不调用真实模型、不执行真实网络、不写领域表、不读取本地 Zotero/PDF。
它尚未证明 active cancel、deadline/crash/restart、Session→Attempt 持久映射，或 claim→Artifact/usage→reducer。

### 5.2 v1 denylist（永久边界，除非新决策明确 supersede）

以下在 Pharos 初始 programme 中一律拒绝：

- model-facing shell、terminal、`child_process`、任意 subprocess、PTY、命令执行、code runtime、notebook、E2B；
- sandbox/workspace/容器逃逸面、任意 path read/write、全盘搜索、Zotero SQLite、Daily Vault 后台写入；
- 除已批准模型 provider endpoint 外的 HTTP/HTTPS、DNS、socket、浏览器、webhook、代理、MCP server 或 URL；
- 动态/用户上传/网络下载的 plugin、skill、bundle、patch、executable、workflow 或 agent preset；
- self-modification、安装依赖、写入 vendor/source/package/config、修改 evaluator/policy/allowlist；
- DSH workflow/job/schedule/goal/subagent/agent-team 用于创建或路由 Pharos 业务工作；
- 读取环境变量、进程表、宿主凭据、SSH/云 metadata、其他用户 Session 或任意 workspace 文件；
- 将 Session message、raw CoT、provider headers、key/token、完整私有论文或内部 stack 写入 Pharos Event/Artifact。

论文、网页、摘要、PDF 和 tool observation 都是不可信内容；其文本不能新增 capability、改变 policy、
提高 evidence level 或生成 approval。协议层的 deny 只是一层，Pharos policy、owner scope、validator、
OS 进程权限和数据库约束仍必须独立成立。

## 6. 资源部署与运行约束

开发/CI 可以由 FastAPI 测试父进程直接启动 fake/DSH child；这个形态没有生产 entitlement，因为同一
容器 uid 理论上仍可读取 API 容器可见的数据库和 secrets。正式启用真实 provider 前，必须在以下两种
形态中以攻击面测试和 RSS 数据冻结一种：

1. 独立 `pharos-agent-runtime` companion container：无 host port、无 DB/Zotero/其他服务 mount，只有
   Attempt-scoped session/artifact 目录和内部 UDS；容器只拥有批准 provider 的最小凭据；
2. 经独立安全审计的 OS process isolation：证明 sidecar 无法读取 API 数据目录、任意环境、宿主路径或
   其他进程；仅仅在 Cordis profile 中隐藏 FS tool 不算隔离。

当前首选是 1；它不是第二个 durable control plane，broker/child 崩溃后仍由 Pharos DB recovery 决策。
每个 active Attempt 最多一个 DSH Session/执行槽；初始全局并发为 1，runtime container 设置 256–384 MB
Node heap 上限并与 Translation、Daily 共享整机 4 GB 的 headroom。API 与 runtime 之间没有公网监听、
反向连接或用户可访问 endpoint。

父进程必须：

1. 在 claim 后、模型调用前建立 Attempt provenance、reserve usage 和 sidecar deadline；
2. 使用固定 argv/profile、清理后的 environment、最小 uid/working directory 和空/临时可写目录；
3. 不把 Pharos DB、Zotero data directory、`.env`、SSH、云凭据或任意 host mount 暴露给 runtime container；
4. 限制 stdin/stdout/stderr/frame/session/output 字节，stderr 只作脱敏诊断，超限立即终止；
5. 以 kill + reap 保证 cancel/shutdown/crash 后不残留进程，记录 exit class，不把退出码当业务成功；
6. 把 sidecar CPU/RSS、启动时间、stdio backpressure、queue age、协议错误和退出类别写入 privacy-safe metrics；
7. 启动时验证 vendor source/package lock/hash 与协议 schema；不匹配时整个 Agent execution gate fail closed。

这不改变 Pharos “数据库为真相”的部署决策：runtime companion 是 Runner 的受限执行器，不是第二个
队列或第二个 durable control plane。生产仍不得擅自增加 Uvicorn worker。

## 7. 隐私、保留与删除

DSH JSONL 在一个 Attempt 运行期间会持久化完整 Session 内容，因此不能宣称“从未落盘”。初始策略是把
它写入 runtime container 的 Attempt-scoped encrypted/private temporary storage，成功投影 typed Artifact
后立即删除正文；需要 crash recovery 时只允许短期 retention，并必须有容量和删除上限。Pharos DB 仅保存：
Session ID、Attempt 映射、协议/profile/source
hash、开始/结束时间、状态、序号水位、Context/输入/输出 hash、脱敏 usage、provider request ID（如有）、
typed error class 和 Artifact 引用。模型可见内容只有在其确属已授权 Context Pack 时才进入 sidecar；
不因 DSH 的“model-visible means logged”原则而扩大 Pharos 的数据留存范围。

若为恢复或安全调查启用受控 Session retention：

- 必须按 owner、sensitivity、retention policy 与法律删除请求隔离；
- 默认短于业务 Artifact retention，并设置总字节、单 Session、单 Attempt 上限；
- secret、token、header、绝对路径、raw CoT、完整私有正文和未脱敏 tool payload 在写入前 scrub；
- 删除正文时保留 Session/Attempt tombstone、hash、schema/protocol/source version、删除原因和 actor，
  使 Pharos Event/Artifact lineage 可解释但不可恢复原文；
- 导出只包含 owner-authorized typed summary，不提供原始 Session log 给普通用户或管理员；
- 管理员只能看到 sidecar 版本、队列、耗时、错误类别、资源和聚合 usage，不看 prompt/论文/Session 内容。

## 8. 阶段门与当前状态

DSH 适配作为 H1.5 插入 H1 code gate 与 H2 业务迁移之间；可以在非生产环境实现，但 H1 operational gate
未完成时不能切生产 route：

| 阶段 | DSH 集成要求 | 当前状态 |
| --- | --- | --- |
| H0 | 记录来源、许可证、denylist、协议 draft | H0 code gate 已通过；生产副本证据仍待 operator |
| H1 code | fake-model canary 先证明 Pharos durable kernel | Pharos H1 code complete；当前产品路径仍无 DSH sidecar |
| H1.5 source/profile | 固定源码、可复现 build、机器可读 denylist 与 safe profile | code gate 完成：source/build/130 SDK tests/6 policy tests/effective dump/shutdown smoke |
| H1.5 adapter/canary | 官方 wire adapter、fake runtime、真实 DSH fake adapter、Artifact/usage/process recovery | wire 与真实 Loader fake canary code gate 完成；per-Attempt gateway、cancel/deadline/recovery 仍在实现，禁止业务写入与真实 provider 默认调用 |
| H1 operational | operator canary、72h soak、rollback、resource/backup evidence | 未完成；H1 不是 Done |
| H2–H4 | H1 operational + H1.5 同时通过后，业务 Agent Step 使用 DSH | 未开始业务 cutover |
| H5 | Desktop/local capability 仍必须走 Pharos approval；DSH 不获得本地 bridge 权限 | Planned |
| H6 | 用真实 queue/RSS/latency/quality/retention 数据决定是否保留 DSH；需要新 runtime/依赖须新 ADR | Planned |
| H7 | 实验 sandbox 仍受 Decision 9 永久 deny；DSH 不能解除该 gate | Blocked by Decision 9 |

进入 durable claim→DSH/operator/product canary 的最小证据：协议 schema/hash golden、fake sidecar crash/restart、frame/timeout
拒绝、tool deny、usage conservation、owner/retention scrub、无公网端口检查、同一 Attempt 单 sidecar、
以及 Pharos Event/Artifact/Run reduction 的可重放测试。缺任一项时保持 fake-only。

## 9. 测试与质量门

必须同时有 Pharos 合同测试和 sidecar 协议测试：

- source/package/license/hash pin 与启动 fail-closed；
- JSON-RPC schema `extra=forbid`、未知 method/field、重复 id、sequence gap、过大 frame、非法 session/attempt；
- fake model 单 turn、bounded turns/tools/tokens/wall time、schema repair 最多一次；
- tool proposal 的 allow/ask/deny、approval pause/resume、未经批准 0 次执行；
- provider request-before-crash、response-unknown → `indeterminate`，不盲重试/不虚报费用；
- sidecar 启动失败、hang、OOM、stdout 污染、stderr 超限、SIGTERM/SIGKILL、父进程重启和孤儿回收；
- Session cursor/hash 映射不改变 Run/Step/Attempt/Event/Artifact/Usage 真相；late message 不能覆盖终态；
- capability catalog 只能来自 Pharos allowlist；论文/网页 prompt injection 不能扩权或改变工具目录；
- 无 shell/subprocess/非批准 provider network/FS/MCP/plugin/self-modification/E2B/code-runtime 的 negative tests；
- owner isolation、admin privacy、Session retention/tombstone、导出/删除和 blob 非恢复性测试；
- weighted admission 下 CPU/RSS/stdio backpressure、SQLite lock wait、queue age 与 translation headroom；
- deterministic fake-model canary 在不访问真实网络、真实 key、真实 Zotero/PDF、领域表的情况下完整通过。

质量门不是“DSH 返回了 JSON”：还要有 output schema、evidence level、provenance、业务 validator 和
人工标注质量评测。任何 model/framework 升级必须跑冻结 eval set；不能因为上游 developer-preview 测试
通过就宣称 Pharos workflow 或生产服务已就绪。

## 10. 回滚与待决策点

回滚优先级固定如下：

1. 新建 DB configuration revision，关闭 Agent execution/相关 route，停止创建和认领新的 DSH Attempt；
2. 对已有 Attempt 写持久 cancel request，并使用官方 shutdown/TERM/KILL/reap 收尾；官方 wire 没有
   per-session cancel，按可恢复/未知副作用规则落为 succeeded/failed/cancelled/indeterminate，
   不删除 Run、Event、Artifact、Usage 或 publication receipt；
3. 如配置通道异常，先启用 deny-only `PHAROS_HARNESS_EMERGENCY_STOP=1`，再修复并提交持久 revision；
4. 恢复到上一个经过 hash/测试的 sidecar/vendor profile；不回滚或删除 Pharos schema，不 DROP Harness 表；
5. 领域 publication 已成功的行保持有效；未知外部费用进入对账，不因回滚重复请求或虚构退款；
6. 验证旧 legacy route/人工 Project CRUD/Translation/阅读和本地 Zotero 没受影响，再解除 emergency stop。

需要产品/安全/运维在实现前明确的事项：

- 开发 profile/启动入口已冻结；生产 immutable closure/container 入口及可接受的 package/runtime 供应链范围；
- Pharos ModelGateway 与 DSH LLM adapter 的责任边界、provider request ID 和 usage reconciliation；
- Session summary/cursor 的最小字段、默认 retention 天数与删除/tombstone 位置；
- sidecar 的 CPU/RSS/启动/stdio 上限及与 Translation 的 admission 权重；
- 是否需要任何 typed capability；若需要，逐项 action/resource/approval/idempotency，而不是开放 DSH tool；
- fake-sidecar 合同测试通过后，哪一个内部 operator canary 账户可试运行，以及停止条件/升级回滚窗口；
- 上游 commit 升级触发何种新 ADR、license audit、协议迁移和双读/回滚策略。

正式结论是：**Pharos 已采用 DSH 作为 Agent Attempt 执行内核，当前正在实现 H1.5；现有代码中的 Agent
Step/内部 canary 路径仍使用进程内 deterministic fake gateway，生产 DSH route/runtime gate 尚未启用或部署。
safe profile、官方 wire transport 和真实 DSH fake canary 已通过本地与远端 CI code gate，但 per-Attempt
集成、隔离/资源/恢复证据和 operator gate 全部通过前，
不启用业务 DSH route、不默认执行真实模型、不开放上游高风险能力，也不宣称生产就绪。**
