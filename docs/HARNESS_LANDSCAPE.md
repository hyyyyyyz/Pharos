# Open-source Agent Harness landscape and Pharos decisions

> 调研快照：2026-08-15；DeepSeek Harness vendor 状态补充于 2026-08-30。Agent 框架变化很快，本文记录的是架构取样与设计理由，
> 不是依赖选型排行榜。实施前应重新核对目标版本、许可证和稳定性。

本文回答一个具体问题：Pharos 应该从 pi、OpenCode 等开源 Harness 学什么，又必须拒绝什么？
结论不是“挑一个框架接进来”，而是建立一个 Pharos 原生的 Research Harness：

> **Workflow 决定接下来允许发生什么；Agent 只决定一个受限步骤内部如何完成。**
> **聊天历史不是业务状态；Run、Artifact、Evidence 与领域表才是。**

总体架构见 [`HARNESS_ARCHITECTURE.md`](HARNESS_ARCHITECTURE.md)，具体工作流见
[`HARNESS_WORKFLOWS.md`](HARNESS_WORKFLOWS.md)，阶段计划见
[`HARNESS_IMPLEMENTATION_PLAN.md`](HARNESS_IMPLEMENTATION_PLAN.md)。已 vendor 的 DeepSeek Harness
集成边界、协议草案和安全 denylist 见 [`DEEPSEEK_HARNESS_INTEGRATION.md`](DEEPSEEK_HARNESS_INTEGRATION.md)。

## 1. 评估维度

每个项目都按同一组问题评估：

1. Agent loop 是否足够小，能否作为受控 Step executor；
2. Session、Run 和业务实体是否分离；
3. 是否支持持久化、重启恢复、幂等和人工中断；
4. Tool 是否有类型、权限、超时、输出上限与审计；
5. 多 Agent 是显式父子任务，还是自由群聊；
6. Context compaction 是否保留原始历史和来源；
7. 是否能服务 Web、Desktop 和自动调度，而不是只服务一个 CLI；
8. 对多租户论文、项目、密钥和本地 Zotero 数据是否安全；
9. 引入依赖后是否符合 Pharos 当前 FastAPI + SQLAlchemy + SQLite 与低资源部署；
10. 是否能以 deterministic fake model/tool 做可重复测试。

## 2. 对比总表

| 项目 | License（调研快照） | 最值得吸收 | 不适合直接搬入 Pharos 的部分 | 决定 |
| --- | --- | --- | --- | --- |
| [Pi Agent Harness](https://github.com/earendil-works/pi) | [MIT](https://github.com/earendil-works/pi/blob/main/LICENSE) | 极小 Agent Core、Provider 抽象、树形 Session、完整历史与压缩视图分离、hooks、fake provider | 官方明确没有内建文件/进程/网络权限系统；扩展继承宿主权限；偏本地编码会话 | 吸收 agent turn 与 context projection；拒绝其安全边界 |
| [OpenCode](https://github.com/anomalyco/opencode) | [MIT](https://github.com/anomalyco/opencode/blob/dev/LICENSE) | Headless App Server、OpenAPI/SSE、多 UI、父子 Session、角色 Agent、细粒度 allow/ask/deny | 高度围绕代码/文件系统；任意插件和工具不适合官方多租户服务；Session 不是 durable workflow | 重点吸收协议、权限与 Run UX；不采用其编码工具面 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | [MIT](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) | checkpoint、interrupt/resume、fault tolerance、subgraph、replay/fork、幂等要求 | 容易把业务事实塞进不透明 graph state；完整观测体验容易绑定额外服务 | 吸收 durable semantics；H0–H4 不直接引入依赖 |
| [OpenHands](https://github.com/OpenHands/software-agent-sdk) | [MIT](https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE) | 模块化 Agent/Tool/Conversation、Agent Server、local 与 ephemeral workspace 分离 | Workspace/容器体系过重；面向代码执行；会扩大当前安全与运维面 | 借鉴未来执行边界；当前不引入 Runtime |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) / [Harness](https://github.com/pydantic/pydantic-ai-harness) | [MIT](https://github.com/pydantic/pydantic-ai/blob/main/LICENSE) / [MIT](https://github.com/pydantic/pydantic-ai-harness/blob/main/LICENSE) | 类型化 tool/output、Provider 中立、capability、approval、eval、OTel | V2 core 已稳定，但独立 Harness 能力库仍为 0.x 且能力矩阵持续演进；durability 常接 Temporal/DBOS/Prefect | 立即采用类型与测试思想；独立 Harness 成熟度复审后再决定依赖 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | [MIT](https://github.com/crewAIInc/crewAI/blob/main/LICENSE) | Flow 管确定性、Crew 只负责局部自治；运行轮数/时间限制 | Role/Goal/Backstory 容易退化为角色扮演；自由 Crew 协作难复现 | 采用 “workflow first, pockets of agency”；拒绝群聊 |
| [AutoGen](https://github.com/microsoft/autogen) | [Code: MIT](https://github.com/microsoft/autogen/blob/main/LICENSE-CODE)；[docs: CC-BY-4.0](https://github.com/microsoft/autogen/blob/main/LICENSE) | Core/AgentChat/Extensions 分层、termination、保存 Team state、OTel | 官方仓库已提示 maintenance mode；GroupChat/speaker 选择成本高且不确定 | 只吸收分层与显式终止，不作为新依赖 |
| [OpenAI Codex CLI](https://github.com/openai/codex) | [Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE) | Thread/Turn/Item、app-server、审批作用域、sandbox、fork/resume、背压 | 面向代码执行，shell/filesystem 能力过重；本地 rollout 不适合作云端业务库 | 借鉴协议、事件与审批；映射为 Run/Step/Artifact |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（vendor 固定 commit） | MIT（上游 developer preview） | Cordis 插件组合、Session event log、Agent turn、checkpoint、typed tool seam、stdio/SDK 边界 | 上游默认能力可触及代码/命令、网络、进程、凭据、文件和第三方插件；Session/workflow 不是 Pharos 业务真相，且未安全审计 | **采用**为受限 Agent Attempt 执行内核；Node stdio JSON-RPC sidecar；不采用 DSH workflow，不开放高风险能力 |

除 DeepSeek Harness（2026-08-30 vendor commit）外，这些许可证是 2026-08-15 对官方仓库的逐项快照，
不替代依赖采用时对目标 commit、子目录、附带资产和传递依赖的复核。许可证宽松也不等于架构适配。Pharos 的客户端仍受
AGPL-3.0-or-later 与 Zotero 衍生边界约束，依赖采用还要单独通过许可证与供应链检查。

## 3. Pi Agent Harness

原 `badlogic/pi-mono` 已迁移到
[`earendil-works/pi`](https://github.com/earendil-works/pi)。它把能力拆成统一模型层、
Agent Core 和 Coding Agent UI。低层 Core 暴露消息状态、工具集合、顺序/并行 tool execution、
`beforeToolCall` / `afterToolCall` 和事件流，适合被更高层产品嵌入。
[Agent Core 文档](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)
说明了这种“小内核、宿主负责产品语义”的边界。

Pi 的 Session 使用 `id` / `parentId` 组成树形 JSONL，支持继续、fork、clone 和分支导航；
compaction 只替换送入模型的旧上下文，完整历史仍留在 Session 中。
[Session 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md#sessions)
与 [Compaction 文档](https://pi.dev/docs/latest/compaction) 都强调这一分离。

### Adopt

- Agent turn 是小而可测试的运行时，不承担业务数据库与调度职责；
- Provider Adapter 与 Agent loop 分离；
- 工具前后置 hooks 可统一做策略、计量、审计和脱敏；
- 完整历史不可变，模型看到的是从历史派生出的 Context View；
- Session branch/fork 是未来比较研究路线的好交互模型；
- faux/fake provider 适合无真实 API 的确定性测试。

### Adapt

- JSONL 只可作为导出、调试或便携包；生产真相写入 Pharos 数据库；
- compaction 必须成为 `ContextCheckpoint` Artifact，并保留 Evidence ID、页码和哈希；
- Pi 的自由 tool loop 要被 Pharos `max_turns`、预算、超时和 output schema 包住；
- subagent 不是新的自由进程，而是有父 `Run`、固定 Workflow 和权限交集的 child Run。

### Reject

Pi 官方明确说明它没有内建的文件系统、进程、网络或凭据权限系统，默认继承启动进程权限；
需要由使用方自己容器化或沙箱化。
[Permissions & Containerization](https://github.com/earendil-works/pi#permissions--containerization)
不适合作为官方多租户科研服务的默认安全模型。Pharos 也不加载项目目录中的任意 TypeScript
Extension，不用 tmux/子进程拼出生产多 Agent，不把 bash/read/write 当研究 Agent 的通用能力。

## DeepSeek Harness（已 vendor 的上游快照）

Pharos 固定 vendor 官方 DeepSeek Harness commit
`cd5ef8148158c3a752a658978873241fdf8e2bbc`（版本 `0.1.2-alpha.1`，MIT，快照日 2026-08-30）。
上游 README 将其标为 developer preview；上游 SAFETY 明确它未经安全审计且不是 production-ready，
并警告模型生成代码/命令、第三方插件、网络、进程、凭据和文件访问可能造成破坏。许可证许可不等于
Pharos 采用了这些能力，也不等于 Pharos 已生产就绪。

DSH 值得吸收的是小型 Agent turn、Session event/replay、checkpoint 和可替换的 model/tool seam；
但它的 everything-is-a-plugin 组合模型与 Pharos 的“受信代码注册、DB configuration head、单一
durable 控制面”不同。Pharos 不把 DSH Session 作为 Run/Step/Attempt 的第二真相，不把 DSH 的 workflow、
goal、schedule、job 或 subagent 用作业务流程，也不加载用户 profile、patch、plugin、MCP server 或
executable bundle。

采用形态固定为一个 per-Attempt Node sidecar，通过无公网端口的 stdio JSON-RPC 与 Pharos Runner 通信。
Pharos 保留 Run/Step/Attempt/Event/Artifact/Approval/Usage、policy、lease、retry、publication 和
owner scope 的唯一所有权；DSH Session 只记录该 Attempt 的内部模型 turn 和受限 tool 交互。首个纵切已以
sealed runtime、真实 Loader/DSH 进程、离线 deterministic fake adapter 和 durable DB 全链 canary 通过；
这只证明执行内核 code boundary，不代表业务 route、真实 provider 或生产隔离已启用。在协议、资源、隐私和
negative security/operator gates 全部通过前不接真实 provider。完整 allowlist、denylist、v1 协议和回滚门槛以
[`DEEPSEEK_HARNESS_INTEGRATION.md`](DEEPSEEK_HARNESS_INTEGRATION.md)
为准。

## 4. OpenCode

OpenCode 最有价值的不是某个 prompt，而是“执行内核与界面分离”。Headless server 发布
OpenAPI，并通过 SSE 输出事件；TUI、Web 和其他客户端都是协议消费者。
[Server 文档](https://opencode.ai/docs/server) 列出 Session 的创建、状态、child、fork、abort、
revert、权限响应和事件接口。

它区分 primary agent、subagent 和隐藏维护 agent；权限采用 `allow` / `ask` / `deny`，并能按
action 与 resource 匹配。V2 进一步把规则规范为有序的
`{action, resource, effect}`，覆盖路径、命令、URL、skill、MCP tool 和 subagent ID。
参见 [Agents](https://opencode.ai/docs/agents) 与
[V2 Permissions](https://opencode.ai/v2/docs/permissions)。

OpenCode 的 compaction 与 Pi 类似：生成结构化 checkpoint 和最近上下文，但不删除旧的 durable
messages。[V2 Compaction](https://opencode.ai/v2/docs/compaction)
明确说明 checkpoint 是模型视图，不是历史删除。

### Adopt

- Harness API 与所有 UI 分离；接口先有 OpenAPI contract；
- 异步 start、独立 status、abort、children、approval response 和 SSE；
- role 与 tool catalog 分离，禁止的 capability 不出现在模型可见目录；
- 权限规则同时看 action 与资源，不只看工具名字；
- parent/child Run 可导航，但 child 仍有独立状态与成本；
- compactor、title/summarizer 等维护 Agent 对用户隐藏，并拥有最小工具集合。

### Adapt

Pharos 的有效权限必须是以下集合的交集：

```text
workflow policy
∩ agent-role policy
∩ parent-run delegated policy
∩ user/project ownership
∩ privacy location
∩ subscription entitlement
∩ current approval grant
```

任何一层 deny 都优先；Agent 不能通过创建 child Run 提升权限。OpenCode 的 Session UX 映射为
Pharos `Run → Step → Attempt → Event`，而不是照搬聊天消息类型。

### Reject

- 官方后端不加载用户上传的任意插件、MCP server 或 executable tool；
- 不给“general” Agent 全部研究工具；
- 不把 raw shell command pattern 当安全沙箱；
- 不允许用户绕过 Workflow 直接调用内部 Agent；
- 不用 Session message stream 表达“论文已导入”“证据已验证”等业务事实。

## 5. LangGraph

LangGraph 的核心价值是 durable execution，而不是“多 Agent”。其 persistence 在步骤边界保存
checkpoint；同一执行层中已经完成的任务写入可在失败恢复时复用。
[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 说明 checkpoint 支撑
fault tolerance、state inspection、memory 和 time travel。

[`interrupt()`](https://docs.langchain.com/oss/python/langgraph/interrupts) 可持久暂停并等待人工输入；
恢复会重新进入节点，因此 interrupt 之前的副作用必须幂等。
[Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) 则区分 per-invocation、
per-thread 与 stateless 子图，避免所有子 Agent 默认共享无限记忆。

### Adopt

- 每个可恢复边界都必须有 durable checkpoint；
- 完成的 sibling Step 不因另一个 Step 失败而重跑；
- approval 是可无限期等待的正式状态，不是阻塞一个 HTTP 请求；
- 外部副作用必须有 idempotency key；
- child workflow 默认 per-invocation 隔离；
- replay/fork 只能从安全 checkpoint 开始。

### Adapt

H0–H4 在 Pharos 自己的 SQLAlchemy 模型上实现这些语义。原因不是重新发明图引擎，而是当前部署是
单 FastAPI 进程、SQLite WAL、有限 CPU/内存，三条业务流已有成熟领域服务；直接引入通用 graph state
会形成第二套状态权威。未来出现多实例、数千并发 Run 或动态图需求时，再用真实指标重新做依赖 ADR。

### Reject

- 不把 `DailyPaper`、`Evidence` 或 `ProjectArtifact` 埋进一个大 state JSON；
- 不要求 LangSmith 才能查看运行；
- 不开放用户提交 Python graph；
- 不从任意历史 checkpoint 重放已经发生的付费或写入副作用。

## 6. OpenHands

当前 OpenHands V1 把可组合的 Python/REST Agent SDK 与远程 Agent Server 分开；同一个 Agent 可以在本机
workspace 运行，也可以由 Agent Server 放进 Docker/Kubernetes 等 ephemeral workspace。
[Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) 明确把 Agent、Tool、Conversation、
Workspace 和远程 Server 当作独立边界。这对 Pharos 未来的实验执行有价值：模型提出受类型约束的工具动作，
真正的外部副作用由隔离执行面完成并返回观察结果。

### Adopt

- Tool 使用 typed action / result，而不是返回含义不明的字符串；
- Agent 控制面与 workspace/执行 Server 分离；
- Event 是可审计事实，UI 是投影；
- 工具声明 read-only、destructive、idempotent、open-world 等风险元数据；
- stuck detection、OTel trace 和 bounded parallelism 进入质量要求；
- 可恢复 child task 用稳定 task ID，而不是同步占住父 Agent。

### Adapt

当前 Daily 和 Discovery 只需要受限 HTTP/domain-service executor，不需要容器。只有未来正式逆转
“不执行实验”的产品决策后，才新增独立 sandbox runner；它不能与 API 容器共享宿主权限、密钥或
Zotero 文库。

### Reject

- H0–H4 不引入 Docker workspace、browser computer-use 或 shell runtime；
- secrets 不写入 conversation/event payload；
- 不让模型本身成为最终风险判定器；
- 长子任务不能让父 Agent 同步等待到超时；
- Event log 不能代替领域数据库。

## 7. Pydantic AI and Pydantic AI Harness

Pydantic AI 与 Pharos 的 Python/FastAPI 栈天然接近。其官方定位包括 type-safe tools/outputs、
Provider 中立、approval、eval、OTel 与 durable integrations。
[项目 README](https://github.com/pydantic/pydantic-ai) 是这些能力的主入口。

V2 把 capability 作为组合单元：一项 capability 可以同时带 instructions、tools、hooks、model settings
和生命周期行为。[Capabilities 文档](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/overview.md)
也展示了 deferred loading、MCP、history processing、event processing 与 durable adapters。
官方另有 [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness) 能力库。

### Adopt now

- 所有 workflow input/output、tool Action/Observation、Artifact payload 使用 Pydantic 严格校验；
- `extra="forbid"`，schema version 明确，拒绝模型多写的未知字段；
- Provider/Model 调用通过一个 gateway，业务代码不直接拼各家请求；
- capability 同时声明 schema、权限、风险、预算、超时和版本；
- fake model、dataset、evaluator 和回归阈值是一等代码；
- OpenTelemetry 语义与供应商无关。

### Defer dependency decision

Pydantic AI **core V2.0 已于 2026-06-23 发布稳定版**；不能把 core 整体描述为 beta。其
[Version Policy](https://github.com/pydantic/pydantic-ai/blob/main/docs/version-policy.md) 承诺 major
版本内的兼容边界，同时明确标注在 `beta` 模块中的个别功能仍可快速变化。与 core 分开的官方
[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness) 在本次快照仍是 0.x 能力库，能力矩阵
中也有多项在开发或规划；这是需要延后依赖决策的演进面。durable execution 另外常通过 Temporal、DBOS
或 Prefect adapter 接入。H0 不应同时引入 Agent framework 和新的 durable runtime。先稳定 Pharos
自己的 `ModelGateway` / `AgentRunner` 接口；H4 后以替换一个 fake-backed adapter 的方式做 spike，
只有收益超过迁移成本才采纳。

### Reject

- 不允许普通用户通过 YAML/JSON 定义可执行 capability；
- 不自动连接任意 MCP server；
- 不让动态 capability 改写系统策略；
- 不把仍处于 beta/0.x 的 capability API 变成 Pharos 的持久数据库格式。

## 8. CrewAI and AutoGen

CrewAI 的可取之处是把 Flow 与 Crew 区分：可预测流程由 Flow 管理，开放式判断只在局部交给 Agent。
Pharos 将其改写为：**deterministic spine, bounded agency**。但 Role/Goal/Backstory 不是协议；任何角色都要
有明确输入、输出、工具、最大轮数和失败策略。

AutoGen 的 Core / AgentChat / Extensions 分层、termination condition、cancellation 和保存 Team state
仍值得参考；但 [AutoGen 官方仓库](https://github.com/microsoft/autogen) 已将项目置于维护阶段并指向
新的 Microsoft Agent Framework。Pharos 不应把新内核建立在 maintenance-mode 依赖上，也不采用
RoundRobin/LLM speaker selection 让聊天顺序决定科研流程。

## 9. Codex CLI

Codex 的 app-server 将长期 Thread、一次 Turn 和消息/命令/文件修改等 Item 分开，并为 resume、fork、
interrupt、approval 与事件通知提供协议。
[App-server 文档](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
体现了“协议先于 UI”的思路；[Rust 架构入口](https://github.com/openai/codex/blob/main/codex-rs/README.md)
记录了 sandbox 与审批边界。

Pharos 采用协议思想，但语义改为：

| Codex 概念 | Pharos 对应 |
| --- | --- |
| Thread | ResearchProject 或长期用户目标，不直接等同 Harness Run |
| Turn | 一个 Run 或一次人工 resume 输入 |
| Item | Step Event、Approval、Artifact 或可见消息 |
| command/file diff | typed Capability Action/Observation |
| sandbox escalation | policy-scoped approval grant |

不会采用通用 shell/file workspace，也不会把大 PDF 或模型密钥内联到事件流。

## 10. 融合后的 Pharos 原则

综合取样后，Pharos 的目标组合是：

```text
Pi 的小 Agent turn 与 context projection
+ OpenCode 的 App Server、权限和父子运行 UX
+ LangGraph 的 checkpoint / interrupt / idempotency 语义
+ OpenHands 的 Tool / Result 与 workspace/runtime 边界
+ Pydantic 的 typed contract 与 eval
+ CrewAI 的 workflow-first 原则
+ Codex 的审批、事件投影与背压
```

但实现不是上述项目的拼盘。H0–H4 保持一个 Python/FastAPI 代码库、一套 SQLAlchemy/SQLite 真相、
一个版本化 Workflow Registry，并把框架替换能力留在内部接口后面。

## 11. 明确拒绝的反模式

以下设计不得以“更智能”为理由进入 Harness：

1. 多个 Agent 在一个聊天室里自由轮流发言；
2. 让 LLM 决定全部路由、重试、权限或预算；
3. 父 Agent 任意创造新 Agent、新工具或新权限；
4. 让聊天摘要、prompt 或 context window 成为业务真相；
5. 用 compaction 代替 durable checkpoint；
6. 用重放整个会话恢复已经发生过的副作用；
7. 默认开放 shell、任意网络、文件系统或本地 Zotero 数据；
8. 把 API key、OAuth token、完整私有论文或 raw chain-of-thought 写入 Event/Trace；
9. 在论文、网页或工具输出中的文字改变系统权限；
10. Agent 产物直接写成 `verified` Claim、实验 Result 或已阅读全文的结论；
11. 官方服务动态加载用户提供的 executable plugin/MCP；
12. H1 同时引入 Redis、Celery、Temporal、LangGraph 和一个新 Agent SDK。

## 12. 依赖复审触发条件

不直接采用框架不是永久拒绝。满足任一条件时创建新 ADR：

- SQLite lease 在真实负载下成为瓶颈；
- 需要多个机器上的 worker、跨区域调度或数天等待的高并发 Run；
- 工作流定义需要安全的动态编辑与版本迁移；
- 自研 AgentRunner 的 Provider/structured-output 适配成本持续高于 Pydantic AI；
- 未来实验 sandbox 需要成熟的 Runtime API；
- 内部观测无法满足 trace、eval 或合规要求。

复审必须拿 benchmark、故障注入结果、迁移方案和回滚成本说话，不能只比较框架功能列表。
