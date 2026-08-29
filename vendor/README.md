# Vendored dependencies

## DeepSeek Harness

Pharos carries a source snapshot of
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness)
under `vendor/deepseek-harness/`.

- Upstream revision: `cd5ef8148158c3a752a658978873241fdf8e2bbc`
- Upstream version at that revision: `0.1.2-alpha.1`
- Upstream license: MIT
- Snapshot date: 2026-08-30

The imported directory deliberately has no nested `.git` directory. The
upstream `LICENSE`, `THIRD_PARTY_NOTICES.md`, package manifests, lockfiles and
per-package notices remain part of the snapshot.

Refresh the snapshot only through:

```bash
scripts/vendor-deepseek-harness.sh
```

The script pins and verifies the full upstream commit before copying the source
and removes only the copied checkout's Git metadata. Update the revision in the
script and this manifest in the same reviewed commit.

Vendoring does not make every upstream plugin a Pharos capability. Production
profiles are allowlisted in Pharos code. Shell, general filesystem access,
subprocesses, arbitrary MCP servers, self-modification and untrusted plugins
remain disabled unless a later security decision explicitly introduces them.
