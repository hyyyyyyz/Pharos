"""Public desktop update advertisement.

The desktop client asks this endpoint which desktop build is the newest. It is
deliberately public: the check must work before sign-in, because a user who is
signed out is the one most likely to be running an old build. The payload is
small and contains nothing an anonymous caller could not read on the public
release page anyway.

Two authorities, in order:

1. ``PHAROS_DESKTOP_UPDATE_VERSION_OVERRIDE`` — the operator's explicit pin.
   Deterministic, offline, and how a deployment advertises a release before or
   without GitHub.
2. GitHub Releases — the repo's newest ``desktop-v*`` tag. Fetched lazily and
   cached for an hour, because the release cadence is days and every desktop
   client checks this endpoint on a schedule.

When neither yields a version, the endpoint answers ``version: null``. The
client treats that as "no update advertised" — an explicit no, not an error.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter

from pharos.config import Settings, get_settings

router = APIRouter(prefix="/api/updates", tags=["updates"])

#: Advertise only what looks like a desktop release: desktop-vX.Y.Z.
_TAG_VERSION = re.compile(r"^desktop-v(\d+\.\d+\.\d+)$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

_GITHUB_API_TIMEOUT = 10.0
_GITHUB_CACHE_TTL = 60 * 60
_MAX_NOTES_CHARS = 2_000
_USER_AGENT = "Pharos/0.1 (desktop update check; +https://github.com/hyyyyyyz/Pharos)"

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


def _github_payload(settings: Settings, now: float) -> dict[str, Any] | None:
    global _github_cache
    if _github_cache is not None and now - _github_cache[1] < _GITHUB_CACHE_TTL:
        return _github_cache[0]

    payload: dict[str, Any] = _payload(None, None, None)
    try:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
        token = settings.desktop_update_github_token
        if token:
            # A private repository answers the anonymous releases API with
            # 404; a read-only token turns the automatic fallback back on.
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{settings.desktop_update_repo}/releases?per_page=30",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=_GITHUB_API_TIMEOUT) as response:  # noqa: S310
            raw = response.read(4 * 1024 * 1024)
        releases = json.loads(raw.decode("utf-8"))
        if not isinstance(releases, list):
            # A body that is not a list is not a release list; treat it the way
            # a network failure is treated rather than 500ing the check.
            releases = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            match = _TAG_VERSION.fullmatch(str(release.get("tag_name") or ""))
            if match is None:
                continue
            notes = str(release.get("body") or "").strip() or None
            if notes is not None:
                notes = notes[:_MAX_NOTES_CHARS]
            payload = _payload(match.group(1), str(release.get("html_url") or ""), notes)
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
        # than taking the update check down with a dependency.
        payload = _payload(None, None, None)
    _github_cache = (payload, now)
    return payload


@router.get("/desktop/latest")
def desktop_latest() -> dict[str, Any]:
    """The newest advertised desktop build, or an explicit "none".

    Deliberately independent of app state (``get_settings`` is cached at module
    level, not fetched from the request): the update check must work on any
    deployment shape, and it needs nothing per-request.
    """
    settings = get_settings()
    override = _override_payload(settings)
    if override is not None:
        return override
    return _github_payload(settings, time.monotonic()) or _payload(None, None, None)
