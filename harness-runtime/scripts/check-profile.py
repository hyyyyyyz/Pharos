#!/usr/bin/env python3
"""Fail-closed audit for the Pharos safe Cordis profile overlay.

This checker intentionally parses only the stable id/name/disabled fields of
Cordis patch rows.  It does not evaluate YAML or ``!!js`` expressions, so it
has no dependency on the harness runtime and cannot execute configuration code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROW_RE = re.compile(r"^(\s*)- id:\s*([^\s#]+)")
FIELD_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$")


def _unquote(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read the row metadata needed for a static profile audit."""
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = ROW_RE.match(line)
        if match:
            current = {
                "id": _unquote(match.group(2)),
                "indent": len(match.group(1)),
                "line": line_number,
                "text": [],
                "fields": set(),
            }
            rows.append(current)
            continue
        if current is None:
            continue
        current["text"].append(line)
        match = FIELD_RE.match(line)
        if match and len(match.group(1)) == current["indent"] + 2:
            field = match.group(2)
            current["fields"].add(field)
            if field in {"name", "disabled"}:
                current[field] = _unquote(match.group(3))
    return rows


def classify(row: dict[str, Any], keywords: dict[str, list[str]]) -> set[str]:
    """Return dangerous capability categories found in a row's id or name."""
    haystack = f"{row['id']} {row.get('name', '')}".lower()
    result: set[str] = set()
    for category, terms in keywords.items():
        if any(term.lower() in haystack for term in terms):
            result.add(category)
    # A filesystem search tool is not a web search tool.  The explicit
    # filesystem category is what makes this exception auditable.
    if "filesystem" in result and "web" in result and "web" not in haystack and "http" not in haystack:
        result.remove("web")
    return result


def vendor_commits(path: Path) -> dict[str, str]:
    """Read the pinned upstream commit column from vendor/README.md."""
    commits: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        columns = line.rstrip().split("|")
        if len(columns) < 8:
            continue
        directory = columns[1].strip().strip("`").rstrip("/")
        commit = columns[-2].strip().strip("`")
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            commits[directory] = commit
    if not commits:
        raise ValueError(f"no vendor commit rows found in {path}")
    return commits


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check(root: Path, policy_path: Path | None = None) -> list[str]:
    """Return all profile policy violations; an empty list means pass."""
    policy_path = policy_path or root / "harness-runtime/security-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    required = {"sources", "upstream", "deny", "allow", "classification_keywords", "patch"}
    missing = required - policy.keys()
    if missing:
        fail(errors, f"policy missing required fields: {', '.join(sorted(missing))}")
        return errors

    deny = {entry["id"]: entry for entry in policy["deny"]}
    allow = {entry["id"]: entry for entry in policy["allow"]}
    overlap = sorted(set(deny) & set(allow))
    if overlap:
        fail(errors, f"allow/deny overlap: {', '.join(overlap)}")
    if len(deny) != len(policy["deny"]):
        fail(errors, "deny contains duplicate row ids")
    if len(allow) != len(policy["allow"]):
        fail(errors, "allow contains duplicate row ids")

    upstream = policy["upstream"]
    snapshot_manifest = root / upstream["snapshot_manifest"]
    actual_hash = hashlib.sha256(snapshot_manifest.read_bytes()).hexdigest()
    if actual_hash != upstream.get("snapshot_manifest_sha256"):
        fail(errors, "snapshot manifest hash changed; review the pinned Harness source")
    snapshot_revision = upstream.get("snapshot_revision")
    if not isinstance(snapshot_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", snapshot_revision):
        fail(errors, "snapshot revision is not a full Git commit")
    elif f"Upstream revision: `{snapshot_revision}`" not in snapshot_manifest.read_text(encoding="utf-8"):
        fail(errors, "snapshot manifest does not contain the policy revision")

    manifest = root / upstream["subvendor_manifest"]
    actual_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if actual_hash != upstream.get("subvendor_manifest_sha256"):
        fail(errors, "subvendor manifest revision/hash changed; review upstream rows and policy")
    try:
        actual_commits = vendor_commits(manifest)
    except (OSError, ValueError) as exc:
        fail(errors, str(exc))
        actual_commits = {}
    if actual_commits != upstream.get("vendor_commits"):
        fail(errors, "vendor manifest upstream revisions do not match security policy")

    expected_source_hashes = upstream.get("source_sha256", {})
    if set(expected_source_hashes) != set(policy["sources"]):
        fail(errors, "source_sha256 keys must exactly match audited sources")

    all_rows: dict[str, list[dict[str, Any]]] = {}
    for source in policy["sources"]:
        source_path = root / source
        if not source_path.exists():
            fail(errors, f"missing policy source: {source}")
            continue
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_source_hashes.get(source):
            fail(errors, f"audited source hash changed: {source}")
        for row in read_rows(source_path):
            all_rows.setdefault(row["id"], []).append(row)

    keywords = policy["classification_keywords"]
    for row_id, rows in sorted(all_rows.items()):
        categories: set[str] = set()
        for row in rows:
            categories |= classify(row, keywords)
        if categories:
            if row_id not in deny:
                fail(errors, f'dangerous row "{row_id}" ({", ".join(sorted(categories))}) is not explicitly denied; review it')
            else:
                declared = set(deny[row_id].get("categories", []))
                if not categories <= declared:
                    fail(errors, f'row "{row_id}" policy categories omit: {", ".join(sorted(categories - declared))}')
        elif row_id not in allow and row_id not in deny:
            fail(errors, f'safe row "{row_id}" is neither explicitly allowed nor denied')

    known = set(all_rows)
    for row_id in sorted(set(deny) | set(allow)):
        if row_id not in known:
            fail(errors, f'policy row "{row_id}" is absent from all audited sources')

    patch_path = root / "harness-runtime" / policy["patch"]
    patch_rows = read_rows(patch_path)
    patch_ids = {row["id"] for row in patch_rows}
    if len(patch_ids) != len(patch_rows):
        fail(errors, "safe patch contains duplicate row ids")
    required_patch_ids = {row_id for row_id, entry in deny.items() if entry.get("patch_required", True)}
    if patch_ids != required_patch_ids:
        missing_patch = sorted(required_patch_ids - patch_ids)
        extra_patch = sorted(patch_ids - required_patch_ids)
        if missing_patch:
            fail(errors, f"safe patch omits denied rows: {', '.join(missing_patch)}")
        if extra_patch:
            fail(errors, f"safe patch contains rows outside sdk deny scope: {', '.join(extra_patch)}")
    for row in patch_rows:
        if row.get("disabled") != "true":
            fail(errors, f'safe patch row "{row["id"]}" must use literal disabled: true')
        if row["id"] not in deny:
            fail(errors, f'safe patch row "{row["id"]}" is not a policy deny row')
        if row["fields"] != {"disabled"}:
            fail(errors, f'safe patch row "{row["id"]}" contains fields beyond id/disabled')

    telemetry_ids = [row_id for row_id, entry in deny.items() if "telemetry" in entry.get("categories", []) and entry.get("patch_required", True)]
    for row_id in telemetry_ids:
        if row_id not in patch_ids or next(row for row in patch_rows if row["id"] == row_id).get("disabled") != "true":
            fail(errors, f'telemetry row "{row_id}" is not disabled in safe patch')
    return errors


def check_effective(root: Path, effective_path: Path, policy_path: Path | None = None) -> list[str]:
    """Audit the exact configuration emitted by ``dsh --dump-config``."""
    policy_path = policy_path or root / "harness-runtime/security-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    rows = read_rows(effective_path)
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        fail(errors, "effective sdk profile contains duplicate row ids")

    known = {entry["id"] for entry in policy["allow"]} | {
        entry["id"] for entry in policy["deny"]
    }
    bundle_rows = {
        row_id
        for bundle in policy.get("bundles", [])
        for row_id in bundle.get("allowed_rows", [])
    }
    bundle_names = {
        row_id: bundle.get("name")
        for bundle in policy.get("bundles", [])
        for row_id in bundle.get("allowed_rows", [])
    }
    known |= bundle_rows
    for row_id in sorted(set(by_id) - known):
        fail(errors, f'effective sdk profile contains unreviewed row "{row_id}"')

    required_disabled = {
        entry["id"] for entry in policy["deny"] if entry.get("patch_required", True)
    }
    for row_id in sorted(required_disabled):
        row = by_id.get(row_id)
        if row is None:
            fail(errors, f'effective sdk profile is missing denied row "{row_id}"')
        elif row.get("disabled") != "true":
            fail(errors, f'effective sdk profile leaves denied row "{row_id}" active')

    for row_id, row in sorted(by_id.items()):
        if row_id.startswith("tool-") and row.get("disabled") != "true":
            fail(errors, f'effective sdk profile exposes model-facing tool row "{row_id}"')

    required_active = {
        "llm",
        "session",
        "agent",
        "session-persistence-jsonl",
        "token-meter",
        "tools",
        "system-prompt",
        "agent-loop",
        "sdk-app-startup",
        "sdk-jsonrpc-server",
    }
    for row_id in sorted(required_active):
        row = by_id.get(row_id)
        if row is None:
            fail(errors, f'effective sdk profile is missing required runtime row "{row_id}"')
        elif row.get("disabled") == "true":
            fail(errors, f'effective sdk profile disables required runtime row "{row_id}"')
    for row_id in sorted(bundle_rows):
        row = by_id.get(row_id)
        if row is None:
            fail(errors, f'effective sdk profile is missing allowlisted bundle row "{row_id}"')
        elif row.get("disabled") == "true":
            fail(errors, f'effective sdk profile disables allowlisted bundle row "{row_id}"')
        elif row.get("name") != bundle_names[row_id]:
            fail(errors, f'effective sdk profile changes allowlisted bundle row "{row_id}"')
    return errors


def check_fake_bundles(root: Path, policy_path: Path | None = None) -> list[str]:
    """Audit the out-of-tree deterministic adapter bundles separately.

    Bundle rows are not upstream profile rows, so they intentionally do not
    participate in the source hash/deny-list loop above.  They still need an
    explicit policy entry: the checker verifies their manifest, patch layer,
    route/model and checked-in runtime artifact without importing JavaScript.
    """
    policy_path = policy_path or root / "harness-runtime/security-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    bundles = policy.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        return ["policy must declare at least one audited out-of-tree bundle"]
    for bundle in bundles:
        if not isinstance(bundle, dict):
            fail(errors, "bundle policy entry must be an object")
            continue
        required = {
            "name",
            "root",
            "manifest",
            "patch",
            "entry",
            "allowed_rows",
            "provider",
            "model",
            "source_sha256",
        }
        missing = required - bundle.keys()
        if missing:
            fail(errors, f"bundle policy missing required fields: {', '.join(sorted(missing))}")
            continue
        root_dir = root / bundle["root"]
        manifest_path = root / bundle["manifest"]
        patch_path = root / bundle["patch"]
        entry_path = root / bundle["entry"]
        for path, label in ((manifest_path, "manifest"), (patch_path, "patch"), (entry_path, "entry")):
            if not path.is_file():
                fail(errors, f"fake bundle {label} is missing: {path.relative_to(root)}")
        if not manifest_path.is_file() or not patch_path.is_file() or not entry_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(errors, f"fake bundle manifest is not valid JSON: {exc}")
            continue
        if manifest.get("name") != bundle["name"]:
            fail(errors, "fake bundle manifest name does not match policy")
        allowed_manifest_keys = {
            "name",
            "version",
            "description",
            "type",
            "main",
            "types",
            "files",
            "license",
            "dsh",
            "peerDependencies",
        }
        if set(manifest) != allowed_manifest_keys:
            fail(errors, "fake bundle manifest contains an unreviewed field")
        if manifest.get("type") != "module" or manifest.get("main") != "index.js":
            fail(errors, "fake bundle must use the checked-in ESM index.js entry")
        if manifest.get("dsh", {}).get("bundle", {}).get("patch") != "./cordis.patch.yml":
            fail(errors, "fake bundle must declare dsh.bundle.patch as ./cordis.patch.yml")
        expected_files = {
            "README.md",
            "cordis.patch.yml",
            "index.d.ts",
            "index.js",
            "package.json",
            "src/index.ts",
        }
        declared_files = set(manifest.get("files", [])) | {"package.json"}
        if declared_files != expected_files:
            fail(errors, "fake bundle manifest files are not the exact reviewed set")
        peers = manifest.get("peerDependencies", {})
        if set(peers) != {"@deepseek-ai/cordis", "@deepseek-ai/dsh-llm"}:
            fail(errors, "fake bundle peer dependencies must be limited to Cordis and dsh-llm")
        if peers != {
            "@deepseek-ai/cordis": "4.0.1",
            "@deepseek-ai/dsh-llm": "0.1.2-alpha.1",
        }:
            fail(errors, "fake bundle peer dependencies must pin the audited DSH version")
        hashes = bundle.get("source_sha256")
        if not isinstance(hashes, dict) or set(hashes) != expected_files:
            fail(errors, "fake bundle hashes must cover the exact reviewed file set")
        else:
            actual_files = {
                str(path.relative_to(root_dir))
                for path in root_dir.rglob("*")
                if path.is_file()
            }
            if actual_files != expected_files:
                fail(errors, "fake bundle directory contains an unreviewed file")
            for relative_path, expected_hash in hashes.items():
                path = root_dir / relative_path
                if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
                    fail(errors, f"fake bundle hash is invalid: {relative_path}")
                elif path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                    fail(errors, f"fake bundle reviewed content changed: {relative_path}")
        patch_rows = read_rows(patch_path)
        allowed = set(bundle["allowed_rows"])
        if {row["id"] for row in patch_rows} != allowed:
            fail(errors, "fake bundle patch rows do not match its explicit policy allowlist")
        for row in patch_rows:
            if row["fields"] != {"name"} or row.get("name") != bundle["name"]:
                fail(errors, f'fake bundle row "{row["id"]}" must only name its package')
        if not root_dir.is_dir():
            fail(errors, "fake bundle policy root is missing")
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root_dir / "src/index.ts", entry_path)
            if path.is_file()
        )
        forbidden = re.compile(
            r"(?:node:|@deepseek-ai/dsh-(?:fs|subprocess)|child_process|process\.env|"
            r"(?:fetch|WebSocket|net|http|https)\s*\(|(?:Date|Math\.random|crypto)\b|"
            r"(?:readFile|writeFile|readdir|setTimeout|setInterval)\s*\()",
            re.IGNORECASE,
        )
        match = forbidden.search(source_text)
        if match:
            fail(errors, f"fake bundle contains a forbidden runtime API: {match.group(0)!r}")
        for required_text, label in (
            ("extends LlmAdapter", "LlmAdapter implementation"),
            ("registerAdapter([PHAROS_FAKE_PROVIDER]", "single fake provider registration"),
            ("block-start", "block-start stream chunk"),
            ("text-delta", "text-delta stream chunk"),
            ("block-end", "block-end stream chunk"),
            ("type: 'usage'", "usage stream chunk"),
            ("type: 'finish'", "finish stream chunk"),
        ):
            if required_text not in source_text:
                fail(errors, f"fake bundle is missing {label}")
        if bundle.get("provider") != "pharos-fake" or bundle.get("model") != "pharos-fake-canary":
            fail(errors, "fake bundle policy route/model is not the reviewed canary")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--policy", type=Path)
    parser.add_argument(
        "--effective-config",
        type=Path,
        help="also audit a file emitted by dsh --dump-config",
    )
    args = parser.parse_args()
    errors = check(args.root.resolve(), args.policy.resolve() if args.policy else None)
    errors.extend(check_fake_bundles(args.root.resolve(), args.policy.resolve() if args.policy else None))
    if args.effective_config is not None:
        errors.extend(
            check_effective(
                args.root.resolve(),
                args.effective_config.resolve(),
                args.policy.resolve() if args.policy else None,
            )
        )
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Pharos safe sdk profile policy is internally consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
