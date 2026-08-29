# Pharos safe Harness runtime

This directory is a reviewable P1/P2 configuration slice for the official
DeepSeek Harness `sdk` profile. It disables telemetry, shell and
subprocess execution, sandbox backends, filesystem/search/editor tools,
filesystem skill discovery, agent instructions, web search/fetch, workflow and
job controls, subagents, Ralph, todo/goal/plan/command controls, mutable
settings/credentials, built-in provider adapters and retries, and any
matching rows added by an upstream revision until the policy is updated.

The overlay is applied after the profile with the supported launcher:

```sh
env -i PATH="$PATH" HOME="$TMP_HOME" DSH_HOME="$TMP_DSH_HOME" \
  dsh --profile sdk \
  --patch /absolute/path/to/Pharos/harness-runtime/profile/pharos-safe.cordis.patch.yml
```

The overlay is an explicit last patch layer. Cordis overlays the fields present
in a patch row; it does not erase omitted upstream fields, which is why the
effective-config smoke must prove every denied row ends with `disabled: true`.
Do not put another patch after it,
and do not use a home-level or profile patch that re-enables a disabled row.
The process boundary is JSON-RPC over stdio only: this slice does not open a
public network port. The SDK and agent loop, LLM registration seam, session
JSONL and bounded runtime persistence remain available. The first executable
profile must register only the deterministic `pharos-fake` adapter; a later
provider adapter is a separate reviewed overlay and deployment policy.

The upstream SDK server can dynamically mount its DeepSeek fallback when its
trusted parent sends `initialize(provider="deepseek-official")`. A profile
overlay cannot remove that code path. The Pharos parent must therefore reject
every provider except its phase allowlist, and production isolation must enforce
network egress independently. This directory is a fail-closed configuration
gate, not an OS sandbox or a complete production boundary.

`sdk-minimal` is a separate standalone tree and is unsafe as shipped. Its
default configuration includes danger-full-access policy, local subprocess and
filesystem providers, persistent Bash/PowerShell, and an editor. The safe
overlay is scoped to the official base-backed `sdk` profile; do not describe an
unmodified `sdk-minimal` launch as safe. `security-policy.json` audits both
trees so that a new dangerous row in either tree fails review. The
`patch_required: false` minimal-only rows need a separately reviewed minimal
profile overlay before that profile can be used safely.

Run from a disposable, dedicated current working directory. Set `DSH_HOME` to
an isolated temporary directory; it owns sessions, attachments, settings, and
other runtime state. The SDK session workspace is otherwise derived from the
invoking cwd, so pin the caller's workspace to the same disposable directory.
Never inherit an arbitrary parent environment. Construct an allowlist such as
`PATH`, `HOME`, and `DSH_HOME`; add a model credential only for an intentional
model test, and keep credentials out of patches, logs, and the workspace.

The static checker is dependency-free and does not evaluate YAML `!!js`
expressions. After building the vendored runtime, also audit the exact output of
`dsh --dump-config`:

```sh
python3 harness-runtime/scripts/check-profile.py
python3 -m unittest discover -s harness-runtime/tests -v
node vendor/deepseek-harness/apps/cli/lib/bin.js --profile sdk \
  --patch "$PWD/harness-runtime/profile/pharos-safe.cordis.patch.yml" \
  --dump-config > /tmp/pharos-dsh-effective.yml
python3 harness-runtime/scripts/check-profile.py \
  --effective-config /tmp/pharos-dsh-effective.yml
```

Before changing either upstream bundle, update the policy's pinned vendor
revision only after reviewing every changed row. The checker must fail when a
new dangerous keyword-matching row is not explicitly denied.
