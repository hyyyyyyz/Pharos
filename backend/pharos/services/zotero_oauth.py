"""Zotero OAuth 1.0a key exchange using only the Python standard library.

Zotero uses OAuth for the three-step consent handshake, then returns a normal
Zotero API key for subsequent Web API calls. Those calls continue to use the
existing ``Zotero-API-Key`` header client in :mod:`pharos.services.zotero`.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from oauthlib.oauth1 import SIGNATURE_HMAC_SHA1, SIGNATURE_TYPE_AUTH_HEADER, Client

REQUEST_TOKEN_URL = "https://www.zotero.org/oauth/request"
ACCESS_TOKEN_URL = "https://www.zotero.org/oauth/access"
AUTHORIZE_URL = "https://www.zotero.org/oauth/authorize"

_TIMEOUT = 20.0
_MAX_BODY = 64 * 1024


class ZoteroOAuthError(RuntimeError):
    """The OAuth provider rejected or could not complete an exchange."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """OAuth endpoints are fixed; redirects are provider failures, not targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class RequestToken:
    token: str
    secret: str


@dataclass(frozen=True)
class AccessToken:
    api_key: str
    user_id: str
    username: str | None = None


def authorization_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str | None = None,
    token_secret: str = "",
    callback: str | None = None,
    verifier: str | None = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Build the RFC 5849 header through oauthlib's audited normalizer."""
    signer = Client(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=token,
        resource_owner_secret=token_secret or None,
        callback_uri=callback,
        verifier=verifier,
        signature_method=SIGNATURE_HMAC_SHA1,
        signature_type=SIGNATURE_TYPE_AUTH_HEADER,
        nonce=nonce,
        timestamp=str(timestamp) if timestamp is not None else None,
    )
    _uri, headers, _body = signer.sign(
        url,
        http_method=method.upper(),
        body="",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    authorization = headers.get("Authorization")
    if not isinstance(authorization, str):  # pragma: no cover - oauthlib contract
        raise ZoteroOAuthError("Could not sign the Zotero OAuth request.")
    return authorization


def _post(url: str, authorization: str, *, timeout: float = _TIMEOUT) -> dict[str, str]:
    request = urllib.request.Request(
        url,
        data=b"",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/x-www-form-urlencoded",
            "User-Agent": "Pharos/0.0.1 (+https://github.com/hyyyyyyz/Pharos)",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        exc.read(_MAX_BODY)
        exc.close()
        raise ZoteroOAuthError(f"Zotero rejected the OAuth exchange (HTTP {exc.code}).") from None
    except Exception as exc:
        raise ZoteroOAuthError("Could not reach Zotero to complete OAuth.") from exc
    if len(raw) > _MAX_BODY:
        raise ZoteroOAuthError("Zotero returned an unexpectedly large OAuth response.")
    try:
        values = urllib.parse.parse_qs(
            raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ZoteroOAuthError("Zotero returned a malformed OAuth response.") from exc
    return {key: items[-1] for key, items in values.items() if items}


def request_token(
    consumer_key: str,
    consumer_secret: str,
    callback_url: str,
    *,
    timeout: float = _TIMEOUT,
) -> RequestToken:
    header = authorization_header(
        "POST",
        REQUEST_TOKEN_URL,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        callback=callback_url,
    )
    payload = _post(REQUEST_TOKEN_URL, header, timeout=timeout)
    token = payload.get("oauth_token")
    secret = payload.get("oauth_token_secret")
    if not token or not secret or payload.get("oauth_callback_confirmed") != "true":
        raise ZoteroOAuthError("Zotero did not return a usable temporary credential.")
    return RequestToken(token=token, secret=secret)


def authorization_url(token: str) -> str:
    """The consent screen, prefilled with the least privilege Pharos needs."""
    query = urllib.parse.urlencode(
        {
            "oauth_token": token,
            "name": "Pharos",
            "library_access": "1",
            "notes_access": "0",
            "write_access": "0",
            "all_groups": "none",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def access_token(
    consumer_key: str,
    consumer_secret: str,
    request_token_value: str,
    request_token_secret: str,
    verifier: str,
    *,
    timeout: float = _TIMEOUT,
) -> AccessToken:
    header = authorization_header(
        "POST",
        ACCESS_TOKEN_URL,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        token=request_token_value,
        token_secret=request_token_secret,
        verifier=verifier,
    )
    payload = _post(ACCESS_TOKEN_URL, header, timeout=timeout)
    # Zotero historically returns the same long-lived API key in the OAuth token
    # pair. Prefer the secret (the field used by Zotero's official example), but
    # accept the token for compatibility and verify it against /keys/current
    # before anything is stored.
    api_key = payload.get("oauth_token_secret") or payload.get("oauth_token")
    user_id = payload.get("userID") or payload.get("userId") or payload.get("user_id")
    if not api_key or not user_id:
        raise ZoteroOAuthError("Zotero did not return an API key and user identity.")
    username = payload.get("username") or None
    return AccessToken(api_key=api_key, user_id=str(user_id), username=username)
