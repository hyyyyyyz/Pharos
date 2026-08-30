"""Strict verification for one provisioned DeepSeek Harness runtime.

The build-time provisioner emits a read-only directory containing a canonical
manifest and a relocatable DSH_HOME template.  Production never trusts hashes
copied out of that JSON by itself: the deployment supplies the expected
manifest digest, profile digest, policy digest and upstream revision, and this
module recomputes every referenced artifact before an Attempt can spawn.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_MAX_FILES = 4096
_MAX_BYTES = 16 * 1024 * 1024
_MANIFEST_FIELDS = frozenset(
    {
        "manifest_version",
        "kind",
        "provisioner_version",
        "profile",
        "node",
        "node_version",
        "node_sha256",
        "cli_sha256",
        "upstream_commit",
        "policy_sha256",
        "protocol_sha256",
        "profile_patch_sha256",
        "lock_sha256",
        "profile_sha256",
        "bundle_sha256",
        "effective_sha256",
        "template_sha256",
        "effective_path",
        "tree_sha256",
    }
)
_AUTHENTICATED_PROFILE = "sdk"


class RuntimeManifestError(ValueError):
    """The prepared runtime cannot be authenticated exactly."""


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeManifest:
    """Authenticated paths and provenance for a prepared runtime."""

    manifest_path: Path
    root: Path
    template: Path
    safe_patch: Path
    manifest_sha256: str
    profile_sha256: str
    source_policy_sha256: str
    profile_patch_sha256: str
    upstream_commit: str
    protocol_sha256: str
    template_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise RuntimeManifestError("runtime artifact cannot be read") from error
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeManifestError("runtime manifest contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise RuntimeManifestError("runtime manifest contains a non-finite number")


def _regular_files(root: Path, *, exclude: frozenset[str] = frozenset()) -> list[Path]:
    if not root.is_absolute():
        raise RuntimeManifestError("runtime root must be absolute")
    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        raise RuntimeManifestError("runtime root is unavailable") from error
    if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
        raise RuntimeManifestError("runtime root must be a real directory")
    if root_mode & 0o222:
        raise RuntimeManifestError("runtime root must be read-only")
    files: list[Path] = []
    total_bytes = 0
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise RuntimeManifestError("runtime directory cannot be inspected") from error
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or mode & 0o222:
                raise RuntimeManifestError(
                    f"runtime contains a mutable link or special directory: {relative}"
                )
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            try:
                info = path.lstat()
            except OSError as error:
                raise RuntimeManifestError("runtime file cannot be inspected") from error
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise RuntimeManifestError(
                    f"runtime contains a link or special file: {relative}"
                )
            if info.st_mode & 0o222:
                raise RuntimeManifestError(f"runtime contains a mutable file: {relative}")
            files.append(path)
            total_bytes += info.st_size
            if len(files) > _MAX_FILES or total_bytes > _MAX_BYTES:
                raise RuntimeManifestError("runtime exceeds the verified tree bound")
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def tree_sha256(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    records = [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in _regular_files(root, exclude=exclude)
    ]
    return hashlib.sha256(_canonical_json(records)).hexdigest()


def _require_digest(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise RuntimeManifestError(f"runtime manifest {name} is invalid")
    return value


def _require_file(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise RuntimeManifestError(f"{label} must be absolute")
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise RuntimeManifestError(f"{label} is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeManifestError(f"{label} must be a regular non-link file")
    return path


def verify_runtime_manifest(
    manifest_path: Path,
    *,
    argv: tuple[str, ...],
    prepared_dsh_home: Path,
    safe_patch: Path,
    expected_manifest_sha256: str,
    expected_profile_sha256: str,
    expected_policy_sha256: str,
    expected_upstream_commit: str,
) -> VerifiedRuntimeManifest:
    """Authenticate the complete prepared runtime against deployment pins."""

    manifest_path = _require_file(manifest_path, "runtime manifest")
    if manifest_path.name != "runtime-manifest.json":
        raise RuntimeManifestError("runtime manifest has an unexpected filename")
    root = manifest_path.parent
    # This also verifies manifest permissions and the complete root tree.
    all_files = _regular_files(root)
    if manifest_path not in all_files:
        raise RuntimeManifestError("runtime manifest is outside the verified tree")
    raw = manifest_path.read_bytes()
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeManifestError("runtime manifest is not strict JSON") from error
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
        raise RuntimeManifestError("runtime manifest fields do not match version 1")
    if (
        payload.get("manifest_version") != 1
        or payload.get("kind") != "pharos.dsh.runtime-manifest"
        or payload.get("provisioner_version") != "1"
        or payload.get("profile") != _AUTHENTICATED_PROFILE
        or payload.get("effective_path") != "template/effective-config.yml"
    ):
        raise RuntimeManifestError("runtime manifest identity is invalid")
    profile_indexes = [index for index, value in enumerate(argv) if value == "--profile"]
    if (
        len(profile_indexes) != 1
        or profile_indexes[0] + 1 >= len(argv)
        or argv[profile_indexes[0] + 1] != _AUTHENTICATED_PROFILE
        or any(value.startswith("--profile=") for value in argv)
    ):
        raise RuntimeManifestError("runtime argv does not select the authenticated sdk profile")
    if raw != _canonical_json(payload):
        raise RuntimeManifestError("runtime manifest is not canonically encoded")

    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if manifest_sha256 != expected_manifest_sha256:
        raise RuntimeManifestError("runtime manifest digest does not match deployment pin")
    profile_sha256 = _require_digest(payload, "profile_sha256")
    source_policy_sha256 = _require_digest(payload, "policy_sha256")
    profile_patch_sha256 = _require_digest(payload, "profile_patch_sha256")
    protocol_sha256 = _require_digest(payload, "protocol_sha256")
    if profile_sha256 != expected_profile_sha256:
        raise RuntimeManifestError("runtime profile digest does not match deployment pin")
    if profile_patch_sha256 != expected_policy_sha256:
        raise RuntimeManifestError("runtime effective policy digest does not match deployment pin")
    upstream_commit = payload.get("upstream_commit")
    if (
        not isinstance(upstream_commit, str)
        or _COMMIT.fullmatch(upstream_commit) is None
        or upstream_commit != expected_upstream_commit
    ):
        raise RuntimeManifestError("runtime upstream revision does not match deployment pin")

    template = root / "template"
    patch = template / "pharos-safe.cordis.patch.yml"
    if prepared_dsh_home.resolve() != template.resolve() or safe_patch.resolve() != patch.resolve():
        raise RuntimeManifestError("runtime paths do not match the authenticated template")
    if sha256_file(_require_file(patch, "runtime safety patch")) != profile_patch_sha256:
        raise RuntimeManifestError("runtime safety patch digest failed verification")
    profile = template / "profiles/sdk"
    bundle = template / "bundles/pharos-fake"
    hash_targets: dict[str, Path] = {
        "lock_sha256": profile / "pnpm-lock.yaml",
        "profile_sha256": profile,
        "bundle_sha256": bundle,
        "effective_sha256": template / "effective-config.yml",
        "template_sha256": template,
    }
    template_sha256 = _require_digest(payload, "template_sha256")
    for name, path in hash_targets.items():
        expected = _require_digest(payload, name)
        actual = tree_sha256(path) if path.is_dir() else sha256_file(_require_file(path, name))
        if actual != expected:
            raise RuntimeManifestError(f"runtime artifact {name} failed verification")
    root_hash = tree_sha256(root, exclude=frozenset({manifest_path.name}))
    if root_hash != _require_digest(payload, "tree_sha256"):
        raise RuntimeManifestError("runtime tree digest failed verification")

    if len(argv) < 2:
        raise RuntimeManifestError("runtime argv does not identify node and the DSH CLI")
    node = _require_file(Path(argv[0]), "runtime node")
    cli = _require_file(Path(argv[1]), "runtime CLI")
    node_digest = _require_digest(payload, "node_sha256")
    cli_digest = _require_digest(payload, "cli_sha256")
    node_record = payload.get("node")
    if (
        not isinstance(node_record, dict)
        or set(node_record) != {"version", "sha256"}
        or node_record.get("sha256") != node_digest
        or node_record.get("version") != payload.get("node_version")
    ):
        raise RuntimeManifestError("runtime node identity is inconsistent")
    if sha256_file(node) != node_digest or sha256_file(cli) != cli_digest:
        raise RuntimeManifestError("runtime executable digest failed verification")

    return VerifiedRuntimeManifest(
        manifest_path=manifest_path,
        root=root,
        template=template,
        safe_patch=patch,
        manifest_sha256=manifest_sha256,
        profile_sha256=profile_sha256,
        source_policy_sha256=source_policy_sha256,
        profile_patch_sha256=profile_patch_sha256,
        upstream_commit=upstream_commit,
        protocol_sha256=protocol_sha256,
        template_sha256=template_sha256,
    )


__all__ = [
    "RuntimeManifestError",
    "VerifiedRuntimeManifest",
    "sha256_file",
    "tree_sha256",
    "verify_runtime_manifest",
]
