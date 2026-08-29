# Pharos Research Harness — staged implementation plan

> 状态：**H0 code gate 通过；H1 代码完成，等待生产 canary / 72 小时 soak / 回滚演练。**
> H0 尚未标 Done（缺 operator 生产副本恢复证据）；H1 状态为
> `H1_CODE_COMPLETE_AWAITING_CANARY`，不是 `H1_GATE_PASSED`。H2–H7 全部 Planned。
> 阶段状态与证据见 [`PHASE-HARNESS-KERNEL.md`](PHASE-HARNESS-KERNEL.md)。
> DeepSeek Harness 已完成固定来源导入与上游 build/SDK contract 验证，并确定为 Agent Attempt 执行内核；
> no-tool 安全 profile、机器可读 policy、effective-config 审计与 shutdown smoke 已完成；严格 stdio adapter
> 与真实 DSH fake canary 正在 H1.5 实现，尚未改变当前 fake-model canary 或任何生产状态。
> 它不是“预计完成”清单；只有通过本阶段全部退出门槛，阶段状态才可以改成 Done。

本文必须与以下文档一起阅读：

- [`HARNESS_ARCHITECTURE.md`](HARNESS_ARCHITECTURE.md)：数据模型、状态机、权限、执行和 API 的 source of truth；
- [`HARNESS_WORKFLOWS.md`](HARNESS_WORKFLOWS.md)：Daily、Discovery、Project Research 的业务步骤与 Artifact contract；
- [`HARNESS_LANDSCAPE.md`](HARNESS_LANDSCAPE.md)：Pi、OpenCode、LangGraph、OpenHands、Pydantic AI 等项目的
  Adopt / Adapt / Reject 结论；
- [`DEEPSEEK_HARNESS_INTEGRATION.md`](DEEPSEEK_HARNESS_INTEGRATION.md)：固定 DeepSeek Harness 快照的
  sidecar 协议、所有权、安全 allowlist/denylist、隐私、测试与回滚门槛；
- [`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md)：当前研究实体、证据强度和人工 checkpoint；
- [`ARCHITECTURE.md`](ARCHITECTURE.md)、[`DECISIONS.md`](DECISIONS.md) 与
  [`CLIENT_DATA_ARCHITECTURE.md`](CLIENT_DATA_ARCHITECTURE.md)：产品边界、Zotero 本地数据和不可逆决策；
- [`ROADMAP.md`](ROADMAP.md)：已实现能力、已知缺口与明确 non-goals。

实现者在每个阶段开始前都要重新读取这些文档和仓库根目录的 [`AGENTS.md`](../AGENTS.md)。代码与本文冲突时
不得自行选择“更方便”的一边：先核对当前实现，再修正文档或提交新的决策。

---

## 1. Program outcome

H0–H6 完成后，Pharos 应具有一套生产可用的 Research Harness：

1. Daily、Discovery 和 Project Research 都以版本化 Workflow 运行；
2. HTTP 请求只创建或操作 Run，不等待长任务完成；
3. Run、Step、Attempt、Event、Artifact、Approval 和 Usage 以数据库为真相；
4. 服务或客户端重启后能从安全 checkpoint 恢复；
5. Agent 只在有界 Step 内判断，不能自行增加工具、权限、预算或 fan-out；
6. 所有自动产物有 schema、hash、来源和模型/工具版本；
7. 领域事实仍由 `DailyPaper`、`LiteratureResult`、`Evidence`、`ProjectArtifact` 等旧表负责；
8. Web 与 Desktop 能查看同一 Run、批准动作和使用结果；
9. 本地 Zotero/PDF 默认不离开设备，只有 H5 Bridge 的明确高层能力可请求本地动作；
10. 官方服务可以执行 entitlement、预算、并发和成本策略，但管理员看不到用户研究内容；
11. H0–H6 仍不运行实验代码。H7 只有在正式 supersede Decision 9 后才允许开始。

## 2. Sequencing and phase gates

阶段必须顺序通过，允许相邻阶段做只读 spike，但禁止提前 cut over：

| 阶段 | 主目标 | 依赖 | 首个真实业务切片 |
| --- | --- | --- | --- |
| H0 | 契约、显式迁移、测试基座、feature flags | 当前后端基线 | 无 |
| H1 | Durable kernel、API、Run Center、deterministic fake canary | H0 | 内部 fake canary；不是 DSH 或真实模型 |
| H1.5 | 固定 DSH 源码、安全 profile、官方 stdio adapter、Agent canary | H1 code gate | 无业务写入的 Agent execution canary |
| H2 | Discovery 迁移 | H1 operational gate + H1.5 | 文献探索 |
| H3 | Daily system/user 双层工作流 | H2 | 每日论文 |
| H4 | Evidence-aware Project Research | H2、H3 的稳定 kernel | 项目研究 |
| H5 | Selected full-text + Desktop Local Capability Bridge | H1–H4 权限/审批稳定 | 逐篇全文深化与本地 Zotero/PDF 动作 |
| H6 | Cross-workflow composition、Evals、运营、fork/replay 与按指标扩容 | H2–H5 有真实数据 | 全工作流组合/质量/规模化 |
| H7 | 独立实验 sandbox | **正式 supersede Decision 9** | 有界实验执行 |

每阶段状态只能取：

- **Planned**：本文已有计划，尚未进入；
- **In progress**：进入条件全部满足且已有独立工作分支/计划；
- **Shadow**：代码存在但不拥有业务写入权；
- **Canary**：只对明确账户/比例生效；
- **Cut over**：新路径拥有该业务执行权，旧路径仍可回退；
- **Done**：退出门槛、回滚演练、文档和提交全部完成；
- **Blocked**：缺少外部条件或决策，不能用“部分完成”掩盖。

## 3. Cross-phase delivery rules

### 3.1 One authority during migration

Shadow 阶段可以比较结果，但同一副作用只能有一个 writer：

- 不同时让 legacy 与 Harness 抓取同一批 arXiv 数据；
- 不同时让两条路径扣同一笔额度；
- 不同时 publish 同一个 `LiteratureSearch`、`DailyPaper` 或 `ProjectArtifact`；
- Shadow 优先消费 legacy 已捕获的输入/Observation，禁止为了比较而重复调用公共 API 或付费模型；
- 切换 writer 必须由一个枚举 mode 控制，不使用两个可能同时为 true 的布尔开关。

### 3.2 Feature flags

H0 定义 contract，H1 把以下字段实现为 **DB-backed immutable configuration revision**。这些是 canonical
snapshot 字段，不是可在运行时各自覆盖 DB 的独立环境变量：

```text
harness_enabled=0|1
dispatcher_enabled=0|1
canary_enabled=0|1
agent_steps_enabled=0|1
domain_publish_enabled=0|1
fulltext_enabled=0|1

workflow_routes = {
  discovery: {active_version, activation_state, execution_mode=legacy|shadow|harness},
  daily: {active_version, activation_state, execution_mode=legacy|shadow|harness},
  project_research: {active_version, activation_state, execution_mode=legacy|shadow|harness}
}

desktop_bridge_enabled=0|1
experiments_enabled=0|1
```

兼容名称 `PHAROS_HARNESS_ENABLED`、`PHAROS_HARNESS_DISPATCHER_ENABLED`、
`PHAROS_HARNESS_CANARY_ENABLED`、`PHAROS_HARNESS_AGENT_STEPS_ENABLED`、
`PHAROS_HARNESS_DOMAIN_PUBLISH_ENABLED`、`PHAROS_HARNESS_FULLTEXT_ENABLED`、
`PHAROS_DISCOVERY_EXECUTION`、`PHAROS_DAILY_EXECUTION`、`PHAROS_PROJECT_RESEARCH_EXECUTION`、
`PHAROS_HARNESS_DESKTOP_BRIDGE_ENABLED` 与 `PHAROS_HARNESS_EXPERIMENTS_ENABLED` 只在 config head 尚不存在的
新库 bootstrap 时提供 defaults。bootstrap 的默认快照必须是 Harness 全关、三个业务 mode 全为 `legacy`；首次
启用也通过持久 revision/validator，不靠改 env 偷渡。head 已存在后这些 env 不再有路由权，差异只产生脱敏告警。
唯一运行时外部覆盖是 `PHAROS_HARNESS_EMERGENCY_STOP=1`，它只能 deny Harness 新 Run/claim/Run-Step
control-write/publish，不能 enable 能力、改变 activation、自动切回 legacy 或改写 DB；独立鉴权的 operator config
revision endpoint 必须继续可用，才能提交持久回滚。

规则：

- current revision 是 activation、writer mode 与 gates 的唯一真相；完整 snapshot/hash/parent/actor/reason 不可变，
  `harness_config_head` 只能用 expected revision CAS；
- 默认 revision 必须保持旧行为；每个 Workflow route 只有一个 `execution_mode`，未知值/版本/依赖启动失败，
  不能静默 fallback；
- `harness_enabled=0` 是总闸：dispatcher、canary、Agent、publish、full-text、Bridge 全部强制关闭；若任一子开关
  为 1 或任一 execution mode 为 `shadow|harness`，revision validator 必须拒绝，不能静默改值；
- `harness_enabled=1` 但 dispatcher 为 0 时只允许建表、编译定义和只读 API；不能创建会永远排队的
  用户 Run；
- `execution_mode=shadow|harness` 必须同时满足 Harness 与 dispatcher 开启；`harness` 还要求对应的
  domain-publish 开关开启；需要 Agent/full-text/Bridge 的 Step 还分别要求对应子开关；
- Workflow mode 变为 `legacy` 后，已领取 Attempt 按 cancel/pause 策略收尾，未领取 Step 不再 claim；禁止在运行中
  偷换同一个 Run 的执行 owner；
- `harness_enabled=0` 或 emergency stop 生效时 Harness start/Run-Step control/write API 返回明确 unavailable；已存
  Run/Artifact 的 owner-authorized read/export API 继续可用，operator-only config revision endpoint 也继续接受通过
  validator 的回滚快照；旧 API 行为必须完全不变；
- `dispatcher_enabled=0` 只停止认领新 Step，不能删除、伪造完成或改变已有 Run；
- Agent、domain publish 和 full-text 开关分别停止对应高风险能力，不能被 workflow 请求绕过；
- H7 即使 DB revision 或 bootstrap env 误设为 1，只要 Decision 9 未被正式 supersede，validator 与 Policy Engine
  仍必须 deny；
- current revision ID/hash 与 gate 状态可进入 operator health，不得把 secret、用户 query 或 Artifact 内容暴露出去；
- Run 创建固化 `config_revision_id`，但 dispatcher claim、legacy/Harness writer 选择与每次 publication 短事务必须
  重新读取 current head 作为 fencing token；旧 cache/revision 下开始而尚未提交的 writer 不能越过 cutover。

所有 mode/gate/activation 切换必须作为完整、版本化的 DB revision 提交：同一 SQLite `BEGIN IMMEDIATE` 短事务
插入 revision/routes，验证最终快照，再以 `expected_head_revision` CAS 切换唯一 head。回滚 revision 同时把目标
Workflow 切回 `legacy`、按需停用 activation，并关闭没有其他 route 依赖的 gate。禁止逐项改 env/DB 或多次重启
制造中间状态；CAS 失败整笔 rollback。emergency stop 只负责先止血，不能替代这次持久回滚。

### 3.3 Commit discipline

每个下文列出的 commit boundary 都是一个可独立测试、可独立回退的提交。禁止把 migration、kernel、业务
cutover 和 UI 重写塞进一个提交。每次提交：

- 只包含一个阶段内的一个完整意图；
- 包含该意图的测试和必要文档；
- 不顺手格式化无关文件；
- 不提交 `.env`、密钥、真实论文、真实 Zotero 数据库或生产快照；
- 遵守 `AGENTS.md` 的作者与共同作者要求；
- 阶段退出前必须保存全部阶段提交；是否推送服从当次明确授权，且不因提交或推送成功宣称阶段 Done。

### 3.4 Migration discipline

- 每个 migration 有稳定 revision、前置 revision、升级说明和失败恢复说明，但不得自行 commit/换 connection；
  一次启动中的全部待应用 revisions 属于同一个 connection-level `BEGIN IMMEDIATE` 原子批次；
- fresh DB、由已发布历史 schema 确定性生成并提交仓库的 fixture、重复启动都必须测试；fixture 只含结构和合成数据，
  不得从生产库复制用户数据；
- Harness ORM 表必须从现有 `Base.metadata.create_all()` bootstrap 中排除；唯一启动顺序固定为：显式事务建立
  migration ledger → legacy table bootstrap/additive compatibility → versioned migrations 建立 Harness 表 → legacy
  indexes/FTS；Harness schema 不得被 `create_all()` 抢先静默创建；
- SQLite DDL migration 使用连接级显式 `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`，或先以故障注入证明替代机制
  具有相同原子性；普通 `engine.begin()` 不得被未经验证地当成可回滚 DDL；
- migration 不能发起网络调用、模型调用或后台任务；
- 生产迁移前先用数据库副本演练并记录耗时、额外磁盘和锁时间；
- rollback 优先停 dispatcher/切回 legacy，不以紧急 `DROP TABLE` 回退；
- 破坏性迁移只能在旧代码已不读取相关结构、备份恢复演练成功后进入单独阶段；H0–H5 原则上只新增；
- 禁止把未经人工检查的 autogenerate SQL 直接用于生产。

### 3.5 Stage report

每个阶段结束时新增或更新阶段报告，至少包含：

- 实际交付与本文差异；
- migration revisions；
- 测试命令、数量和已知基线失败；
- shadow/canary 指标；
- 安全和隐私检查；
- feature flag 当前值；
- 回滚演练结果；
- 未完成项与进入下一阶段的证据；
- commit SHA 和部署/客户端版本（如有）。

---

## 4. H0 — contracts, migrations and test foundation

### 4.1 Goal

在不改变任何用户行为的前提下，冻结 Harness 的语言、持久化演进方式、测试接口和开关。H0 完成表示
“可以安全开始写 durable kernel”，不表示已有 Agent 或 Workflow 可以使用。

### 4.2 Entry conditions

- [ ] Harness architecture、landscape、workflows 与本计划经过一次交叉审阅；
- [ ] `DECISIONS.md` 中 Decision 9、11、14、15 没有被隐式逆转；
- [ ] 当前 backend 全量测试通过或已记录精确基线；
- [ ] 已识别至少一个已发布 tag/commit 及其可复核 schema contract，H0 能在不读取生产库的前提下确定性生成并
  提交合成旧库 fixture；若历史源码不足以还原 schema，则 H0 Blocked，不能用当前 ORM 猜测；
- [ ] 当前 Daily、Discovery、Projects API 的 contract tests 已成为迁移前基线；
- [ ] 已确认 H0 不连接真实 Zotero 文库、不调用真实 Provider。

### 4.3 Backend deliverables

#### A. Explicit migration runner

- 新建版本化 migration ledger 和 runner；
- 把 Harness models 从 `Base.metadata.create_all()` 排除，并按 §3.4 的唯一启动顺序接入现有 bootstrap；
- 在应用初始化期间只执行已打包、checksum 匹配、按顺序的 migration；
- SQLite migration runner 使用可证明回滚 DDL 的显式事务，并用每条 DDL 后故障注入覆盖半迁移；
- 提供 `status`、`upgrade`、`verify` 的非交互 CLI 或等价运维入口；
- 从已发布历史 schema contract 生成并提交脱敏、只含合成数据的 upgrade fixture 与 generator；
- 失败时停止启动，不允许数据库处于“代码以为已升级”的半状态；
- 首个 revision 只建立 migration ledger；Harness 业务表留到 H1；
- 明确旧 `_add_missing_columns` 的兼容期：H0 不删除它，但新 Harness schema 不使用它演化；
- 为 SQLite 不支持的 ALTER 明确 table-copy/batch migration 规范。

#### B. Contract package

建立 `backend/pharos/harness/` 的最小 package：

- Run/Step/Attempt/Approval 状态 Enum；Step 至少能表达 `skipped` 与 `waiting_for_input`，Attempt 能表达
  外部调用结果不明的 `indeterminate`；
- strict Pydantic base model（`extra="forbid"`）；
- Workflow、Role、Capability、Artifact、Policy、Budget、Usage 的版本化定义；
- canonical JSON 和 SHA-256 规则；
- typed error taxonomy：validation、auth、policy、provider、timeout、budget、cancel、bug；
- 时间、ID、随机数和 sleep 的可注入接口；
- 不实现 dispatcher，不启动后台任务。

#### C. Registry/compiler skeleton

- 代码注册受信 Workflow/Role/Capability；
- 拒绝 DAG cycle、重复 key、未知依赖、无限 fan-out、无版本 prompt/tool/schema；
- 检查角色 capability 是 Workflow allowlist 的子集；
- 检查 retryable side effect 有 idempotency strategy；
- canonical snapshot 的 hash 在进程和测试重启后稳定；
- 注册同 key/version 但不同 hash 时启动失败。

#### D. ModelGateway seam

- 定义 provider-neutral request/result/stream/usage/cancel 接口；
- 建立 deterministic fake model；
- 将现有 AI Chat/Daily provider 安全规则提炼成共享测试，不在 H0 强制切换生产调用；
- 明确 personal BYOK 与 official provider 的 resolver contract；
- 不引入第二套 raw HTTP client。

#### E. Flags and diagnostics

- 定义 versioned `HarnessConfigSnapshot`、`WorkflowRoute`、依赖矩阵、canonical hash 与 CAS request/response contract；
- Settings 中的兼容 env 只生成“head 不存在时”的安全 bootstrap defaults；定义 deny-only
  `PHAROS_HARNESS_EMERGENCY_STOP`，不实现 env 对既有 DB head 的覆盖；
- H0 尚未创建 configuration 表或 dispatcher，但 validator/property tests 必须拒绝双 writer、未知 active version、
  gate 依赖冲突、Decision 9 绕过和 partial snapshot；
- health/operator diagnostics contract 只输出 bootstrap/emergency 状态与未来 revision ID/hash，不输出研究内容；
- 测试证明安全 defaults 下没有 Harness task、表扫描或行为变化。

### 4.4 Frontend deliverables

- 不新增用户可见 Harness UI；
- 为未来 API 预留独立 client/type module，但在没有 OpenAPI contract 前不手写猜测类型；
- 建立或补齐现有 Daily/Discovery/Projects 的最低 contract/build gate；
- 页面在 Harness flags 全关时没有空导航项、占位卡片或“即将推出”假按钮。

### 4.5 Desktop deliverables

- 不新增模块、菜单或后台轮询；
- 将现有 Pharos API transport 的认证、401 清理、timeout 和 source/release origin 行为记录为复用边界；
- Pharos 专项测试继续使用隔离 `-datadir`；
- release build 在 H0 不显示任何 canary workflow。

### 4.6 Documentation deliverables

- 冻结首批 schema/状态词汇表；
- 在 `ARCHITECTURE.md` 和 `ROADMAP.md` 中链接 Harness 文档但明确 Planned；
- 在 `DECISIONS.md` 新增“Research Harness 是显式持久工作流，不是自由 Agent 群聊”的决策；
- 写 migration 运维手册和 schema fixture 生成规则；
- 把生产数据库副本演练、备份与恢复证据列为 operator operational gate；没有这份外部证据时只能报告
  code-complete，不能宣称生产 migration gate 已通过；
- 写 provider、secret、raw CoT 和 prompt snapshot 的 retention/redaction 规则。

### 4.7 Data and migration verification

- fresh DB：从空目录启动，migration ledger 正确；
- upgrade DB：从当前发布版 schema 升级，所有旧表行数、外键和索引保持；
- repeat：连续启动三次无重复 revision/DDL；
- checksum mismatch：修改历史 migration 后启动失败；
- interrupted migration：每个 DDL 故障注入点都必须全 rollback，既没有新 revision，也没有部分 DDL；若当前
  driver/启动顺序无法证明这一点，H0 直接 Blocked，不能以“标记需人工恢复”通过 Gate；
- fixture backup restore：恢复合成旧库后，旧版本服务能在 Harness flags 关闭时启动；
- production-copy restore：由 operator 在隔离副本执行并保存证据，实施 Agent 不访问生产数据。

### 4.8 Tests

- Registry property tests 与 hash golden tests；
- 状态 Enum 序列化测试；
- strict schema 拒绝未知字段、超长内容和无 version；
- fake clock/model/tool 完全无网络；
- secret scrubber 测试；
- feature flag matrix；
- 旧 backend 全量回归；
- frontend typecheck/build；
- desktop Pharos 专项基线，无 Harness 行为变化。

### 4.9 Rollout and rollback

- H0 可随普通 backend image 发布，但 flags 必须关闭；
- 部署前运行 migration verify，部署后检查旧 API 与生产保护服务；
- 若初始化失败：停止新 image，恢复数据库备份或修复未完成 revision，再回旧 image；
- 若只是 Harness package 缺陷：关闭 flags 即可，不能删除 migration ledger；
- H0 不创建用户 Run，因此没有 Run 数据回迁问题。

### 4.10 Code gate and operational exit gate

通过下列代码/fixture 条目后可报告 `H0_CODE_COMPLETE` 并继续在本地实现 H1，但不得部署 migration。H0 只有在
获授权 operator 完成生产副本恢复演练后才可标为 Done。

- [ ] 所有 H0 deliverables 和测试通过；
- [ ] legacy 行为/响应 contract 无变化；
- [ ] migration fresh/upgrade/repeat/checksum/interruption/fixture-restore 六类代码测试通过；
- [ ] fake-backed registry 能注册合法 canary definition，非法 definition 全部失败；
- [ ] 没有网络、Agent、dispatcher 或用户可见入口；
- [ ] 文档状态仍准确写为 Planned；
- [ ] 本地/合成 fixture 回滚演练有记录；
- [ ] **Operational gate:** operator 在隔离生产副本上完成 verify/upgrade/backup/restore，记录锁时间、额外磁盘、
  回退结果且未触碰真实 Zotero/其他保护资产。

### 4.11 Non-goals

- 不创建 Harness Run/Step 表；
- 不实现 Agent loop；
- 不迁移 Translation、Daily、Discovery 或 Project；
- 不引入 Redis、Celery、Temporal、LangGraph、MCP 或动态插件；
- 不修改 Zotero schema 或创建本地 sidecar writer。

### 4.12 Commit boundaries

1. `Introduce explicit backend schema revisions`；
2. `Define versioned Harness contracts and canonical hashes`；
3. `Compile trusted workflow definitions against bounded policies`；
4. `Add deterministic Harness test doubles`；
5. `Gate the dormant Harness surface behind explicit settings`；
6. `Document the Harness contract and migration operating procedure`。

---

## 5. H1 — durable kernel and canary

### 5.1 Goal

实现不依赖任何真实业务的 durable execution kernel，并用内部 deterministic fake canary 证明：可创建、认领、重试、暂停、
取消、审批、重启恢复、事件重放和计量。H1 不迁移 Daily/Discovery/Projects。

H1 本身不启动 DeepSeek Harness sidecar。H1 code gate 后可以并行实现和测试 H1.5 的非生产 adapter，
但 H1 operational gate 与 H1.5 code/security gate 都完成前，不得为业务 route 或生产账户启用它。

### 5.2 Entry conditions

- [ ] 本地实现要求 H0 code gate 通过；进入 staging/production 前 H0 必须标为 Done；
- [ ] migration runner 已在本地和部署副本上演练；
- [ ] architecture acceptance checklist 已映射为测试清单；
- [ ] canary Workflow 的输入、输出、故障模式和权限完全确定性；
- [ ] production-like SQLite/CPU/memory fixture 可用。

### 5.3 Backend deliverables

#### A. Persistent schema

用独立 revisions 新增：

- `harness_workflow_versions`；
- `harness_config_revisions` / `harness_config_workflow_routes` / singleton `harness_config_head`；
- `harness_runs`；
- `harness_steps`；
- `harness_attempts`；
- `harness_events`；
- `harness_artifacts` / `harness_artifact_links`；
- `harness_public_artifact_releases` / `harness_public_artifact_projections`；
- `harness_approvals`；
- `harness_schedules`；
- `harness_usage_events`；
- 若 publication contract 需要，新增 owner-scoped domain mapping 表，不给旧领域表强塞 Harness 状态。

所有 owner、unique、check、FK、cascade/SET NULL 和 queue indexes 在 fresh schema 与 upgrade schema 一致。
其中必须冻结以下结构契约：

- `HarnessRun.idempotency_key` 为 `NOT NULL`；客户端未提供时服务端生成 request UUID，它只保证本次创建，
  schedule/publication 则必须派生稳定 key；
- `HarnessRun.config_revision_id` 为 `NOT NULL`，指向创建它的 immutable config revision；
- config revision 保存完整 canonical snapshot/hash/parent/actor/reason；route row 保存 active definition version、
  `active|deprecated|disabled` 与 `legacy|shadow|harness`；只有 `disabled + legacy` 可缺省/使用 NULL version，其他
  组合必须以 composite FK 约束 definition；没有 legacy domain writer 的 allowlisted internal/canary Workflow 才可
  使用 NULL execution mode；head 只有一行且只能 expected-revision CAS；
- `HarnessStep` 使用 `(run_id, definition_step_key, instance_key)` 唯一约束，singleton 使用非 NULL sentinel；
  mapped instance key 来自稳定 item key/hash，展开与聚合均稳定排序；
- mapped fan-in 显式声明 `all_success|all_terminal|min_success|allow_partial` 之一及聚合输入规则；选择
  `min_success` 时 `min_success_count` 是必填正整数，其他 policy 必须为空；
- user-controlled/visible child 表以 `(parent_id, scope_type, scope_id)` 复合外键约束同 scope；migration ledger、
  workflow definition/config revision/head 等 global row 为 system scope，不经用户 API 暴露；
- system → user 只允许经 `harness_public_artifact_releases/projections`：源必须是 allowlisted public system
  Artifact，目标必须是 user-scoped projection Artifact，普通 Artifact Link 仍拒绝跨 scope；
- public release 先生成 immutable `release_id`，再按
  `SHA256(canonical_json({release_id, source_schema_name, source_schema_version, source_content_sha256,
  public_manifest_sha256, release_policy_version}))` 冻结 `release_sha256`；同内容 reissue 也必须使用新 ID/hash，
  不得命中已撤销 release 的 downstream idempotency key；
- projection schema 必须显式包含脱敏 `outcome/coverage/public typed source errors`、evidence level 和必要
  provenance ID/hash；复制其内容的 user Artifact 以同 owner `derived_from` link 建 lineage。Release revoke 必须由
  幂等 job 阻止新 projection，并沿 lineage tombstone projection/非领域派生 payload，同时保留审计 ID/hash/receipt；
- Event cursor 使用永不复用的单调整数（SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` 或经测试等价实现）；
- lease/heartbeat 时间使用 UTC integer epoch 或强制归一化类型，禁止依赖 SQLite 读回的 naive datetime。

#### B. State and repository layer

- `HarnessStateService` 是唯一状态转换入口；
- owner-scoped repositories 的所有 read/write 都要求 scope；
- `HarnessConfigService` 是 activation/writer mode/gate 的唯一更新入口：在单个 `BEGIN IMMEDIATE` 短事务中写完整
  immutable revision/routes，并以 expected revision CAS `harness_config_head`；不存在逐 gate/route 的直接 update；
- Run 创建使用 idempotency key；
- Artifact immutable，修订只能新建并 link；
- Attempt 的身份、输入、策略和历史不可覆盖；活跃 Attempt 只允许通过状态机/CAS 更新 heartbeat、lease、结束时间、
  typed outcome，进入 terminal/abandoned/indeterminate 后冻结，retry 永远新建 Attempt；
- 缺密钥、额度或用户配置进入持久 `waiting_for_input(reason, resume_contract, expires_at)`；用户修复后显式 resume，
  超时按工作流规则 `skipped|partial|failed`，不得永久伪装 running 或滥用 approval；
- condition=false/approval reject/optional branch 使用 `skipped`；Step failure propagation 与 Run reduction（terminal、
  partial、failed、cancelled）由 Workflow/fan-in policy 确定，不允许 UI 或 worker 临时猜测；
- Workflow definition/schema/prompt/tool/hash 永久不可变；`active|deprecated|disabled`、active version、writer mode 与
  gates 只存在于 current config revision，不能另建可变 activation/env authority；
- Run start 保存 config revision；claim 和 publication transaction 必须用 current head fencing。head 已变化且新
  revision 不再授权该 Workflow/writer 时返回 typed stale-config 并停止，不允许进程 cache 继续写；
- Event append 与状态改变处于同一短事务；
- Usage reserve/settle/release append-only，可重建聚合；
- API 不返回内部 stack、prompt、secret 或完整私有输入。

#### C. Dispatcher, lease and reaper

- 原子 claim 一个 due Step，两个 worker 不能同时成功；claim 同一短事务读取 current config head 并验证
  Harness/dispatcher/Workflow activation fence，stale revision 不能认领；
- claim 使用单条 conditional `UPDATE` 或显式短写事务；双 worker 测试必须使用临时文件 SQLite 与独立连接，
  不能用 `:memory:`；
- claim 后立即释放事务，再执行工具/模型；
- heartbeat 间隔小于 lease 的三分之一；`busy_timeout` 小于 lease 且有容量测试；同步 SQLAlchemy 工作放到
  `asyncio.to_thread`，不得阻塞 dispatcher event loop；
- heartbeat、lease expiry、abandoned/indeterminate Attempt 与有限 retry；对没有 provider idempotency/query 能力的
  外部调用，崩溃窗口标 `indeterminate`，不得自动再次付费；
- startup reaper 和周期 reaper；
- persistent pause/cancel；
- shutdown 停止 claim，给可取消任务 grace period，未完成 Attempt 可被下一进程正确回收；
- 建立进程级 weighted workload admission，统一考虑 Harness、Daily 与 BabelDOC 翻译；默认 Harness 并发不超过 2，
  但必须为翻译保留测得的 CPU/RSS 余量，不能让三套独立 semaphore 同时吃满 1800 MB。

#### D. Event projection and API

实现 architecture §12 的 H1 endpoints，`fork` 暂不开放：

- start/list/detail/pause/resume/cancel；
- cursor REST events 与 fetch-based SSE；
- artifact list/detail；
- approval list/decision；
- schedule API 只对 allowlisted Workflow；
- workflows capability endpoint；
- operator-only config status/validate/apply 接口（CLI 或 admin route）必须提交完整 snapshot +
  `expected_head_revision`，返回新 revision/hash；普通用户不能修改 activation/mode/gate；
- 每个 ID 越权与不存在统一 404。

Event list/SSE 都有 `limit`、`next_cursor`、payload 上限和 retention floor。SSE 先用短 Session 完成鉴权并关闭，
再周期性从 DB cursor tail；进程内 bounded buffer 只能提速，不能成为事实源。慢消费者得到含 durable cursor 与
retention floor 的 `resync_required`，通过 DB cursor 补齐；限制每 owner/进程连接数。heartbeat 不持久化。

#### E. Canary Workflow

`harness.canary@1` 至少提供受控输入模式：

- immediate success；
- deterministic retryable failure 后成功；
- terminal validation failure；
- waiting approval → approve/reject/expire；
- long step 可 pause/cancel；
- child/mapped step 有固定 fan-out；
- artifact publication 为无副作用 canary record；
- usage reserve/settle/release。

只有 operator/test account 且 canary flag 开启时可启动。当前 canary 使用 deterministic fake model/tool；
它不启动 DSH，不调用真实模型或真实网络，也不写业务领域表。

#### F. ModelGateway minimum implementation

- AgentRunner 可以通过 fake model 完成 typed turn；
- 官方/个人 Provider resolver 可读取现有安全配置，但 canary 默认不花真钱；
- max turns/tools/tokens/time/cost 强制执行；
- schema repair 最多一次；
- Provider 实际身份和 prompt/schema/tool version 固化在 Attempt；
- 论文/网页内容不能改变 tool catalog。

#### G. DeepSeek Harness integration boundary

- H1 只登记并校验 vendor commit/license/package hash 与协议草案，不把 DSH source 直接挂入 API 进程；
- 预留 Node stdio JSON-RPC sidecar seam，但不在 H1 claim、lease、usage、approval 或 publication 之外建立第二控制面；
- Session 只作为单个 Agent Attempt 的内部执行日志，Pharos DB 仍是 Run/Step/Attempt/Event/Artifact/Approval/Usage 真相；
- shell、terminal、subprocess、sandbox、E2B、code-runtime、general filesystem、非批准 provider network、MCP、动态 plugin、
  self-modification 和 DSH workflow 在初始集成中 deny；
- fake-model canary、协议 negative tests、资源/隐私/回滚证据未完成前，不接真实 DSH 或真实 provider。

详见 [`DEEPSEEK_HARNESS_INTEGRATION.md`](DEEPSEEK_HARNESS_INTEGRATION.md)。

### 5.4 Frontend deliverables

实现最小但真实的 Run Center：

- Run list、状态筛选和详情；
- Step timeline、Attempt 次数、公开错误和 partial outcome；
- Artifact 摘要和安全的内容预览；
- usage 与预算；
- approval approve/reject/reason；
- pause/resume/cancel；
- SSE 断线后 cursor 重连，SSE 不可用时退回 polling；
- canary 入口只在 operator flag 下显示。

不得显示 raw prompt、CoT、API key、内部路径或用户无权读取的父/子 Run。

### 5.5 Desktop deliverables

- H1 不增加普通用户可见模块；
- 为后续工作流实现 owner-authenticated run/status/event polling client；
- release channel 不暴露 canary start；
- source/test build 可用隐藏测试入口验证 token、重启和 401 行为；
- 桌面关闭/重开后按 run ID 恢复，不把窗口状态当执行状态。

### 5.6 Documentation deliverables

- Run/Step/Attempt 状态转换表与错误码目录；
- migration revisions 和恢复手册；
- API OpenAPI 示例、SSE reconnect 示例；
- operator queue/reaper/flag runbook；
- canary 故障注入手册；
- DeepSeek Harness 固定快照、所有权、安全 allowlist/denylist、隐私和回滚文档，以及已生效的 safe-profile
  policy/effective-config 证据；official-wire runtime adapter 仍未接入；
- `pharos/*` 扩展协议仅保留为未来 draft，不得描述成当前上游或当前实现的能力；
- 更新 `ROADMAP.md`：kernel 已实现不等于三个业务 workflow 已迁移。

### 5.7 Tests and fault injection

- 每条合法/非法状态转换穷举；
- 两 worker claim race 重复运行至少 100 次；
- claim 后、tool 前、外部请求发出后但结果未知、tool 后、Artifact commit 前、publication commit 后逐点 crash；
- lease expiry 与新进程 recovery；
- idempotency key 并发重复提交；
- pause/cancel/approval 竞态；
- approval resource/version/expiry 不匹配拒绝；
- config revision canonical hash、非法依赖/unknown version、双 operator CAS 竞争、整笔 rollback；
- head 切换与 stale dispatcher/legacy request/Harness publisher 竞态，切换后旧 fence 的领域写入为 0；
- head 存在后 bootstrap env 不覆盖 DB；emergency stop 只 deny Harness 且 read/export 仍可用；
- owner scope 覆盖每个 user-controlled/visible row、repository 和 endpoint；global/system row 不由用户 API 暴露；
- Event cursor 重放、SSE reconnect、慢消费者和 backpressure；
- Usage reserve/settle/release 守恒；
- secret、stack、prompt injection 与超大 payload；
- migration from H0 checked-in historical fixture；
- 10,000 个 fake Step 的稳定性 soak，不要求同时驻留内存；
- 当前生产限制下 RSS、SQLite lock wait、queue age 和 API latency 记录。

### 5.8 Shadow/canary/cutover

1. **Dormant**：schema/API 合并，dispatcher 关闭；验证只读 list 和 migration；
2. **Local canary**：fake clock/tool，执行全故障矩阵；
3. **Staging canary**：dispatcher 开启，连续重启、kill 和 SSE 断线；
4. **Production operator canary**：只允许管理员账户，禁止真实模型；
5. **Soak**：至少 72 小时没有永久 leased/running Step、重复 Artifact 或 usage 不守恒；
6. H1 无业务 cutover，旧功能全部仍走 legacy。

### 5.9 Rollback

- 首选以一个 DB config revision 原子关闭 canary/dispatcher（仅在没有其他 route 依赖时）并停用 canary activation，
  保留表与 Run 供只读诊断；若 DB 配置通道故障，先设 deny-only emergency stop 止血，再修复并提交持久 revision；
- API 可保持只读，不删除用户/运维证据；
- 回旧 image 前确认旧代码会忽略新表；
- 运行中的可重试 canary 留待恢复或由 operator cancel；
- 不使用删除 Harness 表作为事故响应。

### 5.10 Code gate and operational exit gate

本地实现 Agent 能完成前两级 canary、全部代码/fixture gate，并报告
`H1_CODE_COMPLETE_AWAITING_CANARY`。Production operator canary、72 小时 soak 与生产副本恢复必须由获授权的
operator 执行；未取得其证据时不得把 H1 标为 Done。

- [ ] architecture §20 checklist 全部满足 kernel 部分；
- [ ] 重启/双 worker/副作用边界故障注入全部通过；
- [ ] **Operational gate:** 72 小时 production operator canary 无孤儿 Step、重复领域 publish 或持续写锁；
- [ ] Run Center 可以完成审批、取消和断线恢复；
- [ ] 管理员指标不含研究内容；
- [ ] config revision/head CAS、stale writer fencing、bootstrap env 与 emergency stop 测试全部通过；
- [ ] DB revision 关闭 Harness 时旧 API 与 H0 基线一致；
- [ ] 资源测量在 1800 MB / 2 CPU 部署预算内且保留翻译余量；
- [ ] rollback 演练成功。
- [ ] **Operational gate:** 生产数据库副本恢复、资源余量与保护资产检查有 operator 证据。

### 5.11 Non-goals

- 不迁移真实业务；
- 不做任意 Workflow 编辑器；
- 不开放普通用户启动 canary；
- 不支持 fork/replay UI；
- 不建立长期 Agent memory；
- 不把 worker 拆成第二台机器。

### 5.12 Commit boundaries

1. `Create the durable Harness schema`；
2. `Enforce Harness state transitions and owner scope`；
3. `Claim steps with leases and recover abandoned attempts`；
4. `Persist replayable Harness events and immutable artifacts`；
5. `Enforce approvals, budgets and usage accounting`；
6. `Expose the owner-scoped Harness API`；
7. `Exercise the kernel with a deterministic canary workflow`；
8. `Add the web Run Center and resilient event client`；
9. `Add the dormant desktop Harness transport`；
10. `Document and fault-test Harness operations`。

---

## 5A. H1.5 — DeepSeek Agent execution adapter

> 状态：**In progress，非生产。** 固定源码与许可证已进入仓库，上游全量 build 及 SDK 聚焦测试已在
> 隔离环境通过；安全 profile 的 code gate 已通过，Pharos adapter、真实 DSH canary、部署资源证据和
> 回滚演练尚未完成。

### 5A.1 Goal

在不改变 Pharos durable 控制真相的前提下，把 DSH 接成 `agent` Step 的受限执行内核。首个纵切只运行
内部 canary，不接业务领域表：Pharos claim Attempt、reserve usage、启动受限 sidecar、通过官方 stdio
协议完成一个 turn、校验 typed output、写 immutable Artifact、settle usage、关闭并 reap sidecar，最后由
Pharos reducer 决定 Run 终态。

### 5A.2 Entry conditions

- [x] H1 code gate 已通过，Run/Step/Attempt/Event/Artifact/Usage 状态与 owner scope 已存在；
- [x] 上游来源、commit、版本、MIT/third-party notices 和同步脚本已固定；
- [x] 上游源码可在无用户 HOME/无 API key 环境完成 build，SDK protocol/client/server 测试通过；
- [x] `harness-runtime/` no-tool overlay、机器可读 deny policy、实际组合配置审计与无模型 shutdown smoke 已由 CI 固化；
- [ ] H1.5 只在开发/CI 与 operator fake canary 中运行；H1 operational gate 未完成时生产 route 保持关闭；
- [ ] `DEEPSEEK_HARNESS_INTEGRATION.md` 的所有权、隐私、denylist 和回滚条款已由实现测试固化。

### 5A.3 Official wire first

固定上游版本的公开请求只有 `initialize`、`session/prompt`、`shutdown`，通知只有 `session.event`、
`session.status`、`subagent.started` 和 `subagent.finished`。第一版必须复用这组官方协议：Attempt/Role、
Context Pack、预算、deadline、frame 上限、schema 与 provenance 由 Pharos 父进程绑定和验证；初始 profile
禁用 subagent，所以收到任何 subagent notification 都是协议/安全错误。

文档中的 `pharos/*` 方法是未来可能需要的协议扩展，不属于上游 `0.1.2-alpha.1`，也不是 H1.5 首个
纵切的前置条件。只有官方 wire 无法表达经过测试的恢复或 typed capability 需求时，才新增薄插件和独立
protocol version；不得先发明一套方法再把它描述成已经落地的 DSH 能力。

### 5A.4 Deliverables

#### A. Source and build boundary

- `vendor/deepseek-harness/` 保持上游快照，不在其中混入 Pharos patch；同步脚本按完整 commit fetch；
- CI 校验无嵌套 `.git`、许可证/notices、manifest/version/revision 一致、frozen lock install、build 和 SDK tests；
- 生产产物由 CI 构建 immutable runtime closure/binary，服务器不运行 pnpm install/build，不从运行时网络装 plugin；
- Attempt provenance 记录 upstream commit、runtime/package hash、profile/policy hash 和 wire protocol version。

#### B. Safe runtime profile

- profile 位于 `harness-runtime/`，不使用上游 `sdk-minimal` 的默认 danger-full-access 组合；
- telemetry、shell/terminal/subprocess/PTY、sandbox/workspace、FS/search/editor、skills、agent instructions、
  web/MCP、workflow、subagent、goal/todo/ralph、自修改和动态 plugin 全部显式 disabled；
- 初始 model-facing tool catalog 必须为空；唯一网络例外是固定 provider adapter 到后端批准 endpoint 的
  模型请求，不能提供给模型作为 web/network tool；
- `$DSH_HOME`、cwd、session、attachment 与临时目录均为 Attempt-scoped 私有目录；环境变量采用显式 allowlist，
  不继承用户 HOME、项目 `.env`、SSH、云 metadata 或其他服务凭据；
- policy checker 对上游新增危险 row fail closed，升级 commit 时必须人工分类。
- 当前 overlay 禁用内建 provider adapter、retry、settings 与 credentials；但官方 SDK server 在父进程错误地
  选择 `deepseek-official` 时仍会动态挂载 fallback，因此 provider allowlist 与 OS/container egress 必须由
  Pharos 父进程和部署层独立强制，profile 不能被描述成独立 sandbox。

#### C. Parent process adapter

- 使用严格类型的 `DshInitialize`、`DshPrompt`、`DshNotification`、`DshOutcome`，未知字段/方法/状态拒绝；
- stdout 只能是有界 newline JSON-RPC；stderr 单独限长并脱敏；frame、buffer、event、session、output 都有上限；
- 每个 active Attempt 最多一个 child；启动、initialize、turn、idle、shutdown、TERM、KILL、reap 都有独立 deadline；
- session ID 与 Attempt 显式映射；只接受本 Attempt 的 notification，迟到/重复/跨 session 事件不能改终态；
- 官方 wire 没有 per-session cancel；Pharos 的 `cancel` 必须是 Attempt-scoped 本地 handle，通过有界
  shutdown/TERM/KILL/reap 收尾，不能继续使用会取消全局 FakeModel 的无参共享语义；
- provider 请求是否送达未知时映射 `indeterminate`，不能当普通 timeout 自动重试；启动前失败才可按 policy 重试。

#### D. Agent request, output and Artifact

- Role Definition 冻结 prompt template/version、input/output schema、model profile、turn/token/tool/wall budget；
- 父进程只把已做 owner/sensitivity 检查的 Context Pack 传入；不把任意 Run input 直接拼成 system 指令；
- 首版一次 `session/prompt`、零 tool；assistant final response 必须解析并验证为 Role output schema；
- 校验成功后先写 immutable Artifact（content hash、input lineage、role/prompt/model/runtime/profile provenance），
  再绑定 `Step.output_artifact_id` 并完成 Attempt/Step；校验失败不产生可发布 Artifact；
- 原始 Session chunk/raw CoT 不复制进 `HarnessEvent`；Event 只保存 lifecycle、cursor/hash、usage 与 Artifact ref。

#### E. Usage and process lifecycle

- 调用前按剩余 budget reserve；DSH/provider 返回的 input/output tokens 与 request metadata由 Pharos settle；
- 未知送达保留 reservation/reconciliation 事实，不虚构 0 成本；重复 notification/close 不得重复 settle；
- 父进程或 API 重启后扫描 active Attempt 与 child pid/session provenance，按证据 resume、abandon 或
  `indeterminate`；不得静默创建第二个模型请求；
- 初始并发为 1，Node `--max-old-space-size`、RSS/CPU/stdio/session bytes 和启动耗时纳入 admission/metrics；
- 正常、异常、cancel、timeout 和测试失败路径都必须证明无孤儿进程。

### 5A.5 Tests and fault injection

- 官方 protocol golden：initialize/prompt/event/status/shutdown，未知/重复/错 session/过大/破碎 JSON；
- fake runtime：成功、结构错误、stderr 污染、stdout 非 JSON、hang、提前退出、请求前 crash、可能送达后 crash；
- 真实 DSH + deterministic fake adapter：Agent loop/Session event/Artifact/usage 全链路，不访问网络/key；
- safe-profile negative tests：危险 row、新增危险关键词 row、telemetry、subagent notification、工具目录非空即失败；
- owner、Context Pack sensitivity、secret scrub、Session tombstone 和 admin privacy；
- cancel/TERM/KILL/reap、父进程重启、重复 event、usage conservation、late message 不覆盖终态；
- Linux amd64 生产形态 runtime smoke，RSS 峰值与启动时间在预算内；真实 provider 测试单独 opt-in，不进默认 CI。

### 5A.6 Activation and rollback

接入 adapter 前必须新增独立 `agent_runtime_enabled`/runtime route gate，默认关闭；当前 schema 尚无该 gate，
不得用现有 `agent_steps_enabled` 偷渡生产启用。启用顺序固定为：协议 fake → 真实 DSH
fake adapter → operator 单账户/单并发 → 受控真实 provider canary。任何安全 profile hash、runtime hash、
protocol、orphan、usage conservation、RSS 或 `indeterminate` 指标越界，立即用 DB config revision 停止新 claim；
deny-only emergency stop 只作配置通道失效时的最后手段。回滚保留 Run/Event/Artifact/Usage，不删除 schema，
也不把 DSH Session 恢复成业务真相。

### 5A.7 Exit gate

- [ ] 官方 wire adapter 与 safe profile 均由机器可读 policy 和 negative tests 固化；
- [ ] 真实 DSH fake-model canary 从 claim 到 immutable Artifact/usage/run reduction 完整通过；
- [ ] crash/cancel/restart/unknown-delivery/duplicate-event 全部有可重复测试且无孤儿进程；
- [ ] runtime build 可复现，Linux amd64 镜像 smoke、SBOM/license、hash provenance 通过；
- [ ] 生产默认关闭，operator canary/停止条件/回滚 runbook 完成；
- [ ] H1 operational gate 与 H1.5 gate 同时通过后，H2 才能把 Discovery Agent Step 指向 DSH。

### 5A.8 Commit boundaries

1. [x] `Vendor and continuously verify the pinned DSH source`；
2. [x] `Freeze the no-tool Pharos runtime profile`；
3. `Add the strict official-wire stdio client and fake runtime`；
4. `Persist Agent runtime provenance without changing business routes`；
5. `Write validated Agent output as immutable Harness artifacts`；
6. `Run the canary through a real DSH process and deterministic adapter`；
7. `Package the bounded runtime into the production image`；
8. `Exercise operator canary, resource ceiling and rollback`。

### 5A.9 Non-goals

- 不在此阶段接 Daily、Discovery、Project 的领域 publication；
- 不开放 shell、FS、web、MCP、subagent、workflow、实验代码或 Desktop Local Bridge；
- 不在服务器构建 upstream、不开放公网 sidecar、不允许用户安装 profile/plugin/patch；
- 不把 Session UI、DSH Web/TUI 或 upstream workflow editor嵌入 Pharos；
- 不因一次真实模型响应成功就宣布 Agent backend production-ready。

## 6. H2 — Literature Discovery vertical slice

### 6.1 Goal

把当前同步 Discovery 迁移为第一个真实 Harness Workflow：快速返回 run ID，多来源检索与分析可恢复，结果仍
publish 到现有 `LiteratureSearch` / `LiteratureResult`，并继续严格标注摘要级证据。

### 6.2 Entry conditions

- [ ] H1 Done 且 canary 稳定；
- [ ] `HARNESS_WORKFLOWS.md` 的 Discovery schemas、fan-out、预算和 publish contract 已冻结；
- [ ] 当前 arXiv/OpenAlex adapter、dedup 和 partial/error golden fixtures 齐全；
- [ ] legacy `POST /api/discovery/search` contract 已记录；
- [ ] shadow 比较不重复调用外部 Provider 的方案已验证。

### 6.3 Workflow deliverables

建议固定步骤：

```text
validate_brief
→ plan_queries (bounded agent; 可按策略跳过)
→ search_sources[] (deterministic adapters)
→ normalize_and_deduplicate
→ rank_baseline
→ build_rule_cards
→ read_abstract_cards[] (optional bounded agents)
→ skeptical_critic
→ synthesize_landscape
→ publish_search (idempotent)
```

要求：

- query planner 只能产出有限 `QueryPlan`，不能指定任意 URL/tool；
- provider adapter 保留 independent partial failure；
- normalize/dedup/rule extraction 继续是确定性代码；
- `paper.rule_card@1` 只保存确定性抽取的原始摘要句和“待 AI 分析”状态，不向 `_zh` 字段塞英文；
- 只有通过 Agent + validator 的 `paper.trick_card@1` 才能显示中文核心 Trick/摘要；论文标题始终保留原文；
- 只有书目信息时标记 `metadata_only`；实际读取原始摘要后才标记 `abstract_only`；未读取 PDF 不代表
  可以把缺失摘要的条目升级为 `abstract_only`；
- Critic 可指出证据不足，不能把摘要推断成实验事实；
- publish 使用 stable idempotency key，重试返回同一个领域对象；
- 旧 `LiteratureSearch` 是发布后的业务权威，Harness Artifact 是来源与过程记录。

### 6.4 Backend deliverables

- Discovery Capability adapters 复用现有 HTTP、安全、规范化和 dedup service；
- 新增 legacy Observation capture seam；shadow 只能消费这一份已经抓取的 normalized batch，不能把“复用现有
  服务”误写成重复执行现有 request-bound 流程；
- 从当前 request-bound `run_search` 中抽出短事务 `publish_search_result(...)` seam；慢网络/模型 I/O 必须在事务外，
  publication mapping unique 负责重复 crash 的领域幂等，不能声称直接复用一个尚不存在的 publication service；
- 每个 Provider Observation 单独 Artifact，错误 typed 且脱敏；
- query/card/critic/synthesis strict schemas；
- workflow 预算限制 query 数、来源数、结果数、Agent 卡片数、tokens、wall time；
- personal BYOK/official model 使用 ModelGateway，Attempt 记录实际 provider/model；
- domain publication mapping 可从 Run/Artifact 找回 `LiteratureSearch`；
- 新 Harness start path 返回 `202`；
- legacy API 在 mode=legacy 时完全不变；
- mode=shadow 不做第二次外部搜索和第二次付费模型调用；
- `mode=harness` 时旧同步 route 是临时 compatibility facade：只创建同一个 Harness Run，绝不再调用 legacy
  provider/writer；它在已冻结的旧超时内等待 durable publication 并返回原 `LiteratureSearch` contract。若超时，
  返回旧客户端可识别的“请升级客户端/稍后重试”typed error，不启动第二条执行；最低支持 Desktop 版本发布并
  达到升级窗口前不得退休 facade。
- 旧 `/results/{id}/analyze` 在 `mode=harness` 进入受追踪的 single-paper analysis Run；每次 publication 保存
  before/after domain revision/hash 与 superseding Artifact link，同步 facade 只等待这一 Run，不能无 lineage 覆盖字段。
- `L-120` 对用户主动发起的搜索只物化 owner-scoped `LiteratureSearch/LiteratureResult`，不等于 promotion；保存到
  Web 文库、Desktop Zotero 或加入 `ProjectSource` 分别创建绑定 target/hash/version 的 approval 和独立 receipt；
  `ProjectSource` publication 必须先确保有 owner `LiteratureResult`，输入中的 `project_id` 不能充当批准。

### 6.5 Frontend deliverables

- Discovery 提交后立即进入 Run 详情，不阻塞等待搜索；
- 显示来源进度、partial errors、查询计划和可取消状态；
- 结果卡保持简洁：英文标题、中文核心 Trick、来源/年份/证据级别；
- 展开后才显示方法、结果、局限与 Agent provenance；
- 可从结果回到搜索 Run，也可从 Run 打开已发布搜索；
- 支持选择结果申请进入 Web 文库或项目，UI 先预览目标与副作用、逐项处理 approval；通过后才由当前
  owner-scoped domain service 完成，拒绝/过期不会改领域表；
- legacy/harness 响应由 adapter 归一，切换期间不维护两套视觉逻辑。

### 6.6 Desktop deliverables

- Desktop Discovery 使用相同 start/status/result contract；
- 关闭工具窗口或重启应用后可恢复正在运行的 run；
- polling 有退避，终态停止；支持取消；
- 卡片与 Web 同样标注 `abstract_only`、中文核心 Trick 和来源错误；
- 不下载 PDF、不读取本地 Zotero、不新增 Bridge 权限。

### 6.7 Data migration

- 不回填历史搜索为伪 Harness Run；历史仍通过旧表读取；
- 新 Run publish 到旧表时建立明确 mapping；
- 不修改已有 `analysis_mode` 的含义；
- migration 只新增必要 mapping/index，不重写历史结果；
- 相同 query 的新执行是新 Run，只有客户端重试使用相同 idempotency key 才复用旧 Run。

### 6.8 Tests and quality evaluation

- 单源成功、双源成功、单源 partial、全源 error；
- 重复 DOI、规范标题、版本 arXiv ID 的 dedup golden；
- stable round-robin/rank；
- planner 输出 cycle/超 fan-out/未知来源被拒绝；
- 模型卡 schema、中文比例、核心 Trick 长度和 unsupported claim；
- prompt injection in title/abstract 不改变工具；
- crash after one Provider，不重跑已完成 sibling；
- provider 已可能计费但响应未持久化时进入 `indeterminate`；只有 provider 明确支持 idempotency/query 才自动重试，
  否则再次调用需用户/策略重新确认预算并提示可能已发生 vendor charge；
- crash after publish，不重复 `LiteratureSearch/Result`；
- owner、project scope 和 404；
- `project_id` 不会自动 promotion；Web 文库与 ProjectSource approval/resource hash 不可互用，重复 grant/receipt
  不重复领域行；
- legacy vs shadow normalized parity；
- 由人工标注的小型 Discovery eval set：相关性、核心 Trick 准确性、遗漏、虚构率；
- 明确质量阈值写进阶段报告，不以“模型看起来不错”验收。

### 6.9 Shadow/canary/cutover

1. **Shadow replay**：legacy 拥有外部搜索；Harness 消费捕获后的 provider batch，从 normalize 开始运行；
2. **Comparison**：比较结果集合、dedup、顺序、规则字段和 error semantics；不 publish 第二份；
3. **Internal canary**：指定账户由 Harness 拥有真实搜索，legacy 只读比较；
4. **Opt-in beta**：小比例账户，记录成功率、partial、成本、取消和保存率；
5. **Client cutover**：Web 先切，Desktop 在同一 backend contract 稳定后切；
6. **Compatibility release**：先发布声明最低支持版本的 Desktop；旧同步 facade 只等待同一个 Harness Run；
7. **Default harness**：DB route `discovery.execution_mode=harness`，legacy executor 不再拥有 provider/domain 写入权；
8. **Legacy retirement**：升级窗口和遥测证明已发布客户端不再依赖同步返回后，另阶段删除 facade。

### 6.10 Rollback

- 以一个 DB config revision 同时切回 `legacy` 并关闭相关 agent/publish gate，以 expected-head CAS 一次生效；
- 停 Harness 对新 Discovery Step 的 claim，但已有 Run 保留可读/可取消；
- 已 publish 的领域结果保持有效，不逆向删除；
- 未 publish Artifact 不进入旧搜索历史；
- rollback 不重复请求 Provider 或退回已经结算的真实 usage；
- 若新客户端只认识异步 contract，UI 要显示服务暂时降级并能调用兼容 adapter，不能空白。

### 6.11 Exit gate

- [ ] 100% 通过认证、校验与 admission 的新 start 请求立即返回 `202`，不接触 Provider；容量/鉴权/校验拒绝使用
  明确 typed error，不把网络波动算成例外；
- [ ] Provider partial/error 与 legacy 语义一致或有记录的改进；
- [ ] crash/restart 不重复已完成来源或 publication；
- [ ] 搜索 publication 与 Web Library/ProjectSource promotion 边界有独立 approval/receipt/deny tests；
- [ ] abstract-only 与 provenance 在 Web/Desktop 一致；
- [ ] shadow parity 和人工质量阈值通过；
- [ ] canary ledger/领域 publish exactly-once；外部请求按 provider 能力为 idempotent 或明确 `indeterminate`，
  不虚构“绝不重复计费”；
- [ ] Web、Desktop 都能恢复/取消；
- [ ] legacy rollback 演练成功。

### 6.12 Non-goals

- 不自动下载所有搜索结果 PDF；
- 不声称全文阅读、确认原创或验证实验；
- 不加入无限来源、任意网页浏览或用户 MCP；
- 不自动将所有结果写入 Zotero/项目；
- 不在 H2 删除 legacy 历史数据或同步 endpoint。

### 6.13 Commit boundaries

1. `Define typed Discovery workflow artifacts`；
2. `Expose existing search providers as bounded capabilities`；
3. `Run Discovery through durable mapped steps`；
4. `Generate validated Chinese trick cards and criticism`；
5. `Publish Discovery results idempotently`；
6. `Approve result promotion with resource-bound receipts`；
7. `Replay legacy searches through the shadow workflow`；
8. `Move the web Discovery surface to Harness runs`；
9. `Move desktop Discovery to the shared run contract`；
10. `Canary and cut over Discovery with rollback coverage`。

---

## 7. H3 — Daily Papers system/user workflows

### 7.1 Goal

把 Daily 拆成 system ingestion 与 per-user issue 两层：公共抓取/用户无关阅读可以安全复用，每位用户的方向、
排序、额度和期刊输出保持隔离；睡眠、停机和 Provider 故障后可恢复，Pharos ledger 不重复结算；外部调用
结果不明时显式进入 `indeterminate`，不伪造“不可能重复计费”的保证。

### 7.2 Entry conditions

- [ ] H2 的真实 mapped steps、Provider accounting 和 publication 已稳定；
- [ ] Daily 现有 scheduler、catch-up、direction matching、Vault 和导入测试全部为绿色基线；
- [ ] system/user Artifact sensitivity 和 API 可见性已通过隐私审查；
- [ ] `daily.ingest@1` 与 `daily.issue@1` 的 idempotency key 已冻结；
- [ ] 共享模型成本归属 `system_shared` 的策略已确认。

### 7.3 Workflow deliverables

#### `daily.ingest@1` — system scope

```text
evaluate_due_window
→ load_aggregate_fetch_plan
→ fetch_arxiv_pages
→ normalize_and_global_dedup
→ publish_metadata
→ cache_public_abstract_readings[]
→ validate_and_publish_cards[]
→ aggregate_ingest_report
→ release_public_ingest
```

#### `daily.issue@1` — user scope

```text
project_public_ingest_for_owner
→ snapshot_user_directions
→ select_candidates
→ compute_user_relevance
→ apply_entitlement_and_daily_limit
→ synthesize_issue
→ publish_issue
→ optional approval for local import/Vault action
```

要求：

- `daily.ingest@1` 使用 system key（date/window + workflow/input hash），不含 owner/direction；
- `daily.issue@1` 使用 owner + date/window + user-config snapshot hash + public ingest release hash；两者不得合并成一个 key；
- public ingest release hash 严格使用 Architecture §11.6.1 的 canonical envelope，绑定 immutable release ID、source
  schema name/version、source content hash、public manifest hash 与 release policy version，而不是裸 content hash；
  同内容撤销后 reissue 生成新 ID/hash 和新 issue key；
- system ingest Artifact 不能直接成为 user Run input。`release_public_ingest` 只把 allowlisted public
  metadata/abstract/card manifest 注册为 public release；issue 首步为该 owner 幂等创建最小
  `daily.ingest_projection@1`，后续 Step 只引用 user projection；
- system Event/Artifact 不向普通用户暴露其他人的方向或关键词并集；
- user direction snapshot 只能被该 owner 的 Run 读取；
- user-independent reading cache key 包含 paper identity、input hash、workflow/prompt/schema/model version；
- 任何含用户输入的模型结果不得进入全局共享 cache；
- schedule 采用 level-triggered due predicate，保留有限 catch-up；
- user relevance/membership/rank/cap 是确定性步骤并保留 baseline；可选 Agent 只合成 issue 文案，不能暗中删减、
  增加或重排候选，Daily v1 不设 critic Step、不读取全文；
- `DailyPaper` 继续是发布后的在线业务记录，Daily Vault 继续是便携 snapshot；
- 导入文库/Vault 写入是明确用户动作，H5 前不假装服务器写了本地文件。

### 7.4 Backend deliverables

- versioned schedule 和 due/idempotency service；
- 为 legacy sweeper 抽出一次性 candidate/reading Observation capture，并为 metadata/card/issue 各自抽出短事务
  publication seam；shadow 复用 captured input，不能再次 fetch/read；
- system/user Run API 投影：普通用户只看到自己的 issue 和可公开的 ingest 摘要；
- 实现 Architecture §11.6.1 的 public release/projection service、复合 FK、schema/sensitivity allowlist、撤销与
  projection receipt；拒绝普通 user Run 直接引用任何 system Artifact；
- projection payload 只带公开卡字段以及脱敏 ingest outcome、coverage loss、public typed source errors、evidence
  level 和必要 provenance ID/hash；所有复制源内容的 user Artifact 建立同 owner `derived_from` lineage；release revoke
  由幂等 job 阻止新 projection 并 tombstone projection/非领域派生 payload，保留 Run/Event/Artifact/link/hash/receipt ID，
  已独立 publish 的领域记录不静默删除但 receipt 标记 revoked source；
- 复用现有 fetcher、reader validation、direction/relevance 和 PDF SSRF allowlist；
- per-paper reading 使用 mapped Step、并发/单调用/总批次预算；
- system `daily.ingest@1` 只能解析到 `system_shared/official` provider，绝不借用任一用户 personal BYOK；
  owner-scoped `daily.issue@1` 才能在授权/预算内使用该 owner 的 personal provider；
- metadata 在模型调用之前幂等发布；Provider 未配置或长期失败时论文仍以 `pending` 出现在旧 API，且不生成
  占位摘要；每张有效卡随后独立幂等发布；
- errored paper 的 retry 与 scheduler due 原因分离，避免永久热循环；
- 当前 `DailyRun` 可保留为兼容投影，但完整 Attempt 历史只在 Harness；
- 新增 owner-scoped Daily-import mapping，记录 user/issue/daily-paper 到 web `Paper` 的导入关系；共享
  `DailyPaper.imported_paper_id` 只可暂作兼容投影，不能继续承担多用户事实；
- usage 将 system shared 与 per-user official/BYOK 分开；
- admin 指标只显示数量/失败率，不显示方向和标题。

### 7.5 Frontend deliverables

- 每日论文保留现有按日期和方向浏览；
- 展示 issue Run 的生成时间、partial/pending 状态和恢复信息；
- 卡片继续保持英文标题 + 中文核心 Trick/摘要；
- 用户可手动重跑自己的 issue，但不能触发无界 system crawl；
- 导入文库和 Vault 操作明确显示执行位置；
- Run timeline 默认折叠 system 内部步骤，避免把基础设施噪声放到阅读界面。

### 7.6 Desktop deliverables

- Daily 工具窗口通过 user issue Run 获取状态和结果；
- 应用休眠/关闭后重开可恢复；
- 导入本地 Zotero 继续走明确的现有用户动作；
- Vault 目录仍按版本化格式读写，不把 Harness DB 镜像到本地；
- H5 前没有后台本地写入；
- 真实测试必须使用隔离 Zotero data directory。

### 7.7 Data migration

- 现有 `UserDirection` / `UserDailyConfig` 原样保留；
- 为已启用用户幂等建立 schedule 或按一个 system schedule + user due predicate 创建；
- 不把过去每天唯一 `DailyRun` 伪造成细粒度 Attempt；
- 旧 `DailyPaper` 不重复复制；新 publish 继续按 arXiv identity 幂等；
- owner-scoped Daily-import mapping 以 additive revision 建立并回填可安全确认的关联；无法判定 owner 的旧共享
  `imported_paper_id` 只保留兼容显示，不猜测归属；
- legacy 与 Harness 切换不改变 Vault schema；若确需变更，另升 `schema_version` 并提供双读迁移。

### 7.8 Tests and quality evaluation

- 时区、本地午夜、周末/无论文、睡眠三天、catch-up ceiling；
- scheduler 多次 tick 只创建一个相同窗口 Run；
- 两 worker/system schedule race；
- 多用户不同 category/direction 不互相泄露；
- system release 不能被普通用户直接读取；同一 release 对同一 owner 重放只生成一个 user projection，两个 owner
  的 projection/Run/Artifact 互不可见；非 allowlisted schema 或非 public sensitivity 永远不能投影；
- canonical release hash golden test 覆盖所有 envelope 字段、canonical JSON 稳定性和客户端不可覆盖；同内容
  reissue 必须得到新 release ID/hash/issue Run，不能复活已撤销 key；
- projection contract test 验证 partial outcome、coverage loss、public typed source errors、evidence level 与 provenance
  可见且 stack/endpoint/raw response/secret 不可见；
- revoke race/replay test 验证新 projection 被拒、重复撤销幂等、所有 owner 的 projection 与 feed/digest 等
  `derived_from` 非领域派生 payload 被 tombstone、审计 ID/hash/receipt 仍可查，独立发布的领域记录仅标记 revoked source；
- 关闭 Daily 的用户不扩大 fetch net；
- shared cache 仅复用 user-independent input；
- Provider unavailable/429/invalid JSON/time budget 后 pending/retry；
- 一个用户修改方向后只重建其 issue，不重读全局论文；
- import/Vault idempotency；
- 同一 Daily paper 被两个用户导入时 mapping 隔离、旧兼容列不覆盖另一用户状态；
- crash after fetch、read、publish 各阶段；
- legacy vs shadow 日集合、排序和分数比较；
- 人工 eval：核心 Trick 准确、中文可读、相关性排序和重复率。

### 7.9 Shadow/canary/cutover

1. **Shadow input reuse**：legacy sweeper 是唯一 fetch/LLM writer；Harness 消费同一 captured candidates；
2. **Per-user comparison**：比较 issue 可见集合、方向、排序和日期统计，不把 shadow issue 暴露给用户；
3. **System canary day**：Harness 拥有一个受控窗口，legacy scheduler 对该窗口停用；
4. **User canary**：选定账户使用 Harness issue，其他账户继续 legacy view；
5. **Desktop/Web parity**：两个客户端都稳定后才改默认；
6. **Cutover**：DB route `daily.execution_mode=harness`，旧 scheduler 不再启动；
7. **Soak**：至少跨越 7 个有有效公告/候选输入的日期和一次人工容器重启；周末或空输入日不计样本日。

### 7.10 Rollback

- 以一个 DB config revision 同时切回 `legacy`、关闭相关 agent/publish gate并恢复旧 scheduler，以 expected-head
  CAS 一次生效，确认 Harness scheduler 不再认领 Daily Step；
- 已 publish `DailyPaper` 保持可读，legacy dedup 会跳过；
- Harness-only issue Artifact 保持历史但不覆盖旧 date view；
- 不回滚/删除用户方向、Vault 或已导入文库的论文；
- 记录 system shared usage，不因回退重复结算；
- 回退后跑一次 legacy due check 补欠账，而不是强制重抓整个历史。

### 7.11 Exit gate

- [ ] 7 个有效公告日 soak 无重复 arXiv 抓取、重复论文或 scheduler hot loop；
- [ ] system/user 隔离和成本归属测试通过；
- [ ] public release/user projection 的 schema allowlist、DB 约束、canonical release identity/hash、同内容 reissue、
      脱敏 partial/provenance contract、幂等、lineage-aware 撤销和跨 owner 404 全部通过；
- [ ] sleep/restart/catch-up 行为优于或等同 legacy；
- [ ] Web/Desktop/Vault/导入无回归；
- [ ] pending/partial/error 对用户可解释；
- [ ] shadow/人工质量阈值通过；
- [ ] rollback 后下一次 due check 正常。

### 7.12 Non-goals

- 不为每个用户独立全量抓取 arXiv；
- 不共享用户方向、备注或个性化模型推断；
- 不把 Vault 变成实时执行数据库；
- 不后台写本地 Zotero；
- 不因有 Harness 就删除当前 Daily 领域表。

### 7.13 Commit boundaries

1. `Define system and user Daily workflow contracts`；
2. `Schedule idempotent level-triggered Daily runs`；
3. `Ingest and cache user-independent paper readings`；
4. `Build owner-scoped Daily issues from direction snapshots`；
5. `Publish Daily domain records without duplication`；
6. `Compare Harness issues against the legacy sweep`；
7. `Move web Daily Papers to durable issues`；
8. `Move desktop Daily Papers without changing Vault authority`；
9. `Cut over the Daily scheduler after a seven-day soak`。

---

## 8. H4 — Evidence-aware Project Research

### 8.1 Goal

让项目研究从人工九阶段账本升级为有界、可审核的研究协作 Workflow：整理问题、盘点证据、指出缺口、提出
候选假设、反方审查并形成研究计划。所有发布仍需用户批准，自动产物只能成为 `draft`，不执行实验。

### 8.2 Entry conditions

- [ ] H2 Discovery publication 与 Evidence strength 已稳定；
- [ ] H3 kernel 在真实调度下无恢复/计量缺陷；
- [ ] Project、ProjectSource、Evidence、ProjectArtifact 当前 CRUD 与 owner tests 为绿色；
- [ ] `project.snapshot@1`、`project.research_profile@1`、`project.search_execution_plan@1`、
  `project.evidence_matrix@1`、`project.hypothesis_set@1`、`project.critique@1`、`project.research_plan@1`、
  `project.decision_packet@1` 与 promotion receipt schemas 已冻结；
- [ ] 明确哪些结果只来自 abstract，哪些来自 page-addressable Evidence；
- [ ] Decision 9 仍有效且测试将其作为 deny gate。

### 8.3 Workflow deliverables

```text
authorize_and_snapshot
→ normalize_research_profile
→ propose_search_execution_plan
→ compile_search_execution_plan
→ optional bounded child discovery runs[]
→ collect_evidence
→ curate_evidence_matrix
→ propose_hypotheses
→ skeptical_review
→ optional revise_hypotheses once
→ draft_research_plan
→ assemble_decision_packet
→ await_promotion_approval
→ promote_selected
```

要求：

- snapshot 固化 project/source/evidence IDs + hashes；
- `project.search_execution_plan@1` 是检索前的有限执行计划；`project.research_plan@1` 是批判后的研究计划，
  两者不得复用一个含糊的 `project.plan` schema；
- child Discovery 只按编译后的有限 query 启动，继承更窄预算/权限；每个 child 以 parent/child link 回传
  `discovery.result_set@1` 或 typed partial/error，部分失败不抹掉已有项目证据；
- abstract-only 与 page evidence 分层展示；
- planner、curator 与 critic 使用独立 Role contract，只通过 immutable Artifact 协作，不能共享未记录的聊天；
- hypothesis proposal 必须包含可证伪条件、反证、缺口与区分性观察；critic 后最多一次显式 superseding
  revision，不能展开无限辩论或重新改写已经执行的 search plan；
- `project.research_plan@1` 是 critic 后给用户的不可执行后续研究建议；`project.decision_packet@1` 汇总来源、
  风险、待决项与 exact resource refs，两者都不改变项目；
- `promote_selected` 只创建获批类型的 `ProjectArtifact(status="draft")`。选中论文成为 `ProjectSource` 是另一条
  resource-bound approval/publication receipt，必须先有 owner `LiteratureResult`，不得与 draft approval 合并；
- 自动或获批创建 `ProjectArtifact(type="result")` 永久 deny，即使 status 是 draft；
- 不自动推进到 experimentation，不生成/执行 shell，不把模型意见标 verified。

### 8.4 Backend deliverables

- owner-scoped project snapshot builder；
- bounded child-run composer：仅调用已注册的 `literature.discovery@1`，固定 max children/results/depth=1，
  无递归 fan-out；
- Evidence/ProjectSource context builder，按 token budget 确定性截断；
- research profile/search-plan/hypothesis/research-plan planner、evidence curator、skeptical critic 与 decision
  packet capabilities/roles；
- ProjectArtifact approval action 绑定 project、每个待建 draft、Artifact hash、Workflow version 和过期时间；
  ProjectSource/云端文库写入使用独立 approval、stable publication key 与 receipt，不能用前者授权；
- publish service 重新读取 owner/target、检查 supersede 并幂等写入；
- Project 删除/归档/修改与运行中的 snapshot 冲突有明确策略；
- 运行期间来源变更不悄悄进入旧 Run，需要 fork/new Run；
- Policy 永久 deny `shell`、`compute`、`mark_verified`、`create_result` 等未授权动作；
- Claim/Evidence 强绑定若尚未完成，计划必须显示 gap，不能伪造引用；
- H4 不允许模型自动创建新 Evidence；只有已存在 Evidence 可被引用。未来若开放，必须逐条 exact approval 并
  另行定义 quote/locator 校验与 publication contract。

### 8.5 Frontend deliverables

- Project 页面增加 Research Run 区域，不替换当前人工 artifact 编辑器；
- 显示 evidence map、abstract/page strength、conflict 与 missing evidence；
- 逐候选展示 hypothesis、falsifier、evidence strength、critic、缺口和成本；
- 决策 UI 按 decision packet 逐项支持创建草稿、要求 refine、拒绝或暂不处理并保存理由；
- approval 前可预览即将创建的 draft artifacts；
- 已 publish artifact 可回到 producer Run/Artifact；
- UI 显著说明“计划不是已执行实验，draft 不是 verified”。

### 8.6 Desktop deliverables

- 在论文/Project 上下文中可以启动或打开 Project Research Run；
- 当前打开论文可作为明确的 source/evidence reference，不自动上传本地 PDF；
- Run timeline、候选比较和 approval 与 Web 使用同一 API；
- 用户批准后刷新现有 ProjectArtifact UI；
- 不把长篇写作编辑器塞进 Desktop；
- 无 H5 Bridge 时需要本地全文的 Step 明确等待用户上传或降级 abstract-only。

### 8.7 Data migration

- 历史 ProjectArtifact 保持 human-authored，不补造 provider/model provenance；
- 新 Harness proposal 不直接混入旧表，只有 approved publish 才创建 draft；
- mapping 保存 producer Artifact/Run 和 domain artifact ID；
- project snapshot 只引用，不复制整个 PDF；
- 旧九阶段状态保持，Harness 不替用户自动跳阶段。

### 8.8 Tests and quality evaluation

- owner/project/source/evidence cross-link；
- page/abstract/unlocated strength 不可升级；
- quote 与 model inference 渲染/发布不混淆；
- project 在 snapshot 后修改、归档、删除的冲突；
- approval reject/expire/superseded artifact；
- ProjectArtifact 与 ProjectSource 两种 approval 不能互相消费，也不能合并成批量“全部批准”；
- publish crash/idempotency；
- prompt injection in paper/notes 不开放工具；
- Agent 请求 shell/URL/mark_verified 必须 deny 并记录安全 Event；
- 自动 artifact 永远为 draft；
- 自动或获批 publication 都不能创建 `ProjectArtifact(type="result")`；
- child Discovery 的限额、parent/child owner、partial fan-in、取消传播和重放不重复搜索；
- planner/curator/critic/synthesizer fake fixtures，以及最多一次 hypothesis revision 的确定性测试；
- 人工 eval：可证伪性、证据覆盖、反方质量、新颖性表述谨慎度、虚构引用率；
- Desktop/Web 相同 Run 的状态和 Artifact 一致。

### 8.9 Shadow/canary/cutover

1. **Dry shadow**：从项目 snapshot 生成未发布 proposal，只供内部评估；
2. **Owner opt-in**：用户主动启动，默认停在 approval；
3. **Canary projects**：限定项目与预算，收集 accept-draft/refine/reject/defer 与保存率；
4. **Default available**：所有有 entitlement 的用户可主动启动，绝不后台自动运行；
5. Project 的人工 CRUD 永远保留，不存在强制 cutover。

### 8.10 Rollback

- 以一个原子配置 revision 切回 `legacy` 并关闭 Project 新 Run/agent/publish；保留已生成 proposal 只读；
- pending approval 取消并说明原因；
- 已发布 draft 仍是用户项目记录，不自动删除；
- 人工 Project CRUD 不依赖 Harness，继续可用；
- 不逆向改变项目 stage 或 artifact status。

### 8.11 Exit gate

- [ ] 所有 proposal 都有明确来源强度、反证和 provenance；
- [ ] 运行图与 `HARNESS_WORKFLOWS.md` 的 P-00…P-120、Artifact schema/hash 和最多一次 revision 完全一致；
- [ ] 用户批准前没有领域写入；
- [ ] 自动发布永远是 draft；
- [ ] `ProjectArtifact(type="result")`、stage 自动推进和 verified 自动标记均被 deny tests 覆盖；
- [ ] 可选 child Discovery 有界运行，部分失败可解释且不会越 owner/预算；
- [ ] Decision 9 deny tests、prompt injection 和越权测试通过；
- [ ] 人工 eval 达到事先冻结阈值；
- [ ] Web/Desktop 可完成同一决策；
- [ ] 禁用 Harness 后人工项目功能完整。

### 8.12 Non-goals

- 不执行实验、不分配 GPU、不运行生成代码；
- 不自动标 verified 或自动推进九阶段；
- 不声称 novelty confirmed；
- 不用 Agent 群聊代替 generator/critic Artifact；
- 不在 Desktop 构建完整写作环境。

### 8.13 Commit boundaries

1. `Define evidence-aware project research artifacts`；
2. `Snapshot owner-scoped project evidence deterministically`；
3. `Compile a bounded project search execution plan`；
4. `Compose optional child Discovery runs without recursive fan-out`；
5. `Generate bounded and falsifiable hypothesis proposals`；
6. `Critique proposals through independent typed artifacts`；
7. `Require project decisions before publication`；
8. `Publish approved proposals as draft project records`；
9. `Add the web Project Research decision surface`；
10. `Add desktop Project Research beside the active paper`；
11. `Canary project research without enabling experiment execution`。

---

## 9. H5 — selected full-text and Desktop Local Capability Bridge

### 9.1 Goal

完成 `HARNESS_WORKFLOWS.md` §4.5 的逐篇全文深化轨，并在不暴露 SQLite、任意文件路径或 shell 的前提下，
让等待本地数据/副作用的 Harness Step 能向已配对 Desktop 请求一项高层动作，由 Desktop 主动出站领取、
明确批准、执行和回传最小 Observation。全文轨可以消费用户已授权的后端 `Paper/PaperChunk`；Bridge 只在
全文仅存在本地或需要本地写入时介入，二者不得被混成“服务器可以浏览 Zotero”。

### 9.2 Entry conditions

- [ ] H1 approval/resource canonicalization 已在 H2–H4 验证；
- [ ] Desktop shared Zotero library schema compatibility gate 仍通过；
- [ ] device pairing、revocation、离线、重放和多设备 threat model 已评审；
- [ ] 本地 Capability allowlist 和每项最小输入/输出 schema 已冻结；
- [ ] `paper.fulltext_card@1` 的逐 claim evidence level、locator、retention 与 publication contract 已冻结；
- [ ] 公开 PDF 获取与私有 PDF 上传的 URL、版权、大小、敏感度和删除策略已评审；
- [ ] 真实开发测试有隔离 `-datadir`，不会碰 `~/Zotero`；
- [ ] Privacy copy 明确说明哪些字节会离开设备。

### 9.3 Initial capability allowlist

仅实现有明确产品需求的高层动作，建议顺序：

1. `local.zotero.get_item(libraryID, key)`；
2. `local.pdf.export_attachment(libraryID, attachmentKey, purpose)`；
3. `local.zotero.create_note(parentKey, content)`；
4. `local.zotero.import_attachment(parentKey, downloadToken)`；
5. `local.daily_vault.write(snapshot)`。

不实现：任意 SQL、任意 path read/write、shell、全盘搜索、批量导出整个库、后台上传全部 PDF。

### 9.4 Backend deliverables

- 实现 selected full-text `resolve → approve → acquire/load → chunk/locate → read → validate → link`；
- 只有用户选择的论文可进入该分支，禁止把搜索结果或本地文库全部批量上传；
- 已存在的 owner `Paper/PaperChunk` 优先复用；公开 PDF 仍经过 allowlisted URL、SSRF、大小和内容校验；
- 每条全文 claim 独立保存 `unlocated|page`、`quote|model_inference`、Evidence ref 与 locator；卡片不得整体
  把未定位推断升级成 `page` evidence；
- 全文 Artifact 通过 `derived_from/supersedes` 连接摘要卡，但不覆盖旧 `LiteratureResult` 的摘要字段；
- acquire/chunk/model/cancel 的临时文件、owner blob、retention 和删除状态可审计，不遗留半个可读 PDF；
- owner-scoped device enrollment/revocation 和短期 device credential；
- device/capability version negotiation；
- local action queue 与 lease，不复用普通 server worker claim；
- action resource 固化为 device + libraryID + key + purpose + expiry；
- approval 与 action 绑定，重放或资源变化后失效；
- Desktop 长轮询或 fetch-based stream，始终由客户端主动出站；
- Observation schema、hash、最小内容、敏感级别和 retention；
- upload/download 使用一次性 token、大小上限、内容类型和内容 hash；
- device offline → waiting，超时 → expired/可解释降级；
- revoke 后所有未领取 action 失效，已领取 action 不能继续上传；
- 管理员只看 device 在线/版本/错误，不看条目标题或 PDF 内容。

### 9.5 Frontend deliverables

- Discovery 结果逐篇提供“全文深化”，预览将读取/上传的来源、模型、费用、保留期和证据等级；
- 全文卡逐 claim 展示引用/推断、页码或“未定位”，不把整个卡显示为 `page`；
- 摘要卡保持可见，全文分支失败/取消不破坏原搜索结果；
- 设备列表、名称、平台、版本、最后在线、撤销；
- Run 显示“等待某台设备”而不是无限 spinner；
- action 预览包括目的、资源、数据发送范围和过期时间；
- Web 可以取消等待，但不能直接访问本地路径；
- 多设备时由用户选择，不允许服务器猜测哪份 Zotero 库是权威。

### 9.6 Desktop deliverables

- 用户可从当前 PDF/Discovery 结果显式发起单篇全文深化；若需导出本地附件，复用下述精确资源 approval；
- OS credential store 保存 device token；
- Pharos 启动后以有限频率出站领取属于本 device/owner 的 action；
- 使用 Zotero 自身 API 读取/写入，永不直改 `zotero.sqlite`；
- 每次高风险 action 显示 native approval，含 resource、purpose、大小/目的地；
- 执行前重新验证 libraryID/key、attachment 是否仍存在；
- idempotency receipt 防止重连后重复 note/import/Vault write；
- 退出/崩溃后 action 可恢复为 waiting/abandoned，不伪装完成；
- 离线不阻断普通 Zotero/Pharos 阅读；
- 提供彻底取消配对并清除 credential 的入口。

### 9.7 Documentation deliverables

- pairing 和 device credential threat model；
- 每项 Capability 的 Action/Observation、risk、approval、idempotency、retention；
- 本地数据流图；
- 用户隐私说明；
- 设备丢失/撤销/离线/版本不兼容 runbook；
- 更新 `CLIENT_DATA_ARCHITECTURE.md`，但不把 Bridge 描述成 sidecar writer。

### 9.8 Data migration

- 新增 device/local action/receipt 表；
- 为 selected full-text Run/Artifact、domain paper 与 retention 状态增加 additive mapping/index；
- 不改 Zotero schema；
- 不在 server 保存本地绝对路径；
- PDF 如获批准上传，进入 owner-scoped blob 并保存 purpose/retention；
- 旧账户默认没有 device，H2–H4 workflow 必须继续支持云端/人工降级路径。

### 9.9 Tests and security evaluation

- metadata/abstract-only 输入不能生成全文卡或页码；
- 同一全文卡中的 quote、page inference 与 unlocated inference 逐 claim 不发生强度串升；
- 公开 URL redirect/SSRF、私有上传 approval、MIME/hash/size、取消清理和 retention expiry；
- 全文分支 crash/retry 不重复 blob/Artifact/publication；模型调用仅在 provider 有 idempotency/query contract 时
  自动重试，否则进入 `indeterminate` 并在再次花费前重新确认；
- 全文卡只链接摘要卡，不覆盖旧 `LiteratureResult`；
- pairing token 一次性、过期、错误账户、重放；
- device revoke 与领取竞态；
- action resource substitution、libraryID/key 越权；
- duplicate receipt 不重复 note/import/write；
- 客户端崩溃在副作用前后；
- offline/slow/old-version device；
- 恶意 PDF/超大上传/错误 MIME/hash；
- prompt/论文内容不能改变本地 action；
- 绝对路径、token、PDF 正文不进入普通 Event；
- 所有桌面测试使用隔离目录；
- 人工安全 review 确认无 SQL/shell/general filesystem surface。

### 9.10 Shadow/canary/cutover

1. **Source-build pairing**：只连接测试账户和隔离文库；
2. **Read-only canary**：先开放 `get_item`，不上传 PDF；
3. **Server-paper full-text canary**：先用已授权后端 Paper 验证逐 claim grounding 和 retention；
4. **Explicit export canary**：单附件、单次 approval、严格大小上限；
5. **Write canary**：note/import/Vault 逐项单独上线，不共用总开关；
6. **Signed/official release gate**：按桌面发布规则升级版本并发布；
7. Bridge 与全文深化永远是逐篇 opt-in；无所谓“所有用户 cutover”。

### 9.11 Rollback

- 服务端关闭 Bridge flag，停止创建/领取 action；
- 撤销 device credentials 和未完成 action；
- 已完成本地副作用不自动反向删除，由用户在 Zotero/Vault 中决定；
- 上传 blob 按 retention 清理，不在事故中立即破坏性删除；
- Desktop 本地文库和普通功能继续可用；
- 回滚不能要求降级 Zotero schema。

### 9.12 Exit gate

- [ ] Read/write capabilities 每项独立通过 threat model、测试和 canary；
- [ ] selected full-text 的逐篇 approval、逐 claim evidence、取消清理、retention 和质量门槛全部通过；
- [ ] 无 direct SQLite、任意路径、shell 或全库导出；
- [ ] 所有副作用有 approval + idempotency receipt；
- [ ] device 丢失/撤销/离线行为明确；
- [ ] 本地数据最小化和 retention 可验证；
- [ ] 关闭 Bridge 后 Desktop 仍是完整本地参考管理器；
- [ ] 真正 Zotero 兼容测试只在副本/隔离目录完成。

### 9.13 Non-goals

- 不远程控制用户桌面；
- 不让 server 连接入站端口；
- 不镜像整个 Zotero 文库；
- 不建立第二个本地参考管理器；
- 不默认深化全部 Discovery/Daily 论文，不把全文 Artifact 冒充领域 verified fact；
- 不在 H5 执行实验代码。

### 9.14 Commit boundaries

1. `Deepen one selected paper with claim-level grounding`；
2. `Retain and clean up approved full-text content safely`；
3. `Model paired desktop devices without exposing local paths`；
4. `Queue owner-scoped local capability actions`；
5. `Pair and revoke desktop devices securely`；
6. `Execute read-only Zotero capabilities through native APIs`；
7. `Approve and export one local PDF with bounded retention`；
8. `Make local write capabilities idempotent`；
9. `Expose device state and pending actions in the web Run Center`；
10. `Document and threat-test the full-text and Desktop boundary`；
11. `Canary each local capability independently`。

---

## 10. H6 — evaluation, observability and measured scaling

### 10.1 Goal

把“能运行”提升为“能证明质量、能运营、能按证据扩容”：建立冻结 eval 集、用户/运营指标、fork/replay 的安全
语义、retention 和容量决策。H6 不默认更换数据库或 Agent framework。

### 10.2 Entry conditions

- [ ] H2–H4 至少有一个完整发布窗口的真实 Run 数据；
- [ ] H5 若启用，其安全 Event 与 device metrics 可聚合；
- [ ] 用户反馈、失败类别、保存/批准/拒绝率有基线；
- [ ] 数据 retention 与隐私要求经确认；
- [ ] 扩容讨论有实际 queue/DB/latency 指标，而非框架功能比较。

### 10.3 Backend deliverables

- 把 W5 cross-workflow composition 正式纳入 H6：只注册
  `daily.issue → literature.discovery`、`literature.discovery → project.research_cycle` 和 H4 已实现的
  `project.research_cycle → literature.discovery` 三类 allowlisted edge；每条 edge 有 typed input adapter、最大深度、
  parent/child budget、scope/owner、cancel/fan-in 与 Artifact lineage contract；
- Daily system Artifact 必须先成为 owner projection；Discovery 结果必须先成为 owner `LiteratureResult`，并经独立
  `ProjectSource` approval/receipt 后才能进入 Project。通用 composer 不能绕过 child Workflow compiler、policy 或
  publication approval，也不能接受 Agent 自由消息作为 edge；
- workflow/version/model 分层的 eval runner；
- 冻结 dataset revision、annotation rubric 和 evaluator version；
- deterministic checks 与 LLM-as-judge 分开，judge 不能是唯一质量门槛；
- OpenTelemetry spans/metrics 使用脱敏 semantic attributes；
- queue depth、oldest age、lease expiry、retry、cost、approval wait、publish/save rate；
- Event/Artifact retention、archive/export 与删除策略；允许清理大 payload/blob，但必须保留 tombstone（Artifact
  ID、hash、schema/version、provenance、link 与 deletion reason/time），不能让 replay/审计链断裂；
- safe fork：从无未决副作用的 checkpoint 创建新 Run，旧 Run 不改；
- replay 默认只重算 pure Agent/deterministic Step，外部副作用和付费调用需新预算/审批；
- entitlement plan、并发、额度和 usage reconciliation 的正式运营面；
- operator 可以暂停 Workflow version/Provider，而不能读取用户正文；
- backup/restore、DB integrity 和 orphan blob reconciliation。

### 10.4 Frontend deliverables

- 为“围绕本期深入探索”“把选中论文加入项目/启动研究周期”提供显式用户动作与副作用预览；不因打开、滚动或
  Agent 文本自动创建 child Run/ProjectSource；
- 用户可比较 Run/fork 的版本、Artifact、成本和结果差异；
- 清楚区分 replayed、cached、new attempt；
- 账户 usage/额度/下一次恢复时间；
- operator dashboard 只显示聚合指标和匿名 error classes；
- eval/admin 页面不展示论文标题、query、Evidence 或 Artifact 正文；
- export/delete 操作说明影响范围并需要确认。

### 10.5 Desktop deliverables

- 使用同一 allowlisted composition API 恢复 parent/child Run，并在需要 Web 文库/ProjectSource/Zotero 写入时显示
  独立 approval 或本地确认；
- 查看 usage、模型/Workflow version 和 fork 后结果；
- 不在本地复制完整 server trace；
- Bridge metrics 仅设备状态/错误类，不上传本地库清单；
- 在正式客户端中提供一致的 retention/export/delete 入口或链接到 Web 管理面。

### 10.6 Framework and storage review

用 `HARNESS_LANDSCAPE.md` 的触发条件做正式 ADR：

- SQLite claim/write p95、busy timeout、DB size、backup time；
- 单进程 worker 的 CPU/RSS、queue age 和 restart recovery；
- 多机/跨区/长等待是否已成为真实需求；
- ModelGateway/AgentRunner 维护成本是否证明值得采用 Pydantic AI；
- durable runtime 是否需要 Temporal/DBOS/LangGraph；
- OTel 自建指标是否已不足。

只有 ADR 通过才迁移。优先顺序仍是：优化查询/索引 → worker 与 API 进程分离 → Postgres → 通用 durable
runtime。每一步都保持 API/Artifact/Event contract。

### 10.7 Tests and evaluation

- Daily → Discovery、Discovery → Project、Project → bounded Discovery 的 scope/budget/cancel/fan-in、最大深度、
  Artifact lineage、重复点击幂等和 ProjectSource approval/publication；未知 edge、跨 owner、跨 scope 直链、
  Agent 自造 child workflow 全部拒绝；
- eval dataset 不含未经授权的真实私有研究内容；
- dataset/hash/evaluator/model version 可复现；
- Discovery：coverage、dedup、核心 Trick 准确、虚构率；
- Daily：重复率、方向相关、中文可读、摘要事实性；
- Project：可证伪性、证据引用、critic 质量、错误升级率；
- fork/replay 不重做副作用；
- retention 不破坏 domain authority；
- usage reconciliation 与账单/额度守恒；
- 数据库备份恢复和 blob orphan；
- production-like load/soak 与故障注入；
- 每次 Workflow/prompt/model 变更有质量 regression gate。

### 10.8 Rollout and rollback

- composition 先只显示 deep-link proposal，再按三条 allowlisted edge 分别 opt-in；任一 edge 可独立禁用，已有 child
  Run/Artifact 保持可读，不能靠删除 parent/link 回滚；
- Metrics 先只读收集，再启用告警，不让告警失败影响 Run；
- eval gate 先 advisory，再对高风险发布设 blocking；
- fork 先 operator/internal，再用户 opt-in；
- entitlement enforcement 先 shadow calculate，再 soft warning，再 hard limit；
- 数据库/runtime 迁移必须双读/影子或备份切换，并有独立 ADR/回滚；
- rollback 一个观测供应商不能破坏本地 Event Store。

### 10.9 Exit gate

- [ ] W5 三条 allowlisted composition edge 的 owner、预算、取消、fan-in、lineage、approval 与幂等测试全部通过；
- [ ] 三条 Workflow 各有冻结 eval set、rubric、阈值和回归历史；
- [ ] operator 能在不看内容的前提下诊断 queue/provider/usage；
- [ ] fork/replay 不重做副作用并能解释成本；
- [ ] retention/export/delete/backup 行为通过测试；
- [ ] entitlement shadow 与 settled usage 对账；
- [ ] 每个扩容决定都有数据和 ADR；若指标不要求扩容，明确保留 SQLite 也是合格结果。

### 10.10 Non-goals

- 不为“高级感”迁移 Postgres/Temporal；
- 不让 LLM judge 成为唯一 evaluator；
- 不允许管理员浏览用户研究内容；
- 不通过删除 provenance 来节省空间；
- 不在 H6 偷渡实验执行。

### 10.11 Commit boundaries

1. `Version Harness evaluation datasets and rubrics`；
2. `Compose allowlisted research workflows through typed artifacts`；
3. `Emit privacy-safe Harness telemetry`；
4. `Reconcile usage and enforce entitlements progressively`；
5. `Fork and replay only from side-effect-safe checkpoints`；
6. `Apply explicit Event and Artifact retention`；
7. `Restore Harness state and blobs from tested backups`；
8. `Gate workflow changes on measured quality regressions`；
9. `Record evidence-based scaling decisions`。

---

## 11. H7 — experimental sandbox, conditionally blocked

### 11.1 Status and hard gate

H7 当前状态必须是 **Blocked by Decision 9**。以下条件缺一不可：

- [ ] 产品所有者明确决定 Pharos 开始执行实验，而不仅记录实验；
- [ ] `DECISIONS.md` 以新决策正式 supersede Decision 9，并说明风险、范围和回退；
- [ ] `ROADMAP.md` 移除/改写“Executing experiments” non-goal；
- [ ] 完成独立 threat model、资源预算、数据/网络/secret contract；
- [ ] sandbox 不与 FastAPI API 容器、Zotero 文库或生产 secret 共享权限；
- [ ] 法律、许可证、云成本和滥用响应责任明确；
- [ ] evaluator freeze 与人工 approval 是不可绕过的技术 gate。

在这些条件满足前，只允许写文档、接口 mock 和 threat model；禁止合并能执行命令的生产代码。

### 11.2 Goal after unblocking

提供独立、一次性、最小权限的 Experiment Runner，执行冻结 Research Contract 中允许的命令，产出不可变
Observation/Result Artifact，由预先冻结的 evaluator 计算指标，再由用户决定 Keep / Discard / Pivot。

### 11.3 Required architecture

```text
Project Workflow
→ approved Research Contract
→ immutable experiment bundle
→ sandbox scheduler
→ isolated ephemeral runtime
→ typed logs/files/metrics Observation
→ frozen evaluator
→ Result Artifact
→ human decision
```

必须分离：

- Harness control plane：不执行 shell；
- Sandbox service：只接受签名/哈希匹配的 Experiment Action；
- Runtime：临时、非 root、资源受限、默认断网、无宿主挂载；
- Artifact store：只接收 allowlisted 输出、大小受限、病毒/格式检查；
- Evaluator：实验开始前冻结，Agent 无权修改；
- Human approval：代码、数据、命令、网络、GPU、预算变化后重新批准。

### 11.4 Backend deliverables after unblocking

- ResearchContract/ExperimentAction/Observation/Metric/Result strict schemas；
- bundle manifest、content hashes、allowed files/commands/env；
- resource limits：CPU、RAM、wall time、disk、process、GPU-hour 和 cost；
- default-deny egress，按 host/protocol approval；
- secret broker 只发 scoped short-lived credential，不写 artifact/log；
- sandbox queue、lease、heartbeat、kill 与 cleanup；
- immutable stdout/stderr tail、exit code、files、metrics；
- evaluator version/hash 与 primary metric freeze；
- no-result/timeout/OOM/cancel/preemption 的 typed outcome；
- abuse detection、global kill switch 和 operator runbook；
- 实验 Result 默认未验证，仍需人类判断。

### 11.5 Frontend deliverables after unblocking

- Research Contract diff 与审批；
- 明确展示代码、数据、命令、网络、资源、预算和停止条件；
- 实时但有背压的日志/指标；
- cancel/kill 状态与 cleanup；
- 基线/实验结果比较；
- Keep / Discard / Pivot 与理由；
- 不把一次成功运行描述成科学结论已成立。

### 11.6 Desktop deliverables after unblocking

- Desktop 只发起/查看/批准远程实验，不在 Zotero 进程内执行；
- 本地数据进入实验前经过 H5 Bridge 的单独 export approval；
- 不挂载用户 Zotero data directory；
- 本地附件和实验输出之间有 hash/provenance mapping；
- 实验故障不影响本地阅读和文库。

### 11.7 Tests and security gates

- container/VM escape threat review；
- fork bomb、disk fill、memory bomb、timeout、child process cleanup；
- default-deny network 与 DNS rebinding；
- secret exfiltration/redaction；
- path traversal/symlink/archive bomb；
- malicious output/MIME/size；
- evaluator tamper 和 metric gaming；
- cancel/host crash/preemption/retry，不重复计费或副作用；
- cross-user compute/artifact isolation；
- quota exhaustion/global kill switch；
- independent penetration/security review before public canary。

### 11.8 Rollout and rollback after unblocking

1. 本地 fake runtime；
2. CI ephemeral sandbox，无网络/secret/GPU；
3. operator-only tiny CPU jobs；
4. opt-in canary，固定 allowlisted template；
5. 按 workload 单独开放网络/GPU，不开放通用 shell workspace；
6. 默认保持 experiment flag off，直到安全和成本 soak 完成。

Rollback：关闭 experiment flag 与 sandbox scheduler、kill/cleanup 运行任务、撤销 scoped credentials、保留审计
Artifact 和 usage，不删除用户 Project 或伪造实验完成。

### 11.9 Exit gate after unblocking

- [ ] Decision 9 已正式 supersede；
- [ ] API 与 sandbox 完全分离且默认最小权限；
- [ ] evaluator freeze 无法被 Agent 绕过；
- [ ] 资源、网络、secret、artifact 和用户隔离测试通过；
- [ ] public canary 前独立安全评审通过；
- [ ] 计费、取消、cleanup 和事故 kill switch 演练通过；
- [ ] UI 不夸大实验结果的科学证据强度。

### 11.10 Non-goals

- 不在 API/desktop 进程执行 shell；
- 不提供无限网络、无限 GPU 或长期宠物容器；
- 不允许 Agent 修改 evaluator 后继续同一实验；
- 不自动把 Result/Claim 标 verified；
- 不自动完成从 Idea 到论文投稿的无人流水线；
- 不把 OpenHands/Codex 等通用 coding runtime 原样暴露给多租户用户。

### 11.11 Commit boundaries after unblocking

1. `Supersede the no-experiment decision with an explicit contract`；
2. `Define immutable research and experiment contracts`；
3. `Separate the sandbox control protocol from the API process`；
4. `Execute minimal jobs in an isolated default-deny runtime`；
5. `Freeze evaluators before experiment execution`；
6. `Account for resources and clean up every runtime`；
7. `Expose explicit experiment approvals and results`；
8. `Threat-test and canary the sandbox behind a kill switch`。

---

## 12. Global Definition of Done

任何阶段和整个 Harness program 都必须同时满足以下要求；“主要路径能跑”不够。

### 12.1 Architecture and contracts

- [ ] Workflow、Role、Capability、prompt、schema 和 evaluator 都有不可变版本/hash；
- [ ] deterministic spine 与 bounded agency 边界没有退化；
- [ ] Chat/Context 不被当作业务状态；
- [ ] Agent 不能动态增加工具、权限、预算或无限 fan-out；
- [ ] 新依赖符合 `HARNESS_LANDSCAPE.md` 的决策或有新 ADR。

### 12.2 Data correctness

- [ ] DB 是执行真相，重启恢复不依赖内存 Task；
- [ ] Run/Step/Attempt/Event/Artifact/Usage/Approval 状态一致；
- [ ] Artifact immutable，revision/lineage 可追；
- [ ] publish 通过领域 service、短事务和 idempotency key；
- [ ] 旧领域表仍是业务权威，没有双写竞争；
- [ ] fresh/upgrade/repeat/interrupted/restore migration 测试通过；
- [ ] backup 恢复和 blob reconciliation 有演练。

### 12.3 Reliability

- [ ] 双 worker claim 不重复；
- [ ] activation/writer/gates 只有一个 DB head，双 operator CAS 与 stale claim/publish 竞态不产生双 writer；
- [ ] lease、heartbeat、reaper、retry 和 abandoned 经过故障注入；
- [ ] pause/cancel/approval 在竞态下有确定结果；
- [ ] SSE 可重连/重放，polling 可保证正确性；
- [ ] Provider partial failure 不毁掉成功 sibling；
- [ ] 资源上限内长期 soak 无孤儿任务、无无界 queue、无永久 DB lock。

### 12.4 Security and privacy

- [ ] owner scope 覆盖每个 user-controlled/visible row、repository 和 endpoint；migration ledger、定义/config revision/head
  等 global row 是 system scope 且不经用户 API 暴露；
- [ ] system/user Run 隔离；
- [ ] deny > ask > allow，child 权限不超过 parent；
- [ ] API key/token/credential URL/stack/raw CoT/完整私有输入不进入 Event/Trace；
- [ ] SSRF、redirect、response size、timeout 和 output validation 生效；
- [ ] 管理员只看运维聚合，不看研究内容；
- [ ] 本地 Zotero/PDF 未经 approval 不离开设备；
- [ ] H0–H6 无 shell/实验执行路径。

### 12.5 Budget and entitlement

- [ ] 每个 Agent/Capability 有 wall time、attempt、fan-out、tool、token、cost 上限；
- [ ] Usage reserve/settle/release 守恒；
- [ ] official、BYOK、system_shared 分开；
- [ ] entitlement 由服务端执行，客户端不能绕过；
- [ ] Pharos usage ledger、Artifact 与领域 publish exactly-once；外部 vendor 调用只有在对方支持幂等/查询时才
  承诺自动重试，未知结果进入 `indeterminate`，再次调用前重新预算/确认并提示可能已有 vendor charge；
- [ ] 超预算进入明确 waiting/failed outcome，不输出伪完整结果。

### 12.6 Evidence and research integrity

- [ ] `metadata_only|abstract_only|unlocated|page` 强度不被模型升级；
- [ ] quote、human note、rule summary、model inference 明确区分；
- [ ] 自动 Artifact provenance 完整；
- [ ] `insufficient_evidence` / `search_incomplete` 是正常结果；
- [ ] 自动 ProjectArtifact 只为 draft，除非用户按当前领域规则确认；
- [ ] Claim、novelty、实验和写作描述不超过真实能力。

### 12.7 User experience

- [ ] 用户能看到当前 Step、等待原因、partial/error、预算和产物；
- [ ] 页面/客户端关闭和重开不丢 Run；
- [ ] 可暂停、取消、批准、拒绝和恢复；
- [ ] Web/Desktop 对同一 Run 的状态和证据语义一致；
- [ ] 降级/rollback 时有可解释提示，不出现空白页或永远 spinner；
- [ ] 不把基础设施 trace 强塞给普通用户。

### 12.8 Tests and evaluation

- [ ] unit、contract、integration、migration、fault-injection、security、load tests 通过；
- [ ] fake model/tool/clock 使 CI 不依赖真实 API；
- [ ] 不使用真实 Zotero 文库或用户 secret；
- [ ] 每个 Workflow 有冻结 eval set、rubric、阈值和 regression history；
- [ ] LLM 质量由事实性/覆盖/虚构/可用性评估，不只看 HTTP 200；
- [ ] 当前 frontend 和 desktop 回归 gate 通过。

### 12.9 Operations and rollout

- [ ] feature flags、shadow、canary、cutover、rollback 都已实际演练；
- [ ] health、metrics、告警和 runbook 可在不看研究内容的前提下定位故障；
- [ ] migration 与部署顺序记录；
- [ ] 旧路径只有在已发布客户端不依赖后才删除；
- [ ] 生产保护资产与其他服务未被触碰；
- [ ] 阶段报告包含真实命令、结果、known gaps 和 commit SHA。

### 12.10 Documentation and source history

- [ ] `ARCHITECTURE.md`、`DECISIONS.md`、`ROADMAP.md`、研究/Harness 文档与代码一致；
- [ ] Planned、Shadow、Canary、Done 不混用；
- [ ] API/schema/error/retention/运维文档齐全；
- [ ] 每个 commit boundary 可独立回退；
- [ ] 用户可见新能力按 `DECISIONS.md` 的版本规则发布；
- [ ] 不提交 secret、真实数据、构建缓存或无关修改。

---

## 13. Stop-the-line conditions

出现以下任一情况立即停止 cutover，关闭对应 writer/dispatcher，保留证据并进入修复：

- owner scope 或管理员隐私泄漏；
- 同一 Pharos 领域副作用重复执行、usage ledger 重复结算或重复导入；外部计费结果未知却被静默自动重试；
- migration 破坏旧数据或无法恢复；
- lease/restart 导致任务永久卡住；
- Event/Artifact 出现 API key、token、raw CoT 或未授权正文；
- abstract-only 内容被展示成全文/page evidence；
- Agent 绕过 tool/policy/approval/budget；
- system Daily 泄漏用户方向或将个性化结果全局共享；
- Desktop Bridge 未经批准读取/上传本地文件；
- H0–H6 出现可执行 shell/实验路径；
- 资源使用威胁同机服务稳定性。

事故修复必须新增 regression test；不得只清数据、重启服务后继续放量。

## 14. Recommended first implementation slice

交给实现 Agent 的第一批工作只应是 **H0 + H1 的 backend canary**，不要一次要求完成 H0–H7：

1. 完成 migration runner 与 H0 contracts；
2. 达到 H0 code gate；生产 operational gate 由 operator 另行完成；
3. 创建 H1 schema/state/repository；
4. 加 dispatcher/lease/reaper；
5. 加 Event/Artifact/Approval/Usage；
6. 运行 fake canary 和故障注入；
7. 最后才加最小 API/Run Center；
8. 达到 H1 code gate 后停止并报告 `H1_CODE_COMPLETE_AWAITING_CANARY`；只有 operator 完成生产 canary、
   72 小时 soak 与回滚演练后才可把 H1 标为 Done，再由下一轮单独进入 H2。

这个切片故意没有真实 Agent 能力。先证明“状态不会丢、权限不会越、错误可以恢复、副作用不会重做”，再让
模型参与科研判断，才符合整个 Harness 的建设顺序。
