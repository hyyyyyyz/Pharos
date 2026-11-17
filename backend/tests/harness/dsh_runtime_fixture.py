"""Small canonical provisioned-runtime fixture shared by DSH kernel tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path, *, prefix: str = "") -> str:
    records = [
        {
            "path": f"{prefix}{path.relative_to(root).as_posix()}",
            "sha256": _file_hash(path),
        }
        for path in sorted(
            (candidate for candidate in root.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )
    ]
    return hashlib.sha256(_canonical(records)).hexdigest()


def make_provisioned_runtime(base: Path) -> dict[str, Any]:
    """Create one read-only version-1 runtime with real content hashes."""

    root = base / "prepared-runtime"
    template = root / "template"
    profile = template / "profiles/sdk"
    bundle = template / "bundles/pharos-fake"
    profile.mkdir(parents=True)
    bundle.mkdir(parents=True)
    lock = profile / "pnpm-lock.yaml"
    lock.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (profile / "package.json").write_text('{"private":true}\n', encoding="utf-8")
    (bundle / "index.js").write_text("export const canary = true\n", encoding="utf-8")
    effective = template / "effective-config.yml"
    effective.write_text("- id: sdk-jsonrpc-server\n", encoding="utf-8")
    patch = template / "pharos-safe.cordis.patch.yml"
    patch.write_text("- id: tool-bash\n  disabled: true\n", encoding="utf-8")

    node = base / "node"
    cli = base / "dsh-cli.js"
    node.write_text("fixture-node\n", encoding="utf-8")
    cli.write_text("fixture-cli\n", encoding="utf-8")
    node.chmod(0o755)
    cli.chmod(0o444)

    for current, directories, files in os.walk(template, topdown=False):
        for name in files:
            (Path(current) / name).chmod(0o444)
        for name in directories:
            (Path(current) / name).chmod(0o555)
    template.chmod(0o555)

    source_policy_hash = "c" * 64
    policy_hash = _file_hash(patch)
    profile_hash = _tree_hash(profile)
    payload = {
        "manifest_version": 1,
        "kind": "pharos.dsh.runtime-manifest",
        "provisioner_version": "1",
        "profile": "sdk",
        "node": {"version": "v22.19.0", "sha256": _file_hash(node)},
        "node_version": "v22.19.0",
        "node_sha256": _file_hash(node),
        "cli_sha256": _file_hash(cli),
        "upstream_commit": "d" * 40,
        "policy_sha256": source_policy_hash,
        "protocol_sha256": "e" * 64,
        "profile_patch_sha256": policy_hash,
        "lock_sha256": _file_hash(lock),
        "profile_sha256": profile_hash,
        "bundle_sha256": _tree_hash(bundle),
        "effective_sha256": _file_hash(effective),
        "template_sha256": _tree_hash(template),
        "effective_path": "template/effective-config.yml",
        "tree_sha256": _tree_hash(template, prefix="template/"),
    }
    manifest = root / "runtime-manifest.json"
    raw = _canonical(payload)
    manifest.write_bytes(raw)
    manifest.chmod(0o444)
    root.chmod(0o555)
    return {
        "root": root,
        "template": template,
        "patch": patch,
        "manifest": manifest,
        "node": node,
        "cli": cli,
        "runtime_hash": hashlib.sha256(raw).hexdigest(),
        "profile_hash": profile_hash,
        "policy_hash": policy_hash,
        "upstream_commit": "d" * 40,
    }
