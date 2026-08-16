"""Public desktop update advertisement and installer streaming.

The desktop client asks which desktop build is the newest, and downloads that
build's installer through the service rather than from GitHub directly: the
repository may be private, and the client has no GitHub credential. The check
endpoint is deliberately public (a signed-out user is the one most likely to
be on an old build); the download endpoint is likewise public because the
payload is the same release asset any GitHub user can fetch.

Authorities, in order:

1. ``PHAROS_DESKTOP_UPDATE_VERSION_OVERRIDE`` — the operator's explicit pin.
   Deterministic, offline, and how a deployment advertises a release without
   GitHub. It pins only the advertised version; the download endpoint still
   resolves the matching release's assets from GitHub (with the optional
   token for private repositories).
2. GitHub Releases — the repo's newest ``desktop-v*`` tag. Fetched lazily and
   cached for an hour.

When neither yields a version, both endpoints answer ``version: null``. The
client treats that as "no update advertised" — an explicit no, not an error.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from pharos.config import Settings, get_settings

router = APIRouter(prefix="/api/updates", tags=["updates"])

#: Advertise only what looks like a desktop release: desktop-vX.Y.Z.
_TAG_VERSION = re.compile(r"^desktop-v(\d+\.\d+\.\d+)$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

_GITHUB_API_TIMEOUT = 10.0
_GITHUB_CACHE_TTL = 60 * 60
_MAX_NOTES_CHARS = 2_000
_USER_AGENT = "Pharos/0.1 (desktop update check; +https://github.com/hyyyyyz/Pharos)"

#: The installer asset names the release workflow publishes, per platform.
#: The macOS zip is the self-install bundle (the .app itself); the dmg is for
#: manual installs. Windows and Linux stay portable archives; the workflow
#: names them with an underscore between version and platform.
_ASSET_SUFFIXES = {
    "mac": ("-mac.zip",),
    "windows": ("_win-x64.zip",),
    "linux": ("_linux-x86_64.tar.xz",),
}

#: (payload, monotonic timestamp) of the last GitHub lookup. Module-level by
#: design: one API worker serves all of this; a multi-worker deployment would
#: simply re-fetch once per worker per hour, which is still far below any rate
#: limit that matters.
_github_cache: tuple[dict[str, Any], float] | None = None


def _payload(version: str | None, url: str | None, notes: str | None) -> dict[str, Any]:
    return {"version": version, "url": url, "notes": notes}


def _override_payload(settings: Settings) -> dict[str, Any] | None:
    pinned = settings.desktop_update_version_override
    if not pinned:
        return None
    pinned = pinned.strip()
    if not _VERSION.fullmatch(pinned):
        # A malformed pin is an operator error; advertising it would make every
        # client's version comparison do something undefined. Say nothing.
        return None
    url = f"https://github.com/{settings.desktop_update_repo}/releases/tag/desktop-v{pinned}"
    return _payload(pinned, url, None)


def _headers(settings: Settings) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = settings.desktop_update_github_token
    if token:
        # A private repository answers the anonymous releases API with 404;
        # a read-only token turns the automatic fallback back on.
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_releases(settings: Settings) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{settings.desktop_update_repo}/releases?per_page=30",
        headers=_headers(settings),
    )
    # GitHub's replicas can disagree briefly after a visibility change: one
    # request lands on a stale replica answering 404 for a public repository
    # while the next lands on a healthy one. Retry across replicas with
    # growing gaps; a persistent failure still propagates and is never cached.
    last_error: Exception | None = None
    for delay in (0, 1, 2, 4):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(
                request, timeout=_GITHUB_API_TIMEOUT
            ) as response:  # noqa: S310
                raw = response.read(4 * 1024 * 1024)
            releases = json.loads(raw.decode("utf-8"))
            if not isinstance(releases, list):
                releases = []
            return [release for release in releases if isinstance(release, dict)]
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            last_error = error
    raise last_error  # type: ignore[misc]


def _github_payload(settings: Settings, now: float) -> dict[str, Any] | None:
    global _github_cache
    if _github_cache is not None and now - _github_cache[1] < _GITHUB_CACHE_TTL:
        return _github_cache[0]

    payload: dict[str, Any] = _payload(None, None, None)
    try:
        for release in _fetch_releases(settings):
            match = _TAG_VERSION.fullmatch(str(release.get("tag_name") or ""))
            if match is None:
                continue
            notes = str(release.get("body") or "").strip() or None
            if notes is not None:
                notes = notes[:_MAX_NOTES_CHARS]
            payload = _payload(match.group(1), str(release.get("html_url") or ""), notes)
            payload["assets"] = _asset_map(release, match.group(1))
            break
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ):
        # GitHub unreachable, rate-limited, or returning an unexpected shape.
        # The endpoint must stay available: answer "no update advertised" rather
        # than taking the update check down with a dependency. The failure is
        # deliberately NOT cached: a transient blip must not pin every client
        # to "no update" for the next hour.
        return _payload(None, None, None)
    if payload.get("version") is None:
        # Nothing found is not a durable answer either -- the next check
        # retries instead of replaying an empty result for the cache window.
        return payload
    _github_cache = (payload, now)
    return payload


def _asset_map(release: dict, version: str) -> dict[str, dict[str, Any]]:
    """The installer assets of one release, keyed by platform.

    Includes the SHA-256 digest GitHub publishes so the client can verify the
    download before it replaces anything.
    """
    assets: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if not name.startswith(f"Pharos-{version}"):
            continue
        for platform, suffixes in _ASSET_SUFFIXES.items():
            if name.endswith(suffixes[0]):
                assets[platform] = {
                    "name": name,
                    "url": str(asset.get("browser_download_url") or ""),
                    "size": int(asset.get("size") or 0),
                    "sha256": str(asset.get("digest") or ""),
                }
    return assets


def _release_asset(settings: Settings, platform: str, version: str) -> dict[str, Any]:
    """Resolve one release's installer asset for a platform.

    Uses a fresh, uncached GitHub lookup: the check endpoint's cache does not
    carry asset digests for the operator-pinned path, and a download happens
    once per release per client, so caching here would save nothing.
    """
    if platform not in _ASSET_SUFFIXES:
        raise HTTPException(status_code=400, detail="unknown platform")
    try:
        for release in _fetch_releases(settings):
            match = _TAG_VERSION.fullmatch(str(release.get("tag_name") or ""))
            if match is None or match.group(1) != version:
                continue
            asset = _asset_map(release, version).get(platform)
            if asset is None:
                raise HTTPException(status_code=404, detail="no installer for this platform")
            return asset
    except HTTPException:
        raise
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        raise HTTPException(status_code=502, detail="installer unavailable") from error
    raise HTTPException(status_code=404, detail="release not found")


def _cached_asset_dir(settings: Settings) -> Path:
    directory = Path(settings.data_dir) / "update-assets"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cached_asset_path(settings: Settings, asset: dict[str, Any]) -> Path:
    #: Keyed by the GitHub-published digest, so a new release can never serve
    #: a stale file and a re-upload of the same bytes reuses the cache.
    return _cached_asset_dir(settings) / f"{asset['name']}.{asset['sha256'][:16]}"


def _fetch_asset_to_cache(settings: Settings, asset: dict[str, Any]) -> Path:
    """Download one installer into the server-side cache, with retries.

    GitHub's replicas can disagree after a visibility change, and a
    multi-hundred-MB stream cannot start over casually; several attempts with
    backoff re-resolve each time.
    """
    target = _cached_asset_path(settings, asset)
    if target.exists():
        return target
    part = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(asset["url"], headers=_headers(settings))
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                with part.open("wb") as out:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            digest = hashlib.sha256(part.read_bytes()).hexdigest()
            if asset.get("sha256") and digest != asset["sha256"]:
                raise ValueError("downloaded installer failed its digest check")
            part.rename(target)
            return target
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            http.client.HTTPException,
            ValueError,
        ) as error:
            last_error = error
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(2.0 * (attempt + 1))
    raise last_error  # type: ignore[misc]


_refill_lock = threading.Lock()
_refill_queued: set[str] = set()


def _schedule_background_refill(settings: Settings, asset: dict[str, Any]) -> None:
    """Keep trying to fill the cache after a failed request.

    During GitHub's post-visibility-change flapping a request can fail while
    a minute later the same fetch succeeds. Rather than making every client
    retry against the flapping upstream, one daemon thread keeps the cache
    warm: the next good window fills it, and every later request is served
    from disk.
    """
    key = asset["name"]
    with _refill_lock:
        if key in _refill_queued:
            return
        _refill_queued.add(key)

    def work() -> None:
        try:
            for _ in range(90):  # up to ~90 minutes of retrying
                try:
                    _fetch_asset_to_cache(settings, asset)
                    return
                except Exception:  # noqa: BLE001 - keep trying across windows
                    time.sleep(60)
        finally:
            with _refill_lock:
                _refill_queued.discard(key)

    threading.Thread(target=work, daemon=True, name=f"update-cache-{key}").start()


@router.get("/desktop/latest")
def desktop_latest() -> dict[str, Any]:
    """The newest advertised desktop build, or an explicit "none"."""
    settings = get_settings()
    override = _override_payload(settings)
    if override is not None:
        return override
    return _github_payload(settings, time.monotonic()) or _payload(None, None, None)


@router.get("/desktop/download")
def desktop_download(platform: str, version: str | None = None):
    """Stream one platform's installer for the newest (or named) release.

    ``version`` is optional: it pins the download to the release the client
    was told about, which keeps a download started before a new release
    landed from silently switching payloads mid-flight.
    """
    settings = get_settings()
    # Validate the request shape before anything touches the network.
    if platform not in _ASSET_SUFFIXES:
        raise HTTPException(status_code=400, detail="unknown platform")
    if version is None:
        advertised = desktop_latest()
        version = advertised.get("version")
    if not version or not _VERSION.fullmatch(str(version)):
        raise HTTPException(status_code=404, detail="no update advertised")
    asset = _release_asset(settings, platform, str(version))
    # Deterministic: after the first successful upstream fetch the installer
    # is served from the server-side cache, so GitHub's flaky edges can no
    # longer interrupt a client mid-update.
    try:
        cached = _fetch_asset_to_cache(settings, asset)
    except Exception as error:  # noqa: BLE001
        # The client can retry; meanwhile a background thread keeps the cache
        # warm so a later attempt succeeds without racing the flapping edge.
        _schedule_background_refill(settings, asset)
        raise HTTPException(status_code=502, detail="installer unavailable") from error
    return FileResponse(
        cached,
        media_type="application/octet-stream",
        headers={
            "X-Pharos-Asset-Name": asset["name"],
            "X-Pharos-Asset-SHA256": asset["sha256"],
            "Cache-Control": "no-store",
        },
    )
