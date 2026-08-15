# DeepSeek implementation prompts for Pharos Research Harness

> 用途：把本文件中的提示词复制给 DeepSeek，让其在读取仓库真实状态后分阶段落实
> Pharos Research Harness。本文不是实现状态说明；Harness 的架构真相、业务流程与验收门槛仍以
> `docs/HARNESS_*.md` 为准。

## 使用方式

1. 第一次只使用“主提示词：H0 + H1”。不要把 H2、H3、H4 一起交给同一轮 Agent。
2. H1 Gate 通过并由人或另一位 Agent 审查后，才分别使用 H2、H3、H4 提示词。
3. 后续阶段必须串行进入同一主分支；不要让两个实现 Agent 同时修改 Harness kernel、数据库模型或迁移。
4. 提示词要求阶段性提交，但不授权生产部署、Tag 或 Release。是否 push 服从调用者当次明确指令。
5. 如果仓库文档与代码冲突，先把冲突写进实施报告；只有能从既有决策和测试推出唯一答案时才直接修正。

---

## 主提示词：只实现 H0 + H1，然后在 Gate 停下

~~~text
你正在 Pharos 仓库内实现 Pharos Research Harness。你的任务不是做一个演示页面，也不是一次性完成所有科研工作流；本轮只完成文档定义的 H0（基础契约与迁移地基）和 H1（可持久化、可恢复的 Harness kernel）。完成全部代码与本地/隔离环境 Gate 后立即停下并汇报；如果只剩 operator production canary、回滚演练或 72 小时 soak，不得谎称 H1 Gate 已通过，而应报告 `H1_CODE_COMPLETE_AWAITING_CANARY`。禁止开始 H2 文献探索迁移、H3 每日论文迁移、H4 项目研究迁移。

你的目标是交付一个真实可运行、可测试、可恢复的最小内核，而不是空接口、TODO、内存 mock 或只能在单次 HTTP 请求中工作的“原型”。

## 0. 开始前必须完整阅读

先找到仓库根目录，然后完整阅读下列文件，不得只看标题、摘要或搜索片段：

1. `AGENTS.md`
2. `docs/HARNESS_LANDSCAPE.md`
3. `docs/HARNESS_ARCHITECTURE.md`
4. `docs/HARNESS_WORKFLOWS.md`
5. `docs/HARNESS_IMPLEMENTATION_PLAN.md`
6. `docs/ARCHITECTURE.md`
7. `docs/DECISIONS.md`
8. `docs/ROADMAP.md`
9. `docs/RESEARCH_WORKFLOW.md`

随后阅读真实代码，至少覆盖：

- `backend/pharos/main.py`
- `backend/pharos/config.py`
- `backend/pharos/db/session.py`
- `backend/pharos/db/models.py`
- `backend/pharos/api/deps.py`
- `backend/pharos/api/jobs.py`
- `backend/pharos/services/translation.py`
- `backend/pharos/services/ai_chat.py`
- `backend/pharos/daily/scheduler.py`
- `backend/pharos/daily/service.py`
- `backend/pharos/services/discovery.py`
- `backend/pharos/services/projects.py`
- 对应 API router、schema 与测试
- `.env.example`、Docker/CI 配置和依赖文件

用 `wc -l` 和分段读取确保长文档读到 EOF。先运行 `git status --short`、`git log -5 --oneline`，识别调用者或其他 Agent 的未提交改动；不得覆盖、格式化或顺手修改不属于本任务的文件。

阅读后先输出一份不超过 40 行的 preflight：

- 当前 commit 和工作树状态；
- H0/H1 的准确边界；
- 当前数据库迁移、后台任务、SSE、认证、owner scope 的真实基线；
- 你计划建立的 package/file 边界；
- 预计的阶段提交；
- 发现的文档/代码矛盾与处理方式。

只要没有命中本文 Stop Conditions，就在同一轮继续实现，不要停下来等待“确认计划”。

## 1. 本轮绝对边界

### 必须实现

- 显式、编号化、可记录 revision 的数据库迁移机制；
- H0/H1 所需的严格 Pydantic contract、枚举、版本化定义与 registry compiler；
- Run / Step / Attempt / Event / Artifact / Artifact Link / Approval，以及计划要求的 Usage 最小账本；
- 集中的合法状态机，禁止业务代码直接赋任意状态字符串；
- owner-scoped repository；
- 数据库为真相的 dispatcher、lease、heartbeat、reaper、retry 与 restart recovery；
- 幂等 Run 创建、幂等 Step 展开和幂等副作用边界；
- cursor-based Event replay 与文档要求的 REST/SSE contract；
- 有界的 runner/canary workflow；
- deterministic fake clock、fake model、fake capability/tool 和 failure injection；
- feature flag 与旧 API 兼容；
- H0/H1 的单元、集成、竞争、崩溃恢复与安全测试；
- 完成后只使用实施计划允许的 `Planned|In progress|Shadow|Canary|Cut over|Done|Blocked` 状态更新文档；缺少
  operator Gate 时不得标 `Done`，也不得宣称 H2+ 已完成。

### 本轮禁止实现

- H2 文献探索业务迁移；
- H3 每日论文业务迁移；
- H4 项目研究业务迁移；
- Desktop Local Capability Bridge；
- 实验执行、代码生成执行、GPU 调度或浏览器自动化；
- 通用 shell、bash、Python exec、任意文件系统或任意 URL Agent tool；
- 真实 Zotero 文库读写；
- 生产部署、服务器 SSH、数据库手工迁移、Tag、Release；
- 为“未来可能需要”而重写 translation、Daily、Discovery、Projects 或 AI Chat；
- 用户上传 executable workflow、Agent、plugin、MCP server 或 Python/JS 代码。

## 2. 禁止引入的依赖和架构捷径

H0/H1 禁止引入 Redis、Celery、Temporal、LangGraph、CrewAI、AutoGen、OpenHands、DBOS、Prefect，或任何新的通用 Agent/Workflow runtime。也不要引入 Pydantic AI 作为 durable runtime。使用 Pharos 现有 FastAPI、SQLAlchemy、Pydantic、SQLite WAL 与 asyncio 栈，接口处保留未来可替换 seam。

禁止：

- 用一个 3000 行 `harness.py` 完成全部功能；
- 用进程内 dict、Queue、Task 或 Event 作为执行真相；
- 把 Conversation/Chat History 当作工作流状态；
- 用 Context Compaction 代替 checkpoint；
- 用 BackgroundTasks 假装可恢复 worker；
- 在数据库事务内等待 LLM、HTTP、文件下载或其他慢 I/O；
- 通过 `Base.metadata.create_all()` 或 `_add_missing_columns()` 冒充复杂 migration；
- Alembic autogenerate 后不审查就执行；
- catch `Exception` 后把失败标成成功或 partial；
- 为了让测试通过而 sleep、关掉 foreign keys、放宽 owner filter 或删除约束；
- 永久记录逐 token delta、raw chain-of-thought、完整私有 PDF 或 secret。

## 3. H0：先建立不可绕过的地基

严格以 `docs/HARNESS_IMPLEMENTATION_PLAN.md` 的 H0 为准；以下是最低执行要求，不是可选建议。

### 3.1 显式数据库迁移

建立一个小而明确的 migration runner，要求：

- 每个 migration 有稳定 revision、顺序、说明和不可变实现；
- 数据库记录已经成功应用的 revision；
- 一次 startup upgrade batch 在同一 connection-level `BEGIN IMMEDIATE` 事务中完成 ledger/bootstrap、全部待应用
  revisions 与索引/FTS；单个 migration 有独立 revision/checksum 但不得自行 commit 或换 connection。任一步失败使
  整批 rollback、不记录任何新 revision，应用启动明确失败；
- fresh DB、checked-in 历史 schema fixture upgrade、重复启动/幂等、checksum mismatch、interrupted migration、restore 六类测试/证据全部存在；
- H0 的首个 revision 只建立 migration ledger；`harness_*` 业务表必须留到 H1 的独立 revisions；H1 第一批 Harness schema migration 只新增表、索引和必要约束，不删除/重命名旧表，不重写用户数据；
- 保留 `_add_missing_columns()` 对旧简单 nullable 列的兼容，但 Harness 复杂演化不得加入其中；
- 明确 migration 与 `create_all`、FTS 初始化的执行顺序并用测试固定；
- 不提供“失败时删表”的自动回滚。回滚依靠停用 feature flag、恢复备份或后续 forward migration；
- migration 文件不得读取配置 secret、调用网络或执行业务 service。

如果现有数据库初始化顺序使上述要求无法安全实现，先通过最小改动分离 schema bootstrap 与 versioned migration，不要顺手重写整个 DB 层。

历史 fixture 是本地代码 Gate，生产副本 restore 是独立的 operator operational Gate：

- 先从已发布 tag/commit 的历史源码识别可复核 schema contract，再提交由它**确定性生成**的旧库 fixture/generator；它只能包含结构和合成数据，不得从当前 ORM 即席反推一个“旧库”冒充升级样本，也不得包含用户内容、生产路径或 secret；
- production backup/restore 与旧 image 启动验证只能由 operator 在隔离副本上执行。你只能读取已经提交或由调用者明确提供的脱敏报告，不得 SSH、读取生产数据库或自行部署；
- 如果 Git 历史中没有足以还原任何受支持发布版 schema 的可信 contract，且维护者也未提供，则报告 `H0_BLOCKED`；不要用当前 ORM 猜。若 contract 可复核，则本轮必须生成并提交 fixture/generator，不能因文件起初不存在而停止。如果仅缺 operator restore 证据，而全部 H0 代码/fixture Gate 已通过，则报告 `H0_CODE_COMPLETE` 并可继续本地 H1 实现，但不得把 H0 标为 Done、不得进入 staging/production。

### 3.2 Contract 与定义编译器

建立文档指定的 `backend/pharos/harness/` package。至少包含职责分离后的：

- 状态、风险、敏感度、结果和错误枚举；
- Run/Step/Attempt/Event/Artifact/Approval 的内部 contract 和 API schema；
- Workflow、Role、Capability 的版本化不可变 definition；
- canonical JSON 和 SHA-256；
- registry/compiler；
- compile-time validation。

所有模型输出、Capability Action/Observation 与 Artifact payload 必须：

- 使用严格 Pydantic schema；
- `extra="forbid"`；
- 有 schema name/version；
- 有大小上限或在 runner/capability 边界执行上限；
- 不接受未知 tool、role、workflow 或 version；
- 不允许模型直接添加任意 DAG 节点。

编译器必须测试拒绝：环、重复 step key、缺依赖、无限 fan-out、缺 timeout/attempt/budget、未版本化 prompt/tool/schema、Agent tool 超出 Workflow allowlist、非幂等 publish 配重试、approval 无 reject/expire 分支，以及 sensitivity 不兼容。

### 3.3 可替换 Port 与 deterministic fake

在不迁移现有业务的前提下定义最小接口：

- Clock；
- ID/worker identity source（如果测试需要）；
- ModelGateway；
- CapabilityExecutor；
- EventStore；
- ArtifactStore；
- Run/Lease repository；
- DispatcherWakeup；
- UsageLedger。

为 H1 测试实现：

- 可推进时间的 FakeClock；
- 按脚本返回 typed response、schema error、429、5xx、timeout、取消和 usage 的 FakeModel；
- 可记录调用次数/idempotency key、模拟副作用前后崩溃的 FakeCapability；
- 不依赖真实网络、真实 API key、真实模型或真实 Zotero 的测试夹具。

### 3.4 Feature flags

严格采用实施计划的单一配置真相：activation、active Workflow version、业务 writer mode 与 gates 必须共同进入
DB-backed immutable configuration revision，由 singleton head 以 expected-revision CAS 一次激活。H0 先冻结
`HarnessConfigSnapshot/WorkflowRoute`、依赖 validator、canonical hash、bootstrap 与 emergency-stop contract；
H1 再建立 config revision/routes/head 表和 operator-only apply service。至少区分：

- Harness API/registry 是否暴露；
- Dispatcher/worker 是否执行；
- 任何业务 workflow 是否迁移（本轮全部保持关闭）。

兼容 `PHAROS_HARNESS_*` / `PHAROS_*_EXECUTION` env 只在 DB head 不存在的新库提供安全 bootstrap defaults；head
存在后不能覆盖 DB 或静默生成 revision。`PHAROS_HARNESS_EMERGENCY_STOP=1` 只能 deny 新 Harness
Run/claim/Run-Step control-write/publish，不切换 legacy writer、不改 head；独立鉴权的 operator config revision
endpoint 必须仍可提交通过完整 validator 的回滚快照，不能被 emergency stop 锁死。默认 revision 必须保证旧安装
升级后不会突然启动 Agent、消费额度或改变 Daily/Discovery/Projects 行为。新增表的安全 migration 可以在功能关闭时
运行；功能关闭时旧接口响应、状态码和副作用保持不变。

### 3.5 H0 code gate 与 operational Gate

H0 代码完成后先运行目标测试并单独提交。只有以下 code gate 全部成立才可在本地进入 H1：

- fresh DB migration 通过；
- 至少一个 checked-in、从已发布历史 schema contract 确定性生成且只含合成数据的旧库 fixture 可升级；
- migration 重复运行不改变结果；历史 revision checksum 被修改、revision 缺失/未知时启动明确失败；
- interrupted migration 在每个故障注入点都必须完整 rollback，既无新 revision 也无部分 DDL；做不到则
  `H0_BLOCKED`，不能用“人工恢复状态”放宽 code gate；
- registry hash 稳定；
- 非法 workflow 全部被拒绝；
- fakes 无网络且结果确定；
- feature flag 关闭时现有 route/build 测试通过；
- 没有旧表语义或 API 行为变化。

任何 code gate 失败都不得进入 H1。operator 提供的隔离 backup/restore 报告还必须证明恢复后旧 image 能在
Harness flags 关闭时启动；这项 operational Gate 缺失不阻塞本地 H1 编码，但阻塞 H0 Done、staging 与 production。
Agent 不得自行访问生产补证。

## 4. H1：实现 durable kernel

### 4.1 数据模型与 owner scope

按架构文档建立正式 Harness 表。每个用户可见实体直接保存 owner/scope，不依赖跨多层 join 才判断所有权。要求：

- 建立 `harness_config_revisions`、`harness_config_workflow_routes` 与 singleton `harness_config_head`；revision/routes
  immutable，完整 snapshot 先通过依赖/Decision/definition FK 校验，再在一个 `BEGIN IMMEDIATE` 短事务内以
  `expected_head_revision` CAS head，CAS 失败整笔 rollback；不得另建可独立 PATCH 的 activation/env authority；
- route 只有 `disabled + legacy` 可无 active version；`active|deprecated` 或 `shadow|harness` 必须引用已注册的
  同 key definition，尚未实现的 H2+ Workflow 缺省为 `disabled + legacy`；只有无 legacy domain writer 的
  allowlisted internal/canary Workflow 可使用 NULL execution mode；
- Run 固化 `config_revision_id`；Run start、dispatcher claim、legacy/Harness writer 选择和每次 publication transaction
  都以 current head 作 fencing，旧 cache/revision 不能在切换后继续写；
- 用户 ID 查询与不存在 ID 对外统一 404；
- system scope 与 user scope 显式区分；
- 唯一约束使用 `(scope_type, scope_id, ...)`，不要依赖 SQLite 对 nullable unique 的行为；
- Artifact immutable，修订用 link/supersedes；
- Attempt 在 active 生命周期内只能由 `HarnessStateService` 通过带 expected state/lease owner 的 CAS 更新 heartbeat、usage 摘要和终态；一旦进入 `succeeded|failed|timed_out|cancelled|abandoned|blocked|indeterminate` 就冻结。重试永远新增 Attempt，不复活或覆盖旧 Attempt；
- Event append-only，具有单调 cursor `seq`；
- error/message/payload 有脱敏与长度上限；
- secret、token、credential URL、完整 header、raw CoT 不入库；
- 管理员查询只能返回聚合运行指标，不能读取用户论文标题、query、Evidence、Artifact 正文或项目内容。

每个 repository 方法都把 owner/scope 作为必填输入，测试跨用户按 ID、列表、事件、Artifact、Approval 全部不可见。不要先 `session.get(id)` 再在 Python 中检查 owner。

### 4.2 状态机

建立唯一 `HarnessStateService`（或实施计划指定名称），穷举并测试 Run、Step、Attempt 的合法转换。至少覆盖：

- queued → running / paused / cancelled；
- Step pending → ready → leased → running；
- running → succeeded / failed / cancelled / retry_scheduled / waiting_for_approval / waiting_for_input / indeterminate；
- condition=false、approval reject 或 optional policy branch → skipped；
- lease 过期 → abandoned；
- approval resolve 后的继续、拒绝和过期；
- terminal state 不可 resume；
- `state` 与 `outcome` 分离；
- Run 的终态由其 Step 与 publication 结果确定，不由 API 随意指定。

非法转换必须抛 typed domain error，并且不能产生半条 Event 或半个状态提交。API、worker、runner、capability 不得直接赋状态字符串。

### 4.3 Claim、lease、heartbeat、reaper

Dispatcher 必须以数据库为真相：

- 用短事务和条件更新原子认领 due `ready` Step；同一事务重新读取 current config head 并验证
  Harness/dispatcher/Workflow activation fence；
- 认领写 `lease_owner`、`lease_expires_at_epoch_us`、`heartbeat_at_epoch_us` 并创建 Attempt；所有 lease/deadline/heartbeat 使用归一化 UTC Unix epoch integer，不能依赖 SQLite naive datetime；
- 两个独立 Session/worker 竞争同一步时最多一个成功；
- 慢 I/O 在事务外执行；
- heartbeat 只能由当前 lease owner 更新；
- 过期 lease 的 Attempt 标记 abandoned；
- 只有 capability 声明可安全重试、预算和 attempts 尚有余额时才重新 ready；
- 非幂等或副作用未知的过期 Attempt 不得盲重跑；
- 进程内 wakeup 丢失后，轮询仍能最终执行；
- 后端重启时 reaper/dispatcher 可从 DB 恢复，不需要内存中保存旧 Task。

Claim 必须是单条 conditional UPDATE 或显式短 `BEGIN IMMEDIATE` 事务，双 worker 测试使用文件型 SQLite 与独立连接；同步 SQLAlchemy repository 从 async dispatcher 调用时经 `asyncio.to_thread` 或专用有界线程池，等待 I/O/资源时不持有事务。

不要假设单 Uvicorn worker 永远不变。H1 仍使用 SQLite，但 repository/lease 接口不能把 SQLite 特殊 SQL 散落在业务层。

### 4.4 Runner、重试、取消和预算

实现一个只运行受信任 definition 的有界 runner：

- 每轮检查 cancel、pause、deadline、max turns、max tool calls、token/cost/request 预算；
- Provider 429/部分 5xx，以及**客户端能够证明请求尚未发送**的 connect timeout，才按固定策略指数退避+jitter；
  若无法证明未发送，按 §4.5 的模糊送达进入 `indeterminate`，测试中 jitter 可控；
- schema error 最多一次 typed repair，之后 terminal；
- 401/403、owner mismatch、SSRF/policy deny 不自动重试；
- 达到边界返回明确错误，不把半成品标 `succeeded`；
- cancellation 是持久请求，不能只 cancel 当前 asyncio Task；
- pause 不认领新 Step，running Step 在安全边界退出；
- usage 使用 reserve/settle/release 或实施计划定义的等价原子语义；
- runner 永远只看到 policy 过滤后的 capability catalog。

H1 使用 canary workflow 验证内核。Canary 必须能通过 fake 执行成功、可重试失败、terminal 失败、等待 approval、取消、pause/resume 和 crash recovery；它不能读真实论文、调用网络或写任何旧领域表。

### 4.5 幂等和崩溃窗口

至少实现并测试：

- 相同 owner/workflow/idempotency key 的重复 `POST /runs` 返回原 Run；
- 同一个 mapped stable key 不生成重复 child Step；
- 同一 Step 的重复执行不会产生重复 Artifact/publication；
- side effect 在执行后、DB commit 前崩溃时，FakeCapability 可用 idempotency key 返回已有结果；
- DB commit 后、HTTP response 前重试不会重复创建 Run，也不会重复结算 Pharos 自己的 Usage ledger；
- sibling Step 已成功时，另一个 Step 失败/恢复不会重跑成功 sibling；
- retry 创建新 Attempt，不覆盖前次 error/usage；
- approval grant 只绑定具体 action/resource/version/attempt，不能变成全局授权。

上述 exactly-once 只覆盖 Pharos 控制的 Event/Artifact/领域 publication mapping。对普通 OpenAI-compatible 或搜索 Provider，若没有真实 idempotency key/status lookup，发送后崩溃属于模糊送达：Attempt/Step 进入 `indeterminate`，记录 request ID（若有）与可能费用，不得自动重试或承诺供应商不会重复收费；重新调用必须新 Attempt、新预算，必要时再次审批。

### 4.6 Event replay 与 SSE

实现文档规定的 API，不允许 SSE 成为唯一真相：

- 先按 owner 校验 Run；
- REST 可分页读取 `seq > after_seq`；
- SSE 用短 Session 完成认证与 owner 校验后必须关闭该 Session，再从 DB replay 并进入周期性 DB live tail；不能让 StreamingResponse 持有 request-scoped transaction，也不能仅依赖进程内 Queue；
- Bearer token 只在 header，不放 query；
- heartbeat 不写永久 Event；
- 不永久存逐 token delta；
- event page/payload/replay、每 owner/进程连接数和 live buffer 都有 hard cap；慢消费者收到带 durable cursor 与 retention floor 的 `resync_required`，再用 REST cursor 补齐；
- 断线用旧 cursor 重连不丢 Event、不重复改变状态；
- polling 客户端即使完全不用 SSE 也能正确工作。

对现有 `/api/jobs` SSE 不做破坏性重写；Harness 使用自己的 event contract。

### 4.7 API 与应用生命周期

按架构/实施计划实现 H1 endpoint：workflow list、run create/list/detail、pause/resume/cancel、events、event stream、artifacts、approvals，以及仅面向 allowlisted Workflow 的 schedule list/create/update/delete。另提供 operator-only config status/validate/apply 接口（CLI 或 admin route），只接受完整 snapshot + `expected_head_revision`。Schedule 是 H1 必做项；`fork` 明确属于 H6，本轮禁止实现。

要求：

- `POST /runs` 返回 `202`；
- 服务端从 current config head 解析 active workflow version/writer fence，并固化 config revision + definition snapshot/hash；
- API 不允许客户端提交任意 workflow JSON、role、tool、model 或旧 version；
- response 不返回 prompt、secret、internal stack、完整私有内容；
- 路由使用现有 auth dependency；
- OpenAPI/Pydantic response 与实际一致；
- lifespan 启停 dispatcher 时可重复、可取消、无悬挂 Task；
- current DB revision 或 emergency stop 关闭 dispatcher/worker 时 API 只保留安全的只读诊断；普通用户
  `POST /runs` 明确不可用，不能创建永远 queued 的 Run；
- DB revision 关闭 Harness 时旧 API route test 与生产启动路径保持不变。

本轮不要求漂亮的 Run Center 产品 UI。只有实施计划明确把最小状态查看 UI 放在 H1 时才实现；不得因此提前改 Daily/Discovery/Project 页面。

## 5. 测试门槛

不要使用真实网络、真实模型、真实用户 key、真实 Zotero 数据目录或生产数据库。测试必须使用临时 data dir 和隔离 SQLite。

最低测试矩阵：

### Migration

- fresh DB；
- 旧 schema fixture upgrade；
- 重复启动；
- 中途失败不写 revision；
- 历史 migration checksum mismatch 明确失败；
- 未知/缺失 revision 明确失败；
- 隔离 backup restore 后旧 image/旧 schema contract 在 Harness flags 关闭时仍可启动；production restore 只接受 operator 提供的脱敏证据，Agent 不执行；
- 表、FK、unique、index、check constraint 与 ORM 一致。

### Contract/registry

- canonical hash 稳定；
- definition version 不可变；
- 非法 DAG、权限、fan-out、budget、schema、publish/retry 组合被拒绝；
- unknown fields 被拒绝。

### Configuration revision

- 完整 snapshot canonical hash、definition FK 与 gate dependency validation；
- 两个 operator 用同一 expected head 竞争时只成功一个；失败者无残留 revision/routes；
- head 切换与 stale dispatcher、legacy request、Harness publisher 竞态，旧 fence 在切换后不能写领域表；
- head 存在后 bootstrap env 不覆盖 DB；emergency stop 只 deny Harness，read/export 与旧领域数据仍可用；
- restart 后 current head、route 与 revision hash 一致。

### Owner/security

- 两个用户的所有 list/get/event/artifact/approval 互相不可见；
- IDOR 返回 404；
- secret/redaction；
- prompt injection 文本不能改变 capability catalog；
- 管理员无内容访问路径。

### Durable state

- 状态转换表；
- 双 worker claim race；
- heartbeat owner；
- lease expiry/reaper；
- restart recovery；
- retry/backoff；
- pause/resume/cancel；
- approval approve/reject/expire；
- terminal state；
- sibling result reuse。

### Idempotency/failure injection

- 重复 Run start；
- 重复 Step expansion；
- side-effect-before-commit crash；
- commit-before-response retry；
- duplicate usage settlement；
- malformed/oversized model/tool output；
- budget exhausted；
- fake timeout/429/5xx/schema error。

### Event/API

- REST cursor replay；
- SSE reconnect；
- bounded slow consumer/resync；
- Bearer auth；
- route schema/status；
- existing app route tests。

运行：

- 新增的 Harness 专项测试；
- 数据库、auth、app route、config、jobs SSE 的相关回归测试；
- `cd backend && .venv/bin/pytest` 全套后端测试。

如果全套测试存在与本改动无关的基线失败，必须用同一 commit 复现并在报告中给出命令、失败名和为什么无关；不得静默跳过。若失败可能由本改动引起，Gate 不通过。

## 6. 阶段提交纪律

必须保留可回退的阶段提交。建议边界如下；如果实施计划给出更细边界，以实施计划为准：

1. versioned migration foundation；
2. contracts、definitions、registry 与 deterministic fakes；
3. durable models、repositories、state machine、events/artifacts/approvals；
4. dispatcher、lease、reaper、runner 与 canary；
5. API、SSE、lifespan、feature flags；
6. 完整 fault-injection/compatibility tests 与状态文档。

每个提交前：

- `git diff --check`；
- 运行该阶段目标测试；
- 检查 `git status --short`，只暂存本阶段自己的文件；
- 不使用 `git add -A` 吞入调用者或其他 Agent 的改动；
- 不 rewrite、squash 或 reset 已有历史。

提交作者、邮件与 Co-Authored-By trailer 严格遵守 `AGENTS.md`。不要设置 repository-local `user.email`，不要伪造调用者身份。H1 **code gate** 通过前不要 push；达到 `H1_CODE_COMPLETE_AWAITING_CANARY` 后，只有调用者明确授权才可 push，供 CI/operator canary 使用。Push 本身不授权生产部署、SSH、Tag 或 Release。

## 7. H1 code gate 与最终 Gate：达到当前授权边界后立即停止

按 `docs/HARNESS_IMPLEMENTATION_PLAN.md` 和 `docs/HARNESS_ARCHITECTURE.md` 的 checklist 逐项给证据。至少满足：

- Workflow/Role/Capability version/hash 不可变；
- 显式 migration 的 fresh/upgrade/repeat/checksum/interrupted/fixture-restore 六类本地代码测试全部通过；
- DB 是执行真相，进程重启可恢复；
- Run/Step/Attempt 合法转换集中并穷举测试；
- Attempt 只在 active 生命周期内由状态机/CAS 更新，终态冻结，retry 新建 Attempt；
- activation/writer mode/gates 只有一个 DB config head；revision CAS、stale writer fencing、bootstrap env 与
  emergency stop 测试通过；
- 双 worker 不重复认领；
- crash/retry/idempotency 测试通过；
- Event 可 cursor replay，SSE 可断线恢复且不是正确性前提；
- Artifact immutable，provenance 完整；
- owner scope、IDOR、admin privacy 测试通过；
- approval 持久且作用域最小；
- Agent/runner 有 turns/tools/tokens/cost/time hard bounds；
- secret/raw CoT 不落库不进 trace；
- feature flag 关闭时旧 API、Daily、Discovery、Projects、AI Chat、translation 不变；
- 所有测试不访问真实 Zotero/网络/API key；
- 当前低资源部署约束下没有无界 Queue、无界 fan-out 或 import-time worker。

上面是代码与隔离环境 Gate。最终 `H1_GATE_PASSED` 还必须**另外**有实施计划要求的 operator 证据：生产数据库副本 verify/upgrade/backup/restore、staging 连续重启/kill/SSE 断线、只对管理员开放且禁用真实模型的 production operator canary、至少 72 小时 soak 无孤儿 Step/重复 Artifact/usage 不守恒或持续写锁、资源预算记录，以及成功的 rollback 演练。本提示词不授权你部署、SSH 或等待 72 小时；若这些 operator 证据未在开始前由调用者提供，代码 Gate 全部通过后的唯一正确状态是 `H1_CODE_COMPLETE_AWAITING_CANARY`，不是 `H1_GATE_PASSED`。Operator 报告不能替代前一段的任何本地代码测试。

达到 `H1_CODE_COMPLETE_AWAITING_CANARY` 或已有证据证明最终 Gate 通过后：

1. 不开始 H2；
2. 不部署；
3. 不 Tag/Release；
4. 不把 Daily/Discovery/Projects 标成 Harness 已迁移；
5. 只提交最后的 H1 gate/documentation commit，并按下述格式汇报。

## 8. Stop Conditions

命中以下任一项时停止修改，保留已经通过测试的阶段提交，并清楚报告 blocker；不要自行扩大权限或猜产品决策：

1. 四份 Harness 文档缺失、无法完整读取或彼此给出无法调和的相反要求；
2. 工作树中有其他人的改动正在修改同一 Harness/DB/migration 文件，无法安全隔离；
3. 唯一可行方案需要破坏旧 API、重写旧领域表或进行 destructive migration；
4. 需要访问生产服务器、生产数据库、真实 `.env`、真实 API key 或真实 Zotero 文库；
5. 需要引入本文禁止的 runtime/queue/framework 才能继续；
6. migration fixture 暴露无法安全自动升级的数据状态；
7. owner scope、幂等或恢复语义存在两种会产生不同安全/计费结果的解释，而现有决策无法裁定；
8. 目标测试持续失败且可能是本改动造成，无法在本阶段修复；
9. 发现 secret 已进入 diff、日志、fixture 或 commit；立即停止、从工作树安全移除并报告，不要回显 secret；
10. H0 code gate 未通过；不得以“后面一起修”进入 H1。仅缺 operator operational evidence 时按 `H0_CODE_COMPLETE` 继续本地 H1，但不得部署；
11. H1 Gate 未通过；不得开始 H2；
12. 剩余工作本质属于 H2 Literature Discovery、H3 Daily Papers、H4 Project Research、Local Bridge 或 Experiment Runner。

普通编译错误、可定位测试失败、格式问题和实现困难不是 Stop Condition；应继续修复。

## 9. 最终汇报格式

严格按以下结构汇报，不能只说“完成了”：

Status: H1_GATE_PASSED | H1_CODE_COMPLETE_AWAITING_CANARY | H0_CODE_COMPLETE | H0_BLOCKED | H1_BLOCKED

Base / Head:
- base commit:
- head commit:
- worktree:

Implemented:
- H0:
- H1:

Migrations:
- revisions:
- fresh DB evidence:
- upgrade fixture evidence:
- repeat/failure evidence:

Runtime contract:
- workflow/role/capability versions:
- state machine:
- lease/reaper:
- idempotency:
- event replay/SSE:
- approvals/artifacts/usage:

Feature flags and compatibility:
- flags/defaults:
- old API evidence:
- explicitly unchanged features:

Tests:
- command:
- passed:
- failed/skipped:
- baseline comparison if needed:

Security/privacy:
- owner-scope evidence:
- secret/CoT handling:
- admin privacy:
- Zotero/network isolation:

Commits:
- hash + purpose + tests run before commit

H1 code/final Gate checklist:
- one line per gate: PASS/FAIL + exact evidence

Known limitations / deferred work:
- H2 Literature Discovery:
- H3 Daily Papers:
- H4:
- later infrastructure:

Stop reason:
- `H1 code complete; operator canary/72h soak required before H2 Literature Discovery`、`H1 Gate passed; stopped before H2 Literature Discovery`，或准确 blocker。
~~~

---

## 后续独立提示词：H2 文献探索纵向迁移

> 仅在 H1 Gate 已通过、审查完成且主分支干净时使用。

~~~text
你要在已稳定的 Pharos Research Harness 上只实现 H2：文献探索（Literature Discovery）纵向迁移。不要开始 H3 每日论文、H4 项目研究、Local Capability Bridge 或实验执行。

先完整阅读 `AGENTS.md`、四份 `docs/HARNESS_*.md`、H1 Gate 报告、当前 Harness kernel，以及现有 Discovery provider/service/API/schema/frontend/desktop/tests。先复跑 H1 kernel gate；失败则停止。

实现实施计划中的 Discovery workflow，要求：

1. 用户 idea/brief 经严格输入 schema 和 owner/project scope；
2. Query Planner 只输出有界 `ExpansionProposal`，不能直接改 DAG、指定任意 URL/tool/model；
3. 多源搜索作为 bounded mapped Step，所有 source adapter 使用现有 SSRF、timeout、response-size 与 provider 安全边界；
4. DOI、arXiv ID、标题/作者等规范化与去重由确定性代码完成；
5. `baseline_rank` 由确定性代码计算，是 H2 唯一决定成员与顺序的排名，使用 stable tie-break 并保留可解释分项；H2 不做模型 rerank，Critic/cluster Agent 也不能删论文或改 baseline 顺序；
6. deterministic `paper.rule_card@1` 先保留规范 metadata、可核对的原始摘要句和“待 AI 分析”状态；无摘要条目停在 `metadata_only`，不得把空文本送给 Reader。只有真实摘要存在时 Reader 才消费 metadata + 原始 abstract，并经 validator 产生紧凑中文 `paper.trick_card@1`，证据固定 `abstract_only`；Reader 失败仍保留 rule card/结果，不得声称全文、页码、`unlocated` 或 `page` 证据；
7. clustering、research gap 与 skeptical critic 输出 typed Artifact，不把模型推断冒充 quote 或 verified fact；
8. 每个结论能回到 canonical Paper/Source、原始 abstract 或输入 card ID；搜索不完整必须返回 partial/incomplete，模型不得自报页码；
9. 用户可把选中结果幂等提升到云端文库或项目，但每个写动作都必须经过绑定具体资源/hash/version 的精确 approval，再调用既有 owner-scoped domain service；输入中的 `project_id` 本身不构成授权；
10. 旧 `/api/discovery` contract 在 flag 关闭时完全不变，并有防双写/重复搜索策略。

H2 不实现 selected full-text branch，不下载 PDF、不读取本地 Zotero、不创建 `paper.fulltext_card`，并保持 `PHAROS_HARNESS_FULLTEXT_ENABLED=0`。全文深化只能在后续文档指定的独立 Gate 中实现，不能因为 schema 预留了 evidence level 就提前执行。

测试必须覆盖多 source partial error、重复 DOI/arXiv、同标题异论文、fan-out cap、两 worker、重启、预算、malformed model output、prompt injection、oversized abstract、owner IDOR、cursor replay、promotion idempotency 和 compact Chinese card golden dataset。全部使用 fake provider/model，不访问网络。

阶段提交至少拆为：workflow/contracts；query expansion/search adapters；normalize/rank；reader/cards/critic；publication/API compatibility；quality/fault tests/docs。不得部署、Tag、Release。

H2 Gate 通过后立即停止，以主提示词相同格式汇报，并额外报告：搜索覆盖/去重指标、Trick Card 人工或 golden quality gate、provenance 完整率、partial failure 示例。不要开始 H3 Daily Papers。
~~~

---

## 后续独立提示词：H3 每日论文纵向迁移

> 仅在 H1、H2 Gate 均通过且主分支干净时使用。

~~~text
你要在已经通过 H1 Kernel Gate 和 H2 Literature Discovery Gate 的 Pharos Research Harness 上只实现 H3：每日论文（Daily Papers）纵向迁移。不要开始 H4 项目研究、Local Capability Bridge 或实验执行。

首先完整阅读 `AGENTS.md`、四份 `docs/HARNESS_*.md`、H1/H2 Gate 报告、当前 Harness 实现，以及现有 `backend/pharos/daily/`、Daily API/schema/tests、Frontend/Desktop Daily 调用和 `docs/DAILY_VAULT_FORMAT.md`。验证 H1 的 migration、state、lease、event replay、owner scope、feature flags 和 fake tests，以及 H2 的关键回归仍通过；否则停止，不在坏掉的 kernel 上继续。

按实施计划完成两个边界独立但在产品上仍呈现为“每日论文”的 Workflow：

1. `daily.ingest@1` 是 system scope。level-triggered schedule / catch-up 使用冻结的 `daily.ingest:{date}:{schedule_ref}:{definition_sha256}` 创建全局幂等 Run；key 不含 owner、用户方向或个人配置；
2. ingest 的来源抓取、规范化、全局去重、metadata publication 是 deterministic/mapped capability Step；摘要 Reader 是有 schema、有预算的 Agent Step，只生成不含用户偏好的公共中文 abstract card；
3. ingest 结束时只把 allowlisted public metadata/abstract/card manifest，以及脱敏 ingest outcome、coverage loss、public typed source errors、evidence level 与必要 provenance ID/hash 注册为 public release。`release_sha256` 必须严格等于 `SHA256(canonical_json({release_id, source_schema_name, source_schema_version, source_content_sha256, public_manifest_sha256, release_policy_version}))`：先生成 immutable `release_id`，客户端不能覆盖；同内容撤销后 reissue 必须得到新 ID/hash。`daily.issue@1` 是 user scope，使用 owner scope 内冻结的 `daily.issue:{date}:{ingest_release_sha256}:{direction_config_sha256}:{issue_policy_sha256}`，因此 reissue 不会命中已撤销 release 的 Run；首步通过专用 release/projection service 幂等创建该 owner 的 `daily.ingest_projection@1`，后续只消费 user projection，禁止直接引用 system Artifact，也不重新抓取或重读公共论文；所有复制 projection 内容的 user Artifact 必须用同 owner `derived_from` link 建立可遍历 lineage；
4. issue 的方向匹配、相关性规则、排序和 cap 全部是 deterministic Step，输出命中词与可解释分项；Agent 不得决定成员、删除候选或重排。仅“今日脉络” digest synthesis 可作为有界、可失败降级的 Agent Step；H3 不定义 Daily critic；
5. Daily v1 只允许 `metadata_only → abstract_only`。不得下载/读取全文，不产生 `unlocated`/`page`，不得声称已阅读全文；
6. 每篇论文保存 provenance、source error 与真实 evidence level；多来源 partial failure 必须可见，不能把缺失结果标成完整成功；
7. publication 幂等写回既有 Daily 领域表；旧 API 仍是领域权威和兼容入口，用户方向、私人排序和 personal BYOK 结果不得写入共享 `DailyPaper`；
8. Daily Vault 输出保持版本化、可导入、可恢复，不把 Harness DB dump 当 Vault；
9. Zotero、本地 PDF 和 Vault 本地目录只能由 Desktop 的现有显式用户动作处理。本阶段不远程控制 Desktop，不读取 `zotero.sqlite`；
10. system ingest 只使用 `system_shared`/官方模型预算；owner-scoped issue 的可选 digest 才可按策略使用该用户 BYOK。所有 usage 经 H1 UsageLedger，达到预算返回明确 incomplete/partial；
11. 两个 Workflow 共享同一独立 Daily execution mode 进行灰度；关闭时现有 DailySweeper/Scheduler 行为完全不变，不能双跑、重复请求 Provider 或重复扣费。

必须使用 fake source/model/clock，测试 schedule duplicate、catch-up、source 429/5xx、去重、ranking、schema repair、budget、restart、approval、publication idempotency、Vault round-trip、owner scope 与旧 API compatibility；还要覆盖非 public/system Artifact 拒绝投影、同 owner 投影幂等、跨 owner projection/Run 404、canonical release hash golden fixture、客户端 hash override 拒绝、同内容 reissue 产生新 ID/hash/key，以及 projection 的脱敏 partial/coverage/public error/provenance contract。Release revoke 的 race/replay 测试必须证明：新 projection/issue 被拒；重复撤销幂等；所有 owner 的 projection 及其 `derived_from` feed/digest 等非领域派生 payload 被 lineage-aware tombstone；Run/Event/Artifact/link/hash/receipt ID 保留并返回 `content_deleted(reason=source_release_revoked)`；独立 approval/publish 的领域记录不静默删除但 receipt 标出 revoked source。不得要求网络。

阶段提交至少拆为：Daily workflow contract；source/deterministic steps；Agent/eval steps；publication/compatibility；scheduler cutover/flags；fault-injection/eval/docs。每阶段测试后提交。不得部署、Tag、Release。

完成 H3 Gate 后立即停止，以主提示词相同格式汇报，并额外给出：旧 Daily 与 Harness Daily 的双跑防护、质量 golden dataset 指标、partial/incomplete 示例、Vault compatibility 证据。不要开始 H4。
~~~

---

## 后续独立提示词：H4 项目研究纵向迁移

> 仅在 H1–H3 Gate 均通过且主分支干净时使用。

~~~text
你要在已稳定的 Pharos Research Harness 上只实现 H4：项目研究（Project Research）工作流。它负责从 Project Brief、文献和 Evidence 形成可审查的研究计划、Claim/Evidence Graph 与下一步建议；它不执行实验、不生成并运行代码、不使用 shell/GPU，也不自动宣称研究结论成立。

先完整阅读 `AGENTS.md`、四份 `docs/HARNESS_*.md`、H1 Kernel/H2 Literature Discovery/H3 Daily Papers Gate 报告、`docs/DECISIONS.md` 中项目/实验边界，以及现有 Projects、Evidence、LiteratureSearch、ProjectArtifact 的 model/service/API/schema/frontend/desktop/tests。复跑前置 Gate 的关键测试；失败则停止。

实现实施计划中的 Project Research workflow：

1. 启动时生成不可变 `project.snapshot@1`；运行中 Project/Evidence/Literature 领域数据变化不能静默改写本次输入；
2. 统一 DAG 固定为 `project.research_profile@1` → `project.search_execution_plan@1` → 编译后的可选 bounded Discovery child Run → `project.evidence_matrix@1` → `project.hypothesis_set@1` → `project.critique@1` → 最多一次 superseding hypothesis revision → `project.research_plan@1` → `project.decision_packet@1` → approval/publication；`project.search_execution_plan@1` 是检索执行计划，`project.research_plan@1` 是批判后的不可执行研究建议，二者不得混为一个 Planner 输出；
3. Research Planner 只提出有界计划和 child-run proposal，Harness 校验权限、fan-out、预算和 schema 后才创建 Step/child Run；child Discovery 结果不会自动成为 `ProjectSource`；
4. 明确区分 hypothesis、claim、question、evidence、counter-evidence、method proposal、decision 与 human note；
5. Claim/Evidence link 保存 provenance、evidence strength、source locator，relation 只允许 canonical `supports|contradicts`；没有可定位 Evidence 的不确定项写入 `project.evidence_matrix@1.unknowns_zh`，不得另造第三种 relation；
6. Critic/Reviewer 必须寻找反例、证据缺口、范围限制和不可验证假设；
7. Agent proposal 默认只是 immutable Artifact，不直接修改用户项目；
8. 只有用户明确选中的 proposal 可以在精确作用域 approval 后，通过既有 owner-scoped service 幂等新建 `ProjectArtifact(status="draft")`；选中论文加入项目必须另行精确审批，并先成为该 owner 的 `LiteratureResult` 后再创建 `ProjectSource`。不创建“项目任务/决定”等不存在的领域实体，不修改 stage，不把 draft 改为 ready/verified，也不覆盖既有 Artifact；decision packet 仍只是 immutable Harness Artifact；
9. 永久禁止 Harness 创建 `ProjectArtifact(type="result")`，不生成伪实验 Result，不声称指标已验证；实验建议只能是不可执行计划 Artifact；
10. 父子 Run 通过 Artifact link 传递输入输出，不用自由 Agent-to-Agent chat；
11. 管理员仍只能看聚合运行量，不能读取项目 brief、论文、Evidence 或 Artifact 内容。

测试至少覆盖 snapshot 稳定性、claim/evidence schema、unsupported claim、contradiction、insufficient evidence、critic failure、child fan-out cap、approval reject/expire、publication idempotency、owner IDOR、restart、budget、prompt injection、领域记录并发变化和无 shell/tool escalation。全部使用隔离数据库与 fake model/tool，不访问真实 Zotero、网络或用户 key。

阶段提交至少拆为：snapshot/contracts；planner/validated expansion；claim-evidence graph；critic/reviewer；approval/publication；API/UI compatibility；quality/fault tests/docs。不得部署、Tag、Release。

H4 Gate 通过后立即停止，以主提示词相同格式汇报，并额外给出：人工 approval 路径、证据强度分布、unsupported/contradicted claim 示例、明确未实现的 experiment runner 边界。不要自行进入 H5/H6。
~~~

---

## 审查者快速核对

无论由哪个模型执行，审查者至少确认：

- 它是否真的读完四份 Harness 文档，而不是只复述提示词；
- H0 是否真实过 Gate并单独提交；H1 是已有完整 operator 证据的 `H1_GATE_PASSED`，还是如实停在 `H1_CODE_COMPLETE_AWAITING_CANARY`，不得混写；
- H1 是否存在真实 DB recovery，而不是进程内 Task 恢复；
- migration 是否有旧库 fixture，而不只是 fresh `create_all`；
- owner 是否进入每条查询；
- two-worker claim、crash window、idempotency 是否有测试；
- Event replay 是否基于数据库 cursor；
- fake model/tool/clock 是否让测试完全离线；
- feature flag 关闭时旧功能是否真的不变；
- Agent 是否没有 shell、任意网络、任意插件和 Zotero 本地库权限；
- 最终报告是否明确停在指定 Gate，没有把后续 `Planned` 阶段写成已经实现或 `Done`。
