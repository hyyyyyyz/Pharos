#!/usr/bin/env python3
"""Regression tests for the static safe-profile audit."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = ROOT / "harness-runtime/scripts/check-profile.py"
SPEC = importlib.util.spec_from_file_location("pharos_safe_checker", CHECKER_PATH)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def copy_policy_fixture(root: Path) -> None:
    shutil.copytree(ROOT / "harness-runtime", root / "harness-runtime")
    for bundle in ("base", "sdk-app", "sdk-minimal"):
        source = ROOT / f"vendor/deepseek-harness/packages/bundle/{bundle}/cordis.patch.yml"
        target = root / f"vendor/deepseek-harness/packages/bundle/{bundle}/cordis.patch.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (root / "vendor/deepseek-harness/vendor").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "vendor/deepseek-harness/vendor/README.md",
        root / "vendor/deepseek-harness/vendor/README.md",
    )
    shutil.copy2(ROOT / "vendor/README.md", root / "vendor/README.md")


class CheckProfileTests(unittest.TestCase):
    def test_current_profile_passes(self) -> None:
        self.assertEqual(CHECKER.check(ROOT), [])

    def test_omitting_a_denied_row_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-safe-profile-") as directory:
            root = Path(directory)
            copy_policy_fixture(root)

            patch = root / "harness-runtime/profile/pharos-safe.cordis.patch.yml"
            content = patch.read_text(encoding="utf-8")
            patch.write_text(content.replace("- id: tool-web\n  disabled: true\n", "", 1), encoding="utf-8")

            errors = CHECKER.check(root)
            self.assertTrue(any("safe patch omits denied rows" in error for error in errors), errors)

    def test_new_dangerous_upstream_row_requires_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-safe-upstream-") as directory:
            root = Path(directory)
            copy_policy_fixture(root)

            source = root / "vendor/deepseek-harness/packages/bundle/base/cordis.patch.yml"
            source.write_text(source.read_text(encoding="utf-8") + "\n    - id: future-mcp-provider\n      name: '@deepseek-ai/dsh-mcp-provider'\n", encoding="utf-8")

            errors = CHECKER.check(root)
            self.assertTrue(any('dangerous row "future-mcp-provider"' in error for error in errors), errors)

    def test_safe_patch_rejects_every_extra_row_field(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-safe-fields-") as directory:
            root = Path(directory)
            copy_policy_fixture(root)
            patch = root / "harness-runtime/profile/pharos-safe.cordis.patch.yml"
            content = patch.read_text(encoding="utf-8")
            patch.write_text(
                content.replace(
                    "- id: tool-web\n  disabled: true\n",
                    "- id: tool-web\n  disabled: true\n  isolate: sneaky\n",
                    1,
                ),
                encoding="utf-8",
            )

            errors = CHECKER.check(root)
            self.assertTrue(any("fields beyond id/disabled" in error for error in errors), errors)

    def test_changed_sdk_app_source_requires_policy_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-safe-sdk-app-") as directory:
            root = Path(directory)
            copy_policy_fixture(root)
            source = root / "vendor/deepseek-harness/packages/bundle/sdk-app/cordis.patch.yml"
            source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            errors = CHECKER.check(root)
            self.assertTrue(any("audited source hash changed" in error for error in errors), errors)

    def test_effective_profile_requires_every_denied_row_disabled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-effective-profile-") as directory:
            effective = Path(directory) / "effective.yml"
            rows = []
            policy = json.loads(
                (ROOT / "harness-runtime/security-policy.json").read_text(encoding="utf-8")
            )
            for entry in policy["deny"]:
                if entry.get("patch_required", True):
                    rows.append(f'- id: {entry["id"]}\n  disabled: true')
            for entry in policy["allow"]:
                rows.append(f'- id: {entry["id"]}')
            for bundle in policy["bundles"]:
                for row_id in bundle["allowed_rows"]:
                    rows.append(f'- id: {row_id}\n  name: {bundle["name"]}')
            effective.write_text("\n".join(rows) + "\n", encoding="utf-8")

            self.assertEqual(CHECKER.check_effective(ROOT, effective), [])
            effective.write_text(
                effective.read_text(encoding="utf-8").replace(
                    "- id: tool-web\n  disabled: true",
                    "- id: tool-web",
                    1,
                ),
                encoding="utf-8",
            )
            errors = CHECKER.check_effective(ROOT, effective)
            self.assertTrue(any('leaves denied row "tool-web" active' in error for error in errors), errors)

    def test_effective_profile_accepts_only_the_allowlisted_fake_row(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-effective-fake-") as directory:
            effective = Path(directory) / "effective.yml"
            policy = json.loads(
                (ROOT / "harness-runtime/security-policy.json").read_text(encoding="utf-8")
            )
            rows = [
                *(f'- id: {entry["id"]}\n  disabled: true' for entry in policy["deny"] if entry.get("patch_required", True)),
                *(f'- id: {entry["id"]}' for entry in policy["allow"]),
                "- id: llm-pharos-fake\n  name: pharos-fake-dsh",
            ]
            effective.write_text("\n".join(rows) + "\n", encoding="utf-8")
            self.assertEqual(CHECKER.check_effective(ROOT, effective), [])
            effective.write_text(
                effective.read_text(encoding="utf-8").replace(
                    "- id: llm-pharos-fake\n  name: pharos-fake-dsh",
                    "- id: llm-pharos-fake\n  name: pharos-fake-dsh\n  disabled: true",
                    1,
                ),
                encoding="utf-8",
            )
            errors = CHECKER.check_effective(ROOT, effective)
            self.assertTrue(any("disables allowlisted bundle row" in error for error in errors), errors)

    def test_effective_profile_rejects_provider_name_substitution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pharos-effective-provider-") as directory:
            effective = Path(directory) / "effective.yml"
            policy = json.loads(
                (ROOT / "harness-runtime/security-policy.json").read_text(encoding="utf-8")
            )
            rows = [
                *(
                    f'- id: {entry["id"]}\n  disabled: true'
                    for entry in policy["deny"]
                    if entry.get("patch_required", True)
                ),
                *(f'- id: {entry["id"]}' for entry in policy["allow"]),
                "- id: llm-pharos-fake\n  name: @deepseek-ai/dsh-llm-deepseek",
            ]
            effective.write_text("\n".join(rows) + "\n", encoding="utf-8")
            errors = CHECKER.check_effective(ROOT, effective)
            self.assertTrue(any("changes allowlisted bundle row" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
