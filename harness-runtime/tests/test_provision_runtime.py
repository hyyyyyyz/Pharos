#!/usr/bin/env python3
"""Offline tests for the prepared DSH runtime provisioner."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "harness-runtime/scripts/provision-runtime.py"
SPEC = importlib.util.spec_from_file_location("provision_runtime", SCRIPT)
assert SPEC and SPEC.loader
PROVISION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROVISION)


class OfflineRunner:
    """Simulate DSH's three build commands without a network or CLI process."""

    def __init__(self, bundle: Path) -> None:
        self.bundle = bundle
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        env = kwargs["env"]
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "v22.19.0\n", "")
        if "plugin" in argv:
            profile = Path(env["DSH_HOME"]) / "profiles/sdk"
            profile.mkdir(parents=True)
            (profile / "package.json").write_text(
                json.dumps(
                    {
                        "name": "dsh-profile-sdk",
                        "private": True,
                        "dependencies": {
                            "pharos-fake-dsh": f"file:{self.bundle}",
                        },
                        "dsh": {"profile": {"bundles": ["pharos-fake-dsh"]}},
                    }
                ),
                encoding="utf-8",
            )
            (profile / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
            (profile / "pnpm-workspace.yaml").write_text("packages:\n  - .\n", encoding="utf-8")
            (profile / "cordis.yml").write_text("[]\n", encoding="utf-8")
            (profile / "pnpm-lock.yaml").write_text(
                """lockfileVersion: '9.0'

importers:
  .:
    dependencies:
      pharos-fake-dsh:
        specifier: file:%s
        version: file:../../../../Users/build/fake

packages:
  pharos-fake-dsh@file:../../../../Users/build/fake:
    resolution: {directory: ../../../../Users/build/fake, type: directory}
""" % self.bundle,
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "dump-config" in argv:
            return subprocess.CompletedProcess(argv, 0, "- id: llm\n  name: dsh-llm\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


class ProvisionRuntimeTests(unittest.TestCase):
    def test_canonical_json_and_tree_hash_are_stable(self) -> None:
        self.assertEqual(
            PROVISION.canonical_json({"z": 1, "a": "x"}),
            b'{"a":"x","z":1}\n',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b").write_text("b", encoding="utf-8")
            (root / "a").write_text("a", encoding="utf-8")
            first = PROVISION.tree_hash(root)
            (root / "a").chmod(0o600)
            self.assertEqual(first, PROVISION.tree_hash(root))

    def test_path_symlink_and_secret_audits_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "secret.txt").write_text("API_KEY = 'not-a-value'", encoding="utf-8")
            (root / "path.txt").write_text("/Users/developer/project", encoding="utf-8")
            errors = PROVISION.audit_tree(root)
            self.assertTrue(any("secret-like" in error for error in errors), errors)
            self.assertTrue(any("absolute path" in error for error in errors), errors)
            (root / "uri.txt").write_text(
                "file:/Users/developer/project file:///Users/developer/project file:C:\\\\Users\\\\developer",
                encoding="utf-8",
            )
            self.assertTrue(any("absolute file URI" in error for error in PROVISION.audit_tree(root)))
            (root / "link").symlink_to(root / "path.txt")
            self.assertTrue(any("symlink" in error for error in PROVISION.audit_tree(root)))
            if hasattr(os, "mkfifo"):
                with tempfile.TemporaryDirectory(dir=root) as special_directory:
                    os.mkfifo(Path(special_directory) / "fifo")
                    self.assertTrue(
                        any("special file" in error for error in PROVISION.audit_tree(Path(special_directory))),
                    )

    def test_provision_is_offline_canonical_and_read_only(self) -> None:
        node = Path(sys.executable).resolve()
        cli = ROOT / "vendor/deepseek-harness/apps/cli/lib/bin.js"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime"
            runner = OfflineRunner(ROOT / "harness-runtime/bundles/pharos-fake")
            manifest_path = PROVISION.provision(ROOT, node, cli, output, runner=runner, prove_canary=False)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "pharos.dsh.runtime-manifest")
            self.assertEqual(manifest["node_version"], "v22.19.0")
            self.assertEqual(PROVISION.audit_tree(output), [])
            PROVISION.verify_manifest(output)
            self.assertTrue(all(not kwargs["env"].get("DEEPSEEK_API_KEY") for _, kwargs in runner.calls))
            plugin = next(argv for argv, _ in runner.calls if "plugin" in argv)
            self.assertIn("--offline", plugin)
            self.assertTrue(all(not (path.stat().st_mode & stat.S_IWUSR) for path in output.rglob("*")))
            self.assertFalse(any(path.is_symlink() for path in output.rglob("*")))
            self.assertTrue((output / "template/pharos-safe.cordis.patch.yml").is_file())
            self.assertIn("file:../../bundles/pharos-fake", (output / "template/profiles/sdk/package.json").read_text())

    def test_tampering_changes_verified_hash(self) -> None:
        node = Path(sys.executable).resolve()
        cli = ROOT / "vendor/deepseek-harness/apps/cli/lib/bin.js"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime"
            PROVISION.provision(
                ROOT,
                node,
                cli,
                output,
                runner=OfflineRunner(ROOT / "harness-runtime/bundles/pharos-fake"),
                prove_canary=False,
            )
            target = output / "template/effective-config.yml"
            target.chmod(0o644)
            target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            with self.assertRaises(PROVISION.ProvisionFailure):
                PROVISION.verify_manifest(output)

    def test_bundle_copy_rechecks_policy_hash_before_copy(self) -> None:
        source = ROOT / "harness-runtime/bundles/pharos-fake"
        policy = json.loads((ROOT / "harness-runtime/security-policy.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bundle"
            path = source / "index.js"
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"tampered")
                with self.assertRaises(PROVISION.ProvisionFailure):
                    PROVISION._copy_bundle(source, target, policy)
            finally:
                path.write_bytes(original)

    def test_bundle_copy_rechecks_policy_hash_after_copy(self) -> None:
        source = ROOT / "harness-runtime/bundles/pharos-fake"
        policy = json.loads((ROOT / "harness-runtime/security-policy.json").read_text())
        original_copy = PROVISION._copy_regular

        def copy_then_tamper(source_path: Path, target_path: Path) -> None:
            original_copy(source_path, target_path)
            if target_path.name == "index.js":
                target_path.write_bytes(target_path.read_bytes() + b"tampered")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(PROVISION, "_copy_regular", copy_then_tamper):
                with self.assertRaises(PROVISION.ProvisionFailure):
                    PROVISION._copy_bundle(source, Path(directory) / "bundle", policy)


if __name__ == "__main__":
    unittest.main()
