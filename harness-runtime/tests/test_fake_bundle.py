#!/usr/bin/env python3
"""Contract tests for the out-of-tree deterministic DSH adapter bundle."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import importlib.util


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "harness-runtime/bundles/pharos-fake"
CHECKER_PATH = ROOT / "harness-runtime/scripts/check-profile.py"
SPEC = importlib.util.spec_from_file_location("pharos_safe_checker", CHECKER_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class FakeBundleTests(unittest.TestCase):
    def test_bundle_is_explicitly_allowlisted(self) -> None:
        self.assertEqual(CHECKER.check_fake_bundles(ROOT), [])

    def test_runtime_stream_is_deterministic_and_tool_free(self) -> None:
        """Exercise the checked-in JS artifact through the built DSH LLM seam."""
        vendor_node_modules = ROOT / "vendor/deepseek-harness/node_modules/.pnpm/node_modules/@deepseek-ai"
        dsh_llm = ROOT / "vendor/deepseek-harness/packages/llm/llm/lib/index.js"
        if not vendor_node_modules.is_dir() or not dsh_llm.is_file():
            self.skipTest("vendored DSH dependencies are not built")
        with tempfile.TemporaryDirectory(prefix="pharos-fake-bundle-") as directory:
            root = Path(directory)
            package = root / "node_modules/pharos-fake-dsh"
            shutil.copytree(BUNDLE, package)
            (root / "node_modules/@deepseek-ai").symlink_to(vendor_node_modules, target_is_directory=True)
            script = """
import { apply, PHAROS_FAKE_PROVIDER, PHAROS_FAKE_MODEL } from './node_modules/pharos-fake-dsh/index.js'
const calls = []
const ctx = { llm: { registerAdapter(providers, adapter) { calls.push({ providers, adapter }); return () => {} } } }
apply(ctx)
if (calls.length !== 1 || calls[0].providers.length !== 1 || calls[0].providers[0] !== PHAROS_FAKE_PROVIDER) throw new Error('unexpected registration')
const options = { provider: PHAROS_FAKE_PROVIDER, model: PHAROS_FAKE_MODEL, messages: [], tools: [] }
async function collect() { const chunks = []; for await (const chunk of calls[0].adapter.stream(options)) chunks.push(chunk); return chunks }
const first = await collect()
const second = await collect()
if (JSON.stringify(first) !== JSON.stringify(second)) throw new Error('stream is not deterministic')
if (first.map(chunk => chunk.type).join(',') !== 'block-start,text-delta,block-end,usage,finish') throw new Error('invalid chunk sequence')
if (first[2].block.text !== first[1].text || first[3].usage.inputTokens !== 8 || first[3].usage.outputTokens !== 7) throw new Error('invalid canary output')
const models = await calls[0].adapter.listModels(PHAROS_FAKE_PROVIDER)
if (models.length !== 1 || models[0].provider !== PHAROS_FAKE_PROVIDER || models[0].id !== PHAROS_FAKE_MODEL) throw new Error('invalid model catalog')
let rejected = false
try { await calls[0].adapter.resolveModel(PHAROS_FAKE_PROVIDER, 'not-canary') } catch { rejected = true }
if (!rejected) throw new Error('unapproved model resolved')
console.log(JSON.stringify(first))
"""
            completed = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)[-1]["reason"]["kind"], "stop")

    def test_typescript_source_matches_the_pinned_plugin_api(self) -> None:
        vendor = ROOT / "vendor/deepseek-harness"
        tsc = vendor / "node_modules/.bin/tsc"
        if not tsc.is_file():
            self.skipTest("vendored DSH dependencies are not installed")
        with tempfile.TemporaryDirectory(prefix="pharos-fake-types-") as directory:
            root = Path(directory)
            shutil.copytree(BUNDLE, root / "pharos-fake-dsh")
            (root / "node_modules").symlink_to(
                vendor / "node_modules/.pnpm/node_modules", target_is_directory=True
            )
            config = {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "NodeNext",
                    "moduleResolution": "NodeNext",
                    "strict": True,
                    "noEmit": True,
                    "skipLibCheck": True,
                },
                "include": ["pharos-fake-dsh/src/index.ts"],
            }
            (root / "tsconfig.json").write_text(json.dumps(config), encoding="utf-8")
            completed = subprocess.run(
                [str(tsc), "-p", str(root / "tsconfig.json")],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_source_contains_no_host_capability_api(self) -> None:
        text = "\n".join((BUNDLE / name).read_text(encoding="utf-8") for name in ("src/index.ts", "index.js"))
        for token in ("process.env", "node:", "child_process", "fetch(", "WebSocket", "setTimeout(", "setInterval(", "Math.random", "Date("):
            self.assertNotIn(token, text)

    def test_policy_rejects_a_capability_added_to_the_entry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-fake-policy-") as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "harness-runtime", root / "harness-runtime")
            entry = root / "harness-runtime/bundles/pharos-fake/index.js"
            entry.write_text(entry.read_text(encoding="utf-8") + "\nprocess.env.SECRET\n", encoding="utf-8")
            errors = CHECKER.check_fake_bundles(root)
            self.assertTrue(any("forbidden runtime API" in error for error in errors), errors)

    def test_policy_rejects_obfuscated_capability_or_install_script(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-fake-policy-") as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "harness-runtime", root / "harness-runtime")
            entry = root / "harness-runtime/bundles/pharos-fake/index.js"
            entry.write_text(
                entry.read_text(encoding="utf-8")
                + "\nconst hidden = globalThis['fetch']; void hidden\n",
                encoding="utf-8",
            )
            manifest_path = root / "harness-runtime/bundles/pharos-fake/package.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scripts"] = {"postinstall": "node install.js"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = CHECKER.check_fake_bundles(root)
            self.assertTrue(any("reviewed content changed" in error for error in errors), errors)
            self.assertTrue(any("unreviewed field" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
