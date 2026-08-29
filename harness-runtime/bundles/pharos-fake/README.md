# `pharos-fake-dsh`

This is a deterministic, CI-only DeepSeek Harness bundle for the Pharos H1.5
canary. It registers exactly one provider (`pharos-fake`) and one model
(`pharos-fake-canary`). Every successful call emits the same valid text stream
containing the `harness.canary` output contract and fixed usage values.

The bundle deliberately has no network, filesystem, environment, clock,
randomness, subprocess, tool, skill or subagent access. It is not a real model
provider and must never be presented as one. It exists to exercise the DSH
wire, process lifecycle, schema validation and Pharos accounting without an API
key or external side effect.

The checked-in `index.js` is the reviewed runtime artifact and `src/index.ts`
is its typed counterpart. Both files are content-addressed by the Pharos
runtime policy. The package uses only exact-version peer
dependencies supplied by the installed DSH profile; it does not use
`workspace:*`, modify `vendor/deepseek-harness`, or execute an install-time
build script. Install the package into a disposable profile with `dsh plugin`
and apply `harness-runtime/profile/pharos-safe.cordis.patch.yml` last:

```sh
dsh plugin --profile sdk add file:/absolute/path/to/harness-runtime/bundles/pharos-fake
dsh --profile sdk \
  --patch /absolute/path/to/harness-runtime/profile/pharos-safe.cordis.patch.yml
```

The `security-policy.json` entry and `check-profile.py` audit are the policy
allowlist for the inserted `llm-pharos-fake` row. Do not add another provider
or a home-level patch that re-enables an upstream provider. This bundle is not
an installation path for a production model or a user-supplied plugin.
