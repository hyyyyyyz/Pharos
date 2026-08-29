# Phase report — DeepSeek Harness official-wire boundary

> Date: 2026-08-30
> State: **H1.5 wire code gate complete; dormant in product execution**
> Implementation commit: `ae135a9a` (`Add the bounded official Harness wire transport`)
> CI-boundary hardening commit: `efa007dc` (`Stabilize the Harness wire CI boundary`)
> Production activation: **disabled**

> **This is not the H1.5 exit gate and does not make H1 operationally passed.**

This report records the first Pharos-to-DeepSeek-Harness execution boundary that is backed by
code, negative tests and a real Loader canary. It is evidence for one H1.5 milestone, not a
claim that the Agent runtime is production-ready.

## 1. What this milestone proves

Pharos now has a single-use parent transport intended for one Attempt that can launch the pinned DSH SDK runtime,
initialize one exact provider/model route, submit one text prompt, validate the official SDK
event chain, collect sanitized output and usage, perform the official shutdown handshake, drain
EOF, terminate descendants when necessary and reap the process before returning a sanitized
candidate eligible for later validation and durable persistence.

The real canary composes the vendored Loader with the external `pharos-fake` bundle and the
no-tool Pharos profile. It does not replace DSH with a protocol-only mock. The same canary is run
by `.github/workflows/harness-runtime.yml` through the Pharos parent-side `AttemptTransport` class. The 1:1
binding to a durable DB Attempt/owner is deliberately still pending.

The milestone deliberately leaves the product route unchanged:

- `HarnessApp` and `StepExecutor` still use the in-process deterministic `FakeModelGateway`;
- no business workflow opens a DSH process;
- `agent_runtime_enabled` remains a distinct default-off gate;
- no real model provider is enabled by this commit;
- no raw SDK transcript, prompt, system text or reasoning block is returned as a publishable
  `PromptOutcome`.

## 2. Delivered boundary

### 2.1 Fixed official wire

The parent admits only the pinned official SDK methods:

- requests: `initialize`, `session/prompt`, `shutdown`;
- notifications: `session.event`, `session.status`;
- upstream subagent notifications are parsed and then rejected because subagents are disabled.

The safe SessionEvent vocabulary is fixed to:

- `agent/inbox/spliced`;
- `turn/start`, `turn/end`;
- `step/start`, `step/end`;
- `user/message`, `assistant/message`, `assistant/chunk`;
- `model/selection`;
- `request/header`, `request/context`;
- `session/title` with the pinned fallback-title shape.

Unknown methods, notification fields, event types, content blocks, tool calls, subagents,
replacement surface operations and future upstream extensions fail closed.

### 2.2 Causal success contract

A successful fresh Attempt must prove the following causal chain, not merely receive an idle
status:

1. the exact direct-user inbox receipt is the first event;
2. status becomes `running`;
3. one matching turn starts and consumes that receipt once;
4. one matching step starts;
5. the surface user message equals the submitted prompt;
6. optional title/model-selection events cite the correct earlier event and route;
7. one tool-less request header and context match the initialized provider/model and explicit
   reasoning/token settings;
8. typed stream chunks close every block, report consistent usage and end with a valid finish;
9. one assistant message is bound to the complete stream by `sourceEventSeqs`;
10. the step and turn end once, then status becomes `idle`;
11. official `shutdown` returns `{}`, the child reaches EOF, exits with code 0, leaves no process
    group descendants and is reaped;
12. only then may the sanitized candidate return to the caller.

An exit code other than 0 after a syntactically valid shutdown response is a process failure and
cannot publish a result. Delayed output or a delayed notification after idle is caught during the
shutdown/EOF closure and also cannot publish.

### 2.3 Accounting and non-success evidence

`TokenUsage` follows the pinned upstream disjoint-count semantics:

- `inputTokens` excludes `cacheReadTokens` and `cacheWriteTokens`;
- a supplied total cannot be smaller than known disjoint input, cache and output counts;
- when both cache buckets are present, an exact total must equal all known disjoint counts;
- `reasoningTokens` cannot exceed `outputTokens`;
- all counts and aggregates remain within the JavaScript safe-integer range.

Provider finish-error and finish-aborted paths are accepted only with the official mapping to a
`turn/end` error carrying the same typed failure. They do not require a synthesized
`assistant/message`. A `HarnessTurnError` removes provider-controlled message text but retains:

- the turn classification and safe provider code/status/request identifier fields;
- the prompt message ID;
- validated usage when the runtime emitted it;
- text-only partial output when it can be causally reconstructed;
- delivery evidence (`acknowledged`).

The transport also exposes monotonic pre-integration delivery evidence:

- `not_started`: prompt write has not begun;
- `unknown`: a write began and may be partial;
- `sent`: the complete prompt frame was written but no exact receipt is proven;
- `acknowledged`: an exact direct-user inbox receipt was validated.

The next gateway/runner milestone must persistently map any possibly delivered nonterminal failure
to `indeterminate`; it must not retry it as though no provider call occurred.

### 2.4 Resource, process and privacy boundaries

The implementation enforces:

- one child process group per single-use transport; durable Attempt/owner binding remains pending;
- absolute executable, canonical existing cwd, exactly one fixed profile, explicit immutable
  provider/model allowlist and copied immutable argv/env policy;
- explicit environment allowlist, bounded entries, no NUL bytes or invalid environment names;
- bounded initialize/prompt/idle/shutdown/TERM/KILL/reap phases;
- bounded frame, buffer, JSON depth, raw SessionEvent bytes, event count, output and stderr;
- raw wire-byte accounting, so JSON whitespace cannot bypass the total event budget;
- duplicate-key rejection and strict JSON-RPC envelopes;
- UTF-8 and length bounds for product-facing text/identifiers;
- TERM → KILL → process-group verification → reap, including a leader that exits before its
  descendants;
- explicit closure of stdin/stdout/stderr after successful or failed cleanup;
- stderr diagnostics as byte count plus SHA-256 only, never raw child text;
- sanitized `PromptOutcome` containing only message ID, usage, text output and acknowledged
  delivery state.

This is a POSIX parent-process boundary. Container/cgroup isolation, a Windows process-tree
equivalent and production egress enforcement remain deployment gates.

## 3. Evidence recorded on this milestone

The implementation regression commands were run at `ae135a9a`; the isolated wire suite, profile
checks and remote workflow were then rerun at `efa007dc`, whose only additional changes harden the
CI/test boundary. The working tree contained only this subsequent documentation update.

| Evidence | Command | Result |
|---|---|---:|
| Isolated wire contract, lifecycle, resource and process tests at `efa007dc` / remote CI | `cd backend && python -m pytest --noconftest -q tests/harness/test_dsh_transport.py` | 97 passed |
| Harness subsystem regression at `ae135a9a` | `backend/.venv/bin/pytest -q backend/tests/harness` | 213 passed |
| Full backend regression at `ae135a9a` | `cd backend && .venv/bin/pytest -q` | 1180 passed, 1 skipped, 1 xfailed |
| Static lint | `backend/.venv/bin/ruff check backend/pharos/harness/protocol.py backend/pharos/harness/transport.py backend/tests/harness/test_dsh_transport.py backend/tests/harness/fixtures/fake_dsh_runtime.py harness-runtime/scripts/run-fake-canary.py` | passed |
| Static typing | `backend/.venv/bin/mypy backend/pharos/harness/protocol.py backend/pharos/harness/transport.py` | passed |
| Real Loader + safe profile + external fake bundle | `PYTHONPATH=backend backend/.venv/bin/python harness-runtime/scripts/run-fake-canary.py --attempt-transport` | passed |
| Vendored-source integrity | `scripts/check-deepseek-harness-vendor.sh` | passed at `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| Remote pinned-source/wire/Loader/SDK workflow | [`Validate Harness runtime source`](https://github.com/hyyyyyyz/Pharos/actions/runs/33276646427) at `efa007dc` | passed |
| Patch hygiene | `git diff --check` | passed |

The focused tests include malformed/duplicate/oversized frames, wrong session/receipt/route,
unsafe event/content vocabulary, raw-whitespace amplification, inconsistent usage, missing
assistant/usage, all admitted non-success reasons, delayed post-idle output, nonzero shutdown,
timeouts, stderr overflow, partial lifecycle, process-group orphans, stale PGID protection,
descriptor closure and mutable launch-policy inputs.

The pinned source identity is `cd5ef8148158c3a752a658978873241fdf8e2bbc`; the reviewed runtime inputs are
`harness-runtime/profile/pharos-safe.cordis.patch.yml`, `harness-runtime/security-policy.json`
and the external `harness-runtime/bundles/pharos-fake/` package. Production runtime/profile hashes are
schema slots only at this stage and are not yet written by a DB-bound handle. The linked remote run
completed successfully; that verifies the checked-in CI closure, not production isolation or a
product DSH route.

## 4. What is still required before a product route

The next H1.5 milestone must not be skipped. It must deliver:

1. a `GatewayFactory` that opens exactly one `GatewayHandle` per claimed Attempt instead of sharing
   the global gateway object;
2. owner-scoped active-handle registration and Attempt CAS so late child results cannot overwrite
   a terminal state;
3. immutable runtime provenance written before provider dispatch: upstream commit, runtime/package
   hash, profile/policy/config hash, protocol version, session ID, PID and deadline;
4. active cancel and deadline coordination with bounded shutdown/TERM/KILL/reap;
5. persistent delivery-state mapping and restart reconciliation; possibly delivered cancel,
   timeout, crash or cleanup failure must become `indeterminate` and must not auto-retry;
6. reserve/settle/reconcile usage conservation for successes and non-successes;
7. immutable validated Artifact creation before Step/Run reduction;
8. a claim → DSH fake adapter → Artifact/usage → reducer canary through the actual durable kernel;
9. a factory-owned immutable argv/profile/patch closure, private `HOME`/`DSH_HOME`, verified hashes
   and no user-controlled profile or plugin injection;
10. Linux production packaging, container/cgroup and egress policy, SBOM/license evidence, RSS/CPU/
    disk ceilings, operator canary, rollback drill and 72-hour soak.

Only after those gates pass may a business workflow route an Agent Step to DSH. Daily Papers,
Literature Discovery and Research Projects remain unchanged by this milestone.

## 5. Rollback and operational meaning

This milestone is dormant code plus CI coverage. Rolling these commits back does not require a database rollback
and does not change existing Runs because no product route imports it. The durable schema fields and
`agent_runtime_enabled` gate were added additively in earlier commits and remain safe with the gate
off.

If a later integration regresses, the first rollback action is to publish a new configuration
revision with `agent_runtime_enabled=false`, stop new DSH claims and preserve all Run, Attempt,
Event, Artifact and Usage evidence for reconciliation. Do not delete Attempt rows or usage
reservations to make a failed rollout appear clean.
