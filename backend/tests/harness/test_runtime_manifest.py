"""Runtime package verification is content-addressed and fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest
from pharos.harness.dsh_gateway import DshRuntimeConfig
from pharos.harness.runtime_manifest import RuntimeManifestError, verify_runtime_manifest
from tests.harness.dsh_runtime_fixture import make_provisioned_runtime


def _verify(runtime: dict) -> None:
    verified = verify_runtime_manifest(
        runtime["manifest"],
        argv=(
            str(runtime["node"]),
            str(runtime["cli"]),
            "--profile",
            "sdk",
            "--patch",
            str(runtime["patch"]),
        ),
        prepared_dsh_home=runtime["template"],
        safe_patch=runtime["patch"],
        expected_manifest_sha256=runtime["runtime_hash"],
        expected_profile_sha256=runtime["profile_hash"],
        expected_policy_sha256=runtime["policy_hash"],
        expected_upstream_commit=runtime["upstream_commit"],
    )
    assert verified.template_sha256
    assert verified.profile_patch_sha256 == runtime["policy_hash"]


def test_manifest_authenticates_tree_executables_and_deployment_pins(tmp_path: Path) -> None:
    runtime = make_provisioned_runtime(tmp_path)
    _verify(runtime)

    config = DshRuntimeConfig(
        argv=(
            str(runtime["node"]),
            str(runtime["cli"]),
            "--profile",
            "sdk",
            "--patch",
            str(runtime["patch"]),
        ),
        cwd=str(tmp_path),
        patch=str(runtime["patch"]),
        prepared_dsh_home=str(runtime["template"]),
        runtime_manifest=str(runtime["manifest"]),
        runtime_hash=runtime["runtime_hash"],
        profile_hash=runtime["profile_hash"],
        policy_hash=runtime["policy_hash"],
        upstream_commit=runtime["upstream_commit"],
        expected_model_profile_sha256="a" * 64,
        expected_model_route_sha256="b" * 64,
    )
    config.verify_prepared_runtime()


def test_manifest_rejects_wrong_pin_and_post_construction_tamper(tmp_path: Path) -> None:
    runtime = make_provisioned_runtime(tmp_path)
    with pytest.raises(RuntimeManifestError, match="deployment pin"):
        verify_runtime_manifest(
            runtime["manifest"],
            argv=(
                str(runtime["node"]),
                str(runtime["cli"]),
                "--profile",
                "sdk",
                "--patch",
                str(runtime["patch"]),
            ),
            prepared_dsh_home=runtime["template"],
            safe_patch=runtime["patch"],
            expected_manifest_sha256="0" * 64,
            expected_profile_sha256=runtime["profile_hash"],
            expected_policy_sha256=runtime["policy_hash"],
            expected_upstream_commit=runtime["upstream_commit"],
        )

    config = DshRuntimeConfig(
        argv=(
            str(runtime["node"]),
            str(runtime["cli"]),
            "--profile",
            "sdk",
            "--patch",
            str(runtime["patch"]),
        ),
        cwd=str(tmp_path),
        patch=str(runtime["patch"]),
        prepared_dsh_home=str(runtime["template"]),
        runtime_manifest=str(runtime["manifest"]),
        runtime_hash=runtime["runtime_hash"],
        profile_hash=runtime["profile_hash"],
        policy_hash=runtime["policy_hash"],
        upstream_commit=runtime["upstream_commit"],
        expected_model_profile_sha256="a" * 64,
        expected_model_route_sha256="b" * 64,
    )
    target = runtime["template"] / "effective-config.yml"
    target.chmod(0o644)
    target.write_text("tampered\n", encoding="utf-8")
    target.chmod(0o444)
    with pytest.raises(RuntimeManifestError, match="failed verification"):
        config.verify_prepared_runtime()


def test_manifest_rejects_non_sdk_profile_argv(tmp_path: Path) -> None:
    runtime = make_provisioned_runtime(tmp_path)
    with pytest.raises(RuntimeManifestError, match="authenticated sdk profile"):
        verify_runtime_manifest(
            runtime["manifest"],
            argv=(
                str(runtime["node"]),
                str(runtime["cli"]),
                "--profile",
                "evil",
                "--patch",
                str(runtime["patch"]),
            ),
            prepared_dsh_home=runtime["template"],
            safe_patch=runtime["patch"],
            expected_manifest_sha256=runtime["runtime_hash"],
            expected_profile_sha256=runtime["profile_hash"],
            expected_policy_sha256=runtime["policy_hash"],
            expected_upstream_commit=runtime["upstream_commit"],
        )
