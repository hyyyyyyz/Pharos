# Pharos Research Harness — H0/H1 kernel stage report

> 状态：**H0 code gate 通过（operational gate 待 operator）；H1 code complete, awaiting canary.**
> 本报告只描述已提交、已测试的代码与 fixture 证据。生产数据库副本恢复、staging
> 连续重启/kill/SSE 断线、production operator canary、72 小时 soak 与回滚演练属于
> operator operational gate，未执行；在取得这些证据前 H1 不得标 Done，H2 不得开始。

## 1. 实际交付与计划差异

### H0（contracts / migrations / test foundation）

| 交付 | 状态 |
| --- | --- |
| 显式编号化 migration runner（ledger、checksum、单 `BEGIN IMMEDIATE` 原子批次） | ✅ `backend/pharos/db/migrations.py` |
| 旧库 fixture（由 9afc1fb 发布 schema contract 确定性生成、只含合成数据） | ✅ `backend/tests/harness/fixtures/{generate_legacy_fixture.py,legacy-schema-v0.sqlite}` |
| `backend/pharos/harness/` package（contracts / definitions / registry / configrev / fakes / seams） | ✅ |
| feature flag contract（DB head 单真相、bootstrap env、deny-only emergency stop） | ✅ H0 契约 + H1 DB 实现 |
| migration CLI（`python -m pharos.db.migrations status|upgrade|verify`） | ✅ |

**差异（已记录）**：计划 §3.4 要求 ledger + revisions + FTS 在同一启动事务内。实际顺序为
legacy `create_all`/兼容列（SQLAlchemy engine）→ migration 批次（ledger + 全部 revision，
同一 `BEGIN IMMEDIATE` 事务，原子）→ FTS。ledger 与 revisions 保持单事务批次；legacy
bootstrap 未迁入该事务以最小化对现有启动路径的改动（主提示词允许的最小分离路径）。

### H1（durable kernel + canary）

| 交付 | 状态 |
| --- | --- |
| 15 张 harness 表（revision 0002–0007），ORM 镜像与 DDL 一致性测试钉死 | ✅ |
| `HarnessStateService` 唯一转换权威 + 穷举测试；`blocked`/终态冻结 | ✅ |
| owner-scoped repositories；DB 复合 FK 同 scope 约束 | ✅ |
| 配置 revision / routes / singleton head + expected-revision CAS | ✅ |
| 幂等 Run 创建（同 key 同 input 回放；同 key 异 input 409） | ✅ |
| dispatcher claim（单条件 UPDATE）、lease/heartbeat、reaper、retry 队列 | ✅ |
| 双 worker claim race（文件型 SQLite、双线程、100 轮） | ✅ |
| bounded runner + 依赖感知就绪 + 确定 reduction + pause/resume/cancel | ✅ |
| canary workflow `harness.canary@1`（success/retry/terminal/approval/mapped/agent） | ✅ |
| Event cursor replay + fetch-based SSE（不持 request session） | ✅ |
| Approval request/decide/expire/consume（同 hash 单次消费） | ✅ |
| Usage reserve/settle/release 守恒 | ✅ |
| 公开 release/projection 表与最小 service（H3 接 daily） | ✅ 表 + service + 测试 |
| Run Center（web） | ✅ 最小但真实 |
| Desktop dormant transport | ✅ 无 UI、无 canary 入口 |

**未实现（按计划属于后续阶段）**：H2/H3/H4 业务迁移、Local Capability Bridge、实验执行、
`fork`、任意 Workflow 编辑器、Run Center 之外的桌面 UI。真实 Model Gateway（HTTP）未接：
H1 唯一实现是 deterministic fake；canary 因此构造上无法花真钱。

## 2. Migration revisions

| revision | 内容 |
| --- | --- |
| `0001_schema_ledger` | migration ledger |
| `0002_harness_definitions_config` | workflow versions + config revisions/routes/head |
| `0003_harness_runs` | runs（scope 幂等键、config_revision 固化） |
| `0004_harness_steps_attempts` | steps + attempts（复合 scope FK、唯一身份） |
| `0005_harness_events` | append-only events（AUTOINCREMENT cursor） |
| `0006_harness_artifacts_links_releases` | artifacts/links/public releases/projections |
| `0007_harness_approvals_schedules_usage` | approvals/schedules/usage ledger |

## 3. 测试证据

```text
命令: cd backend && .venv/bin/pytest
结果: 1053 passed, 1 skipped, 1 xfailed（含 85 项 Harness 专项）
      ruff check pharos/ tests/ 干净；mypy pharos/harness 无问题
```

Harness 专项矩阵：

- migration：fresh / repeat / checksum mismatch / unknown revision / interrupted
  rollback / fixture upgrade（6/6）；
- registry：cycle、重复 key、缺失依赖、无界 fan-out/budget、allowlist 越权、非幂等
  publish/retry、approval 无 reject 分支、hash 跨进程稳定；
- 状态机：全部合法转换 + 全部非法转换穷举；
- 双 worker claim：100 轮 ×2（单步恰好一个胜者 / 队列无重叠）；
- 故障注入：副作用前/后崩溃、外部结果不明 `indeterminate` + usage release、超大事件
  拒绝、prompt injection 不改 catalog、重启恢复；
- 配置：双 operator CAS 只一成功且败者无残留、env 不覆盖已有 head、emergency stop
  deny-only、Decision 9 永久 deny、rollback revision；
- 兼容：`test_app_routes` 全过（含新 harness router）；旧 API 行为不变（flag 关闭时
  Harness 不启动 loop、旧表语义零改动）；frontend `tsc -b` + build + 41 vitest；
  desktop `pharosHarness` 5/5。

已知基线失败：无（全套后端测试与改动无关的失败为 0）。

## 4. Feature flags 当前值

bootstrap 默认（新库，无 head）：`harness_enabled=0`，全 gate 关，业务 route 均
`disabled + legacy`；canary route `disabled`。启用 canary 只经 operator revision
（`/api/harness/operator/config/apply`）。`PHAROS_HARNESS_EMERGENCY_STOP=1` 为唯一
DB 外覆盖，deny-only。生产默认未启用任何 Harness 能力。

## 5. 安全与隐私检查

- 所有用户可见行/端点 owner-scoped；越权与不存在统一 404（kernel + API 测试）；
- secret、stack、raw CoT 不入 Event/Artifact（无此字段；payload 上限强制）；
- 管理员只有 operator status/validate/apply（聚合 gate 状态 + 快照校验），无内容路径；
- 无 shell、无任意 URL、无 Zotero 访问；capability 目录由受信代码编译；
- 测试全程离线（fake clock/model/capability），不碰真实 `.env`/生产库/真实 Zotero。

## 6. 回滚

- 已演练：operator rollback revision（原子切回安全默认）+ stale writer fencing 测试 +
  emergency stop deny 测试 + migration 批次 rollback（故障注入）。
- 未演练（operator gate）：生产数据库副本 backup/restore、旧 image 启动验证、
  staging 连续重启/kill/SSE 断线。

## 7. 未完成项与进入下一阶段的证据

| 项 | 归属 |
| --- | --- |
| H0 Done：operator 隔离副本 verify/upgrade/backup/restore 报告 | operator |
| H1 Gate：production operator canary（禁真实模型）、72h soak、rollback 演练 | operator |
| H2 Literature Discovery 纵向迁移 | 下一实现轮（禁止提前开始） |

## 8. Commit SHA

```text
aae6da2 Introduce explicit backend schema revisions
e1bc476 Define versioned Harness contracts, definitions and deterministic fakes
171f796 Create the durable Harness schema and enforce state transitions
3151cc3 Claim steps with leases and run the kernel through a deterministic canary
61e4f99 Expose the owner-scoped Harness API behind the config gates
854e019 Add the web Run Center for durable research runs
671526d Add the dormant desktop Harness transport
（fault-injection 与文档提交随最后 gate 提交）
```

未推送；是否推送按调用者当次明确指令。
