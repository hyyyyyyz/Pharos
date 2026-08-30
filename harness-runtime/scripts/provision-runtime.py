#!/usr/bin/env python3
"""Build a relocatable, read-only DSH runtime template.

This is deliberately a build-time command.  It never reads a caller's
credentials or profile and every DSH subprocess receives a freshly constructed
environment.  The only package operation is an offline ``file:`` install of
the checked-in deterministic fake bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


PROVISIONER_VERSION = "1"
PROFILE = "sdk"
BUNDLE_NAME = "pharos-fake-dsh"
BUNDLE_RELATIVE = Path("harness-runtime/bundles/pharos-fake")
PROFILE_PATCH_RELATIVE = Path("harness-runtime/profile/pharos-safe.cordis.patch.yml")
POLICY_RELATIVE = Path("harness-runtime/security-policy.json")
CLI_RELATIVE = Path("vendor/deepseek-harness/apps/cli/lib/bin.js")
CHECKER_RELATIVE = Path("harness-runtime/scripts/check-profile.py")
OUTPUT_TEMPLATE = Path("template")
MANIFEST_NAME = "runtime-manifest.json"

FORBIDDEN_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "attachments",
    "anonymous-id",
    "anonymous_id",
    "anonymous-id.json",
    "anonymous_id.json",
    "logs",
    "log",
    "sessions",
    "storages",
    "storage",
}
SECRET_RE = re.compile(
    r"(?:DEEPSEEK_API_KEY|PHAROS_CREDENTIAL_SECRET|OPENAI_API_KEY|"
    r"AWS_SECRET_ACCESS_KEY|BEGIN[ -]RSA[ -]PRIVATE[ -]KEY|"
    r"(?:api[_-]?key|secret(?:[_-]?key)?|password|private[_-]?key|"
    r"access[_-]?token|authorization)\s*[:=])",
    re.IGNORECASE,
)
# A path with at least two components is enough to catch /Users/alice/project
# and /home/runner/build.  URLs are excluded by the negative look-behind.
ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\.])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"
    r"|(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/])(?:[^\\/\s]+[\\/])+[^\\/\s]+"
)
FILE_ABSOLUTE_URI_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])file:(?://)?(?:/|[A-Za-z]:[\\/])"
)


class ProvisionFailure(RuntimeError):
    """The template cannot be proven relocatable and safe."""


def canonical_json(value: Any) -> bytes:
    """Serialize JSON with one deterministic representation."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(root: Path, *, exclude: Iterable[str] = ()) -> list[Path]:
    """Return regular files in lexical relative-path order, rejecting links."""
    excluded = set(exclude)
    if not root.is_dir():
        raise ProvisionFailure(f"tree is not a directory: {root}")
    result: list[Path] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in excluded or any(relative.startswith(item.rstrip("/") + "/") for item in excluded):
                if name in directories:
                    directories.remove(name)
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ProvisionFailure(f"template contains a symlink: {relative}")
            if not stat.S_ISDIR(mode):
                raise ProvisionFailure(f"template contains a special file: {relative}")
            if name in FORBIDDEN_NAMES or name.startswith(".env") or "anonymous" in name.lower():
                raise ProvisionFailure(f"template contains forbidden runtime residue: {relative}")
        for name in list(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in excluded or any(relative.startswith(item.rstrip("/") + "/") for item in excluded):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ProvisionFailure(f"template contains a symlink: {relative}")
            if not stat.S_ISREG(mode):
                raise ProvisionFailure(f"template contains a special file: {relative}")
            if name in FORBIDDEN_NAMES or name.startswith(".env") or "anonymous" in name.lower():
                raise ProvisionFailure(f"template contains forbidden runtime residue: {relative}")
            result.append(path)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def tree_hash(root: Path, *, exclude: Iterable[str] = ()) -> str:
    """Hash a tree by sorted relative names and content hashes, not mtimes."""
    records = []
    for path in _relative_files(root, exclude=exclude):
        relative = path.relative_to(root).as_posix()
        records.append({"path": relative, "sha256": sha256_file(path)})
    return sha256_bytes(canonical_json(records))


def _prefixed_tree_hash(root: Path, prefix: str) -> str:
    """Hash one tree as though it were mounted below ``prefix``."""
    records = [
        {"path": f"{prefix.rstrip('/')}/{path.relative_to(root).as_posix()}", "sha256": sha256_file(path)}
        for path in _relative_files(root)
    ]
    return sha256_bytes(canonical_json(records))


def _scan_text_file(path: Path, root: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    relative = path.relative_to(root).as_posix()
    errors: list[str] = []
    if SECRET_RE.search(text):
        errors.append(f"template contains a secret-like value: {relative}")
    if FILE_ABSOLUTE_URI_RE.search(text):
        errors.append(f"template contains an absolute file URI: {relative}")
    if ABSOLUTE_PATH_RE.search(text):
        errors.append(f"template contains a developer absolute path: {relative}")
    return errors


def audit_tree(root: Path) -> list[str]:
    """Return all portability and residue violations in ``root``."""
    try:
        files = _relative_files(root)
    except ProvisionFailure as error:
        return [str(error)]
    errors: list[str] = []
    for path in files:
        errors.extend(_scan_text_file(path, root))
    return errors


def _require_regular(
    path: Path,
    label: str,
    *,
    executable: bool = False,
    allow_symlink: bool = False,
) -> Path:
    if not path.is_absolute():
        raise ProvisionFailure(f"{label} must be an absolute path: {path}")
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ProvisionFailure(f"{label} is unavailable: {path}") from error
    if not stat.S_ISREG(mode) and not (allow_symlink and stat.S_ISLNK(mode)):
        raise ProvisionFailure(f"{label} must be a regular, non-symlink file: {path}")
    if executable and not mode & stat.S_IXUSR:
        raise ProvisionFailure(f"{label} is not executable: {path}")
    return path.resolve() if stat.S_ISLNK(mode) else path


def _result_output(result: Any) -> str:
    output = getattr(result, "stdout", "")
    return output if isinstance(output, str) else str(output)


def _run(
    runner: Callable[..., Any],
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    label: str,
) -> Any:
    try:
        result = runner(
            argv,
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvisionFailure(f"{label} failed to start: {error}") from error
    if getattr(result, "returncode", 1) != 0:
        stderr = getattr(result, "stderr", "")
        raise ProvisionFailure(f"{label} failed: {str(stderr)[-2000:]}")
    return result


def _resolve_pnpm() -> Path:
    """Resolve pnpm once; the subprocess receives only its parent in PATH."""
    candidate = shutil.which("pnpm")
    if candidate is None:
        raise ProvisionFailure("pnpm is required for the offline bundle provision")
    return _require_regular(Path(candidate).resolve(), "pnpm")


def _clean_env(node: Path, pnpm: Path, home: Path, dsh_home: Path) -> dict[str, str]:
    # No os.environ value is copied.  In particular, credentials, proxy
    # settings, user patches and NODE_OPTIONS cannot affect the provision.
    # pnpm is a small shell/Node launcher and may use ``env bash``.  Keep the
    # system tool directories explicit rather than inheriting the caller PATH.
    path_entries = [str(node.parent), str(pnpm.parent)]
    path_entries.extend(path for path in ("/usr/bin", "/bin") if Path(path).is_dir())
    return {
        "PATH": os.pathsep.join(dict.fromkeys(path_entries)),
        "HOME": str(home),
        "DSH_HOME": str(dsh_home),
        "NODE_ENV": "production",
        "DSH_TELEMETRY_DISABLED": "1",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "npm_config_offline": "true",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvisionFailure(f"invalid JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise ProvisionFailure(f"JSON root is not an object: {path}")
    return value


def _copy_regular(source: Path, target: Path) -> None:
    _require_regular(source, "bundle artifact")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _bundle_policy_entry(policy: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    bundle_entries = policy.get("bundles")
    if not isinstance(bundle_entries, list) or not bundle_entries:
        raise ProvisionFailure("security policy has no bundle entry")
    entry = next(
        (item for item in bundle_entries if isinstance(item, dict) and item.get("name") == BUNDLE_NAME),
        None,
    )
    hashes = entry.get("source_sha256") if isinstance(entry, dict) else None
    if entry is None or not isinstance(hashes, dict) or not hashes:
        raise ProvisionFailure(f"security policy does not audit {BUNDLE_NAME}")
    normalized: dict[str, str] = {}
    for raw_name, expected in hashes.items():
        name = str(raw_name)
        if (
            not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or not re.fullmatch(r"[0-9a-f]{64}", str(expected))
        ):
            raise ProvisionFailure(f"security policy has an invalid bundle hash: {name}")
        normalized[name] = str(expected)
    return normalized, entry


def _copy_bundle(source: Path, target: Path, policy: Mapping[str, Any]) -> None:
    hashes, _entry = _bundle_policy_entry(policy)
    # README is source-audited but intentionally not a runtime input: its
    # installation example contains an absolute placeholder path.
    for name, expected in sorted(hashes.items()):
        source_path = source / name
        if not source_path.is_file() or source_path.is_symlink():
            raise ProvisionFailure(f"audited bundle file is missing or linked: {name}")
        # Verify the source immediately before the copy. The target hash below
        # detects a source mutation during copy or an altered output.
        if sha256_file(source_path) != expected:
            raise ProvisionFailure(f"audited bundle source changed: {name}")
        if name != "README.md":
            target_path = target / name
            _copy_regular(source_path, target_path)
            if sha256_file(target_path) != expected:
                raise ProvisionFailure(f"audited bundle copy changed: {name}")


def _normalize_profile(
    generated_profile: Path,
    template_profile: Path,
    generated_bundle: Path,
    template_bundle: Path,
    policy: Mapping[str, Any],
) -> None:
    manifest = _load_json(generated_profile / "package.json")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, dict) or BUNDLE_NAME not in dependencies:
        raise ProvisionFailure("plugin add did not install the expected fake bundle")
    dependencies[BUNDLE_NAME] = "file:../../bundles/pharos-fake"
    template_profile.mkdir(parents=True, exist_ok=True)
    (template_profile / "package.json").write_bytes(canonical_json(manifest))
    for name in ("cordis.patch.yml", "pnpm-workspace.yaml", "cordis.yml"):
        source = generated_profile / name
        if source.is_file():
            _copy_regular(source, template_profile / name)
    lock = generated_profile / "pnpm-lock.yaml"
    if not lock.is_file():
        raise ProvisionFailure("offline plugin add did not produce pnpm-lock.yaml")
    lock_text = lock.read_text(encoding="utf-8")
    # pnpm records both the original specifier and a profile-relative virtual
    # store path.  Replacing the source root leaves one stable relative path.
    relative = "file:../../bundles/pharos-fake"
    lock_text = re.sub(r"(?m)(specifier:\s*)file:\S+", rf"\g<1>{relative}", lock_text)
    lock_text = re.sub(r"(?m)(version:\s*)file:\S+", rf"\g<1>{relative}", lock_text)
    lock_text = re.sub(r"(?m)(pharos-fake-dsh@)file:\S+:", rf"\g<1>{relative}:", lock_text)
    lock_text = re.sub(r"(?m)(resolution:\s*\{directory:\s*)[^,}]+", r"\g<1>../../bundles/pharos-fake", lock_text)
    (template_profile / "pnpm-lock.yaml").write_text(lock_text, encoding="utf-8")
    _copy_bundle(generated_bundle, template_bundle, policy)
    _copy_bundle(generated_bundle, template_profile / "node_modules" / BUNDLE_NAME, policy)


def _normalized_effective(text: str, root: Path, generated_home: Path, patch: Path) -> str:
    # Dump comments name the absolute patch source.  Keep the effective rows,
    # but replace build-machine locations with stable labels before packaging.
    for path, replacement in (
        (str(generated_home.resolve()), "<build-home>"),
        (str(patch.resolve()), "<safe-profile-patch>"),
        (str(root.resolve()), "<repo-root>"),
    ):
        text = text.replace(path, replacement)
    # The effective tree records the name of an optional credential env var;
    # preserve the row while ensuring that even a future dump cannot publish a
    # credential-shaped identifier in the prepared artifact.
    text = re.sub(
        r"\b(?:DEEPSEEK_API_KEY|PHAROS_CREDENTIAL_SECRET|OPENAI_API_KEY|AWS_SECRET_ACCESS_KEY)\b",
        "<redacted-env>",
        text,
    )
    return text if text.endswith("\n") else text + "\n"


def _load_canary_module(root: Path) -> Any:
    path = root / "harness-runtime/scripts/run-fake-canary.py"
    spec = importlib.util.spec_from_file_location("pharos_runtime_canary", path)
    if spec is None or spec.loader is None:
        raise ProvisionFailure("cannot load the real fake canary")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prove_template_canary(
    root: Path,
    node: Path,
    cli: Path,
    template: Path,
    patch: Path,
) -> None:
    """Run the existing real JSON-RPC canary against the normalized copy."""
    canary = _load_canary_module(root)
    with tempfile.TemporaryDirectory(prefix="pharos-runtime-proof-") as directory:
        runtime = Path(directory)
        dsh_home = runtime / "dsh"
        shutil.copytree(template, dsh_home, symlinks=False)
        home = runtime / "home"
        workspace = runtime / "workspace"
        home.mkdir()
        workspace.mkdir()
        env = {
            "PATH": str(node.parent),
            "HOME": str(home),
            "DSH_HOME": str(dsh_home),
            "NODE_ENV": "production",
            "DSH_TELEMETRY_DISABLED": "1",
        }
        peer = canary.RuntimePeer(
            [str(node), str(cli), "--profile", PROFILE, "--patch", str(patch)],
            workspace,
            env,
        )
        try:
            initialized = canary.initialize(peer, workspace, "pharos-fake-canary", "initialize")
            if initialized.get("result", {}).get("serverInfo") != {
                "name": "deepseek-harness-sdk-runtime",
                "version": "0.0.1",
            }:
                raise ProvisionFailure("normalized template canary returned the wrong SDK identity")
            canary.verify_prompt(peer)
            canary.shutdown(peer)
        except ProvisionFailure:
            raise
        except Exception as error:
            raise ProvisionFailure(f"normalized template canary failed: {error}") from error
        finally:
            peer.close()


def _readonly(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            (Path(current) / name).chmod(0o444)
        for name in directories:
            (Path(current) / name).chmod(0o555)
    root.chmod(0o555)


def _artifact_hashes(template: Path, effective: Path) -> dict[str, str]:
    profile = template / "profiles" / PROFILE
    bundle = template / "bundles" / "pharos-fake"
    lock = profile / "pnpm-lock.yaml"
    required = {
        "lock_sha256": lock,
        "profile_sha256": profile,
        "bundle_sha256": bundle,
        "effective_sha256": effective,
        "template_sha256": template,
    }
    hashes: dict[str, str] = {}
    for name, path in required.items():
        if path.is_dir():
            hashes[name] = tree_hash(path)
        elif path.is_file():
            hashes[name] = sha256_file(path)
        else:
            raise ProvisionFailure(f"missing output artifact for {name}: {path}")
    return hashes


def provision(
    root: Path,
    node: Path,
    cli: Path,
    output: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
    prove_canary: bool = True,
) -> Path:
    """Provision ``output`` and return its canonical manifest path."""
    root = root.resolve()
    node = _require_regular(node, "node", executable=True, allow_symlink=True)
    cli = _require_regular(cli, "built CLI")
    if not root.is_dir():
        raise ProvisionFailure(f"repo root is not a directory: {root}")
    output = output.absolute()
    if output.exists():
        raise ProvisionFailure(f"output must not already exist: {output}")
    if output == root or root in output.parents:
        raise ProvisionFailure("output must not be inside the source repository")
    bundle = root / BUNDLE_RELATIVE
    patch = root / PROFILE_PATCH_RELATIVE
    policy_path = root / POLICY_RELATIVE
    checker = root / CHECKER_RELATIVE
    for path, label in ((bundle, "fake bundle"), (patch, "safe patch"), (policy_path, "security policy"), (checker, "profile checker")):
        if not path.exists():
            raise ProvisionFailure(f"missing {label}: {path}")
    policy = _load_json(policy_path)
    pnpm = _resolve_pnpm()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pharos-runtime-build-") as directory:
        build = Path(directory)
        home = build / "home"
        dsh_home = build / "dsh"
        workspace = build / "workspace"
        home.mkdir()
        workspace.mkdir()
        env = _clean_env(node, pnpm, home, dsh_home)
        node_version = _node_version(node, env, root, runner)
        source_spec = f"file:{bundle.resolve()}"
        _run(
            runner,
            [str(node), str(cli), "plugin", "--profile", PROFILE, "add", "--offline", source_spec],
            cwd=root,
            env=env,
            timeout=180,
            label="offline fake bundle add",
        )
        generated_profile = dsh_home / "profiles" / PROFILE
        if not generated_profile.is_dir():
            raise ProvisionFailure("plugin add did not create the sdk profile")
        effective_raw = _run(
            runner,
            [str(node), str(cli), "--profile", PROFILE, "--patch", str(patch), "--dump-config"],
            cwd=root,
            env=env,
            timeout=30,
            label="effective profile dump",
        )
        effective = build / "effective.yml"
        effective.write_text(_result_output(effective_raw), encoding="utf-8")
        _run(
            runner,
            [sys.executable, str(checker), "--root", str(root), "--effective-config", str(effective)],
            cwd=root,
            env=env,
            timeout=30,
            label="effective profile policy check",
        )
        template = build / OUTPUT_TEMPLATE
        template_profile = template / "profiles" / PROFILE
        template_bundle = template / "bundles" / "pharos-fake"
        _normalize_profile(generated_profile, template_profile, bundle, template_bundle, policy)
        prepared_patch = template / "pharos-safe.cordis.patch.yml"
        _copy_regular(patch, prepared_patch)
        effective_output = template / "effective-config.yml"
        effective_output.parent.mkdir(parents=True, exist_ok=True)
        effective_output.write_text(
            _normalized_effective(_result_output(effective_raw), root, dsh_home, patch),
            encoding="utf-8",
        )
        errors = audit_tree(template)
        if errors:
            raise ProvisionFailure("normalized template audit failed: " + "; ".join(errors))
        if prove_canary:
            _prove_template_canary(root, node, cli, template, prepared_patch)
        hashes = _artifact_hashes(template, effective_output)
        policy_protocol = policy.get("protocol")
        if not isinstance(policy_protocol, dict):
            raise ProvisionFailure("security policy has no protocol record")
        manifest: dict[str, Any] = {
            "manifest_version": 1,
            "kind": "pharos.dsh.runtime-manifest",
            "provisioner_version": PROVISIONER_VERSION,
            "profile": PROFILE,
            "node": {"version": node_version, "sha256": sha256_file(node)},
            "node_version": node_version,
            "node_sha256": sha256_file(node),
            "cli_sha256": sha256_file(cli),
            "upstream_commit": policy.get("upstream", {}).get("snapshot_revision"),
            "policy_sha256": sha256_file(policy_path),
            "protocol_sha256": sha256_bytes(canonical_json(policy_protocol)),
            "profile_patch_sha256": sha256_file(prepared_patch),
            **hashes,
            "effective_path": "template/effective-config.yml",
            "tree_sha256": _prefixed_tree_hash(template, "template"),
        }
        # The manifest is written after the template hash is known, so it does
        # not recursively include itself.  A canonical JSON byte stream makes
        # two builds compare byte-for-byte.
        staging = output.parent / f".{output.name}.staging-{os.getpid()}"
        if staging.exists():
            raise ProvisionFailure(f"staging path already exists: {staging}")
        shutil.copytree(build / OUTPUT_TEMPLATE, staging / OUTPUT_TEMPLATE, symlinks=False)
        (staging / MANIFEST_NAME).write_bytes(canonical_json(manifest))
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, output)
    _readonly(output)
    manifest_path = output / MANIFEST_NAME
    verify_manifest(output)
    return manifest_path


def _node_version(node: Path, env: Mapping[str, str], root: Path, runner: Callable[..., Any]) -> str:
    result = _run(runner, [str(node), "--version"], cwd=root, env=env, timeout=10, label="node version")
    version = _result_output(result).strip()
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9._-]+)?", version):
        raise ProvisionFailure(f"node --version is not canonical: {version!r}")
    return version


def verify_manifest(output: Path) -> None:
    """Fail closed if a provisioned output was modified or is not portable."""
    manifest_path = output / MANIFEST_NAME
    try:
        manifest = _load_json(manifest_path)
    except ProvisionFailure:
        raise
    if manifest.get("kind") != "pharos.dsh.runtime-manifest":
        raise ProvisionFailure("runtime manifest kind is invalid")
    required_fields = {
        "manifest_version",
        "profile",
        "node",
        "node_version",
        "node_sha256",
        "cli_sha256",
        "upstream_commit",
        "lock_sha256",
        "template_sha256",
        "profile_sha256",
        "policy_sha256",
        "profile_patch_sha256",
        "effective_sha256",
        "protocol_sha256",
        "bundle_sha256",
        "tree_sha256",
    }
    if not required_fields.issubset(manifest):
        missing = ", ".join(sorted(required_fields - manifest.keys()))
        raise ProvisionFailure(f"runtime manifest is missing fields: {missing}")
    if manifest_path.read_bytes() != canonical_json(manifest):
        raise ProvisionFailure("runtime manifest is not canonical JSON")
    errors = audit_tree(output)
    if errors:
        raise ProvisionFailure("output audit failed: " + "; ".join(errors))
    hashes = {
        "lock_sha256": output / "template/profiles/sdk/pnpm-lock.yaml",
        "profile_sha256": output / "template/profiles/sdk",
        "bundle_sha256": output / "template/bundles/pharos-fake",
        "profile_patch_sha256": output / "template/pharos-safe.cordis.patch.yml",
        "effective_sha256": output / "template/effective-config.yml",
        "template_sha256": output / "template",
    }
    for key, path in hashes.items():
        actual = tree_hash(path) if path.is_dir() else sha256_file(path)
        if manifest.get(key) != actual:
            raise ProvisionFailure(f"runtime manifest {key} does not match output")
    if manifest.get("tree_sha256") != tree_hash(output, exclude={MANIFEST_NAME}):
        raise ProvisionFailure("runtime manifest tree_sha256 does not match output")
    if any(path.stat().st_mode & 0o222 for path in output.rglob("*")) or output.stat().st_mode & 0o222:
        raise ProvisionFailure("runtime template is not read-only")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="repository root")
    parser.add_argument("--node", type=Path, required=True, help="absolute fixed node executable")
    parser.add_argument("--cli", type=Path, required=True, help="absolute built DSH CLI")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--skip-canary", action="store_true", help="only for offline unit fixtures")
    args = parser.parse_args(argv)
    try:
        manifest = provision(
            args.root,
            args.node,
            args.cli,
            args.output,
            prove_canary=not args.skip_canary,
        )
    except (ProvisionFailure, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: provisioned read-only DSH template ({manifest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
