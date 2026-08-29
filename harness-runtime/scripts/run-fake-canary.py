#!/usr/bin/env python3
"""Boot the real vendored DSH loader and exercise the Pharos fake bundle."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable


CANARY_PROVIDER = "pharos-fake"
CANARY_MODEL = "pharos-fake-canary"
CANARY_TEXT = '{"ok":true,"workflow":"harness.canary","step":"actor_turn"}'


class CanaryFailure(RuntimeError):
    """The disposable profile failed its executable contract."""


class RuntimePeer:
    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            raise CanaryFailure("runtime pipes were not created")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.stderr: list[str] = []

    def send(self, frame: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(frame, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def collect_until(
        self,
        predicate: Callable[[list[dict[str, Any]]], bool],
        *,
        timeout: float = 20.0,
    ) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = self.selector.select(deadline - time.monotonic())
            if not ready:
                break
            for key, _ in ready:
                line = key.fileobj.readline()
                if not line:
                    self.selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    self.stderr.append(line)
                    if sum(map(len, self.stderr)) > 64 * 1024:
                        raise CanaryFailure("runtime stderr exceeded its canary bound")
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError as error:
                    raise CanaryFailure("runtime polluted stdout") from error
                if not isinstance(frame, dict):
                    raise CanaryFailure("runtime emitted a non-object frame")
                frames.append(frame)
                if predicate(frames):
                    return frames
            if self.process.poll() is not None and not self.selector.get_map():
                break
        raise CanaryFailure(
            f"runtime boundary timed out or exited; returncode={self.process.poll()} "
            f"stderr_bytes={sum(map(len, self.stderr))}"
        )

    def close(self) -> None:
        self.selector.close()
        if self.process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)


def response_seen(request_id: str) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda frames: any(frame.get("id") == request_id for frame in frames)


def response(frames: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    matches = [frame for frame in frames if frame.get("id") == request_id]
    if len(matches) != 1:
        raise CanaryFailure(f"expected one response for {request_id}")
    return matches[0]


def launch(runtime_dir: Path, root: Path, env: dict[str, str]) -> RuntimePeer:
    return RuntimePeer(
        [
            shutil_which("node"),
            str(root / "vendor/deepseek-harness/apps/cli/lib/bin.js"),
            "--profile",
            "sdk",
            "--patch",
            str(root / "harness-runtime/profile/pharos-safe.cordis.patch.yml"),
        ],
        runtime_dir / "workspace",
        env,
    )


def initialize(peer: RuntimePeer, cwd: Path, model: str, request_id: str) -> dict[str, Any]:
    peer.send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "cwd": str(cwd),
                "provider": CANARY_PROVIDER,
                "model": model,
            },
        }
    )
    return response(peer.collect_until(response_seen(request_id)), request_id)


def shutdown(peer: RuntimePeer) -> None:
    peer.send({"jsonrpc": "2.0", "id": "shutdown", "method": "shutdown"})
    result = response(peer.collect_until(response_seen("shutdown")), "shutdown")
    if result.get("result") != {}:
        raise CanaryFailure("shutdown did not return the empty result")
    assert peer.process.stdin is not None
    peer.process.stdin.close()
    peer.process.wait(timeout=10)
    if peer.process.returncode != 0:
        raise CanaryFailure(f"runtime exited with {peer.process.returncode}")


def verify_prompt(peer: RuntimePeer) -> None:
    peer.send(
        {
            "jsonrpc": "2.0",
            "id": "prompt",
            "method": "session/prompt",
            "params": {
                "sessionId": "pharos-canary",
                "contentBlocks": [{"type": "text", "text": "canary"}],
            },
        }
    )

    def finished(frames: list[dict[str, Any]]) -> bool:
        return response_seen("prompt")(frames) and any(
            frame.get("method") == "session.status"
            and frame.get("params", {}).get("sessionId") == "pharos-canary"
            and frame.get("params", {}).get("status") == "idle"
            for frame in frames
        )

    frames = peer.collect_until(finished)
    prompt_response = response(frames, "prompt")
    message_id = prompt_response.get("result", {}).get("messageId")
    if not isinstance(message_id, str) or not message_id:
        raise CanaryFailure("prompt did not return a message id")
    if any(str(frame.get("method", "")).startswith("subagent.") for frame in frames):
        raise CanaryFailure("safe canary emitted a subagent notification")
    events = [
        frame["params"]["event"]
        for frame in frames
        if frame.get("method") == "session.event"
        and isinstance(frame.get("params"), dict)
        and isinstance(frame["params"].get("event"), dict)
    ]
    if [event.get("seq") for event in events] != list(range(len(events))):
        raise CanaryFailure("canary event sequence is not contiguous")
    if any(str(event.get("type", "")).startswith("tool/") for event in events):
        raise CanaryFailure("safe canary emitted a model-facing tool event")
    receipts = [event for event in events if event.get("type") == "agent/inbox/spliced"]
    inserted = receipts[0].get("data", {}).get("inserted", []) if receipts else []
    if not inserted or inserted[0].get("id") != message_id:
        raise CanaryFailure("prompt receipt did not bind the submitted message")
    assistants = [event for event in events if event.get("type") == "assistant/message"]
    if len(assistants) != 1:
        raise CanaryFailure("canary did not emit exactly one assistant message")
    assistant = assistants[0]
    assistant_data = assistant.get("data", {})
    expected_source = {"kind": "model", "provider": CANARY_PROVIDER, "model": CANARY_MODEL}
    if assistant.get("surfaceOp") != "append":
        raise CanaryFailure("assistant output was not appended to the surface")
    if assistant_data.get("message", {}).get("source") != expected_source:
        raise CanaryFailure("assistant provenance does not match the fake route")
    if assistant_data.get("message", {}).get("content") != [
        {"type": "text", "text": CANARY_TEXT}
    ]:
        raise CanaryFailure("assistant canary payload changed")
    if assistant_data.get("usage") != {"inputTokens": 8, "outputTokens": 7}:
        raise CanaryFailure("assistant canary usage changed")
    ends = [event for event in events if event.get("type") == "turn/end"]
    if len(ends) != 1 or ends[0].get("data", {}).get("reason") != {"kind": "completed"}:
        raise CanaryFailure("canary turn did not complete exactly once")
    contexts = [event for event in events if event.get("type") == "request/context"]
    if len(contexts) != 1 or contexts[0].get("data") != {
        "provider": CANARY_PROVIDER,
        "model": CANARY_MODEL,
    }:
        raise CanaryFailure("request context does not match the fake route")
    headers = [event for event in events if event.get("type") == "request/header"]
    config = headers[0].get("data", {}).get("header", {}).get("config", {}) if headers else {}
    if config.get("provider") != CANARY_PROVIDER or config.get("model") != CANARY_MODEL:
        raise CanaryFailure("request header does not match the fake route")
    if headers[0].get("data", {}).get("header", {}).get("tools") if headers else False:
        raise CanaryFailure("safe canary request exposed tools")


def shutil_which(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise CanaryFailure(f"required executable is missing: {name}")
    return resolved


def run(root: Path) -> None:
    cli = root / "vendor/deepseek-harness/apps/cli/lib/bin.js"
    if not cli.is_file():
        raise CanaryFailure("the pinned Harness snapshot must be built before the canary")
    with tempfile.TemporaryDirectory(prefix="pharos-dsh-canary-") as directory:
        runtime_dir = Path(directory)
        (runtime_dir / "home").mkdir()
        (runtime_dir / "workspace").mkdir()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(runtime_dir / "home"),
            "DSH_HOME": str(runtime_dir / "dsh"),
            "DSH_TELEMETRY_DISABLED": "1",
            "NODE_ENV": "production",
        }
        install = subprocess.run(
            [
                shutil_which("node"),
                str(cli),
                "plugin",
                "--profile",
                "sdk",
                "add",
                f"file:{root / 'harness-runtime/bundles/pharos-fake'}",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if install.returncode != 0:
            raise CanaryFailure(f"failed to install fake bundle: {install.stderr[-2000:]}")
        effective = subprocess.run(
            [
                shutil_which("node"),
                str(cli),
                "--profile",
                "sdk",
                "--patch",
                str(root / "harness-runtime/profile/pharos-safe.cordis.patch.yml"),
                "--dump-config",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if effective.returncode != 0:
            raise CanaryFailure(f"failed to compose fake profile: {effective.stderr[-2000:]}")
        effective_path = runtime_dir / "effective.yml"
        effective_path.write_text(effective.stdout, encoding="utf-8")
        policy = subprocess.run(
            [
                sys.executable,
                str(root / "harness-runtime/scripts/check-profile.py"),
                "--effective-config",
                str(effective_path),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if policy.returncode != 0:
            raise CanaryFailure(f"effective profile failed policy: {policy.stderr[-2000:]}")

        peer = launch(runtime_dir, root, env)
        try:
            init = initialize(peer, runtime_dir / "workspace", CANARY_MODEL, "initialize")
            if init.get("result", {}).get("serverInfo") != {
                "name": "deepseek-harness-sdk-runtime",
                "version": "0.0.1",
            }:
                raise CanaryFailure("SDK server identity changed")
            verify_prompt(peer)
            shutdown(peer)
        finally:
            peer.close()

        wrong = launch(runtime_dir, root, env)
        try:
            rejected = initialize(wrong, runtime_dir / "workspace", "not-canary", "wrong-model")
            if "error" not in rejected or "result" in rejected:
                raise CanaryFailure("SDK initialize accepted an unapproved fake model")
            shutdown(wrong)
        finally:
            wrong.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        run(args.root.resolve())
    except (CanaryFailure, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: real DSH loader completed the deterministic Pharos canary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
