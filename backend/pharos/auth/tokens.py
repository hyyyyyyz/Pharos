"""Stateless access tokens (JWT, HS256).

Stateless is a deliberate trade: no session table, no per-request write, and a
token that any worker can validate on its own. The price is that a token cannot
be individually revoked, which is what :attr:`User.token_epoch` buys back — the
epoch travels in the token, is compared against the database on every request,
and bumping it invalidates every token that account ever issued (password
change, logout-everywhere, incident response).

The one non-negotiable here is the algorithm allow-list. A JWT carries its own
``alg`` header, and a verifier that trusts it accepts ``alg=none`` (no signature
at all) or an RS256 token whose "public key" is the HMAC secret. Both are total
authentication bypasses, and both are prevented by passing an explicit
``algorithms=["HS256"]`` to :func:`jwt.decode`. Never widen that list.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from pharos.config import auth_secret_or_die, get_settings
from pharos.db.models import User

#: The only algorithm this application signs or accepts.
ALGORITHM = "HS256"

#: Value of the ``token_type`` claim on an access token. Every token this app
#: ever mints carries a type, so a token issued for some future purpose (an
#: email-confirmation link, a share URL) can never be replayed as a session
#: credential just because it happens to be signed by the same key.
ACCESS_TOKEN_TYPE = "access"

#: Claims a token must carry to be considered well-formed at all. Checking for
#: presence up front means a missing claim is an authentication failure rather
#: than a ``KeyError`` somewhere further in.
_REQUIRED_CLAIMS = ("sub", "email", "epoch", "token_type", "iat", "exp")

#: Addresses that mean "this process is only reachable from this machine".
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


class TokenError(Exception):
    """Base class for every reason a token was not accepted."""


class InvalidTokenError(TokenError):
    """Malformed, wrongly signed, wrong algorithm, or missing a claim."""


class TokenExpiredError(TokenError):
    """Well-formed and correctly signed, but past its ``exp``."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The validated contents of an access token.

    Frozen because callers must not be able to edit a claim and have it look
    like something the signature covered.
    """

    user_id: str
    email: str
    epoch: int
    token_type: str
    issued_at: datetime
    expires_at: datetime


def _host_of(bind: str) -> str | None:
    """The host part of a bind specification, or ``None`` if there is none.

    Accepts every spelling the supported servers use: a bare host
    (``0.0.0.0``, uvicorn's ``--host``), ``host:port`` (gunicorn/hypercorn
    ``--bind``), and a bracketed IPv6 literal (``[::]:8000``). A ``unix:`` or
    bare-path bind is a filesystem socket, which is not network-reachable, so it
    yields ``None`` — the same answer as "nothing was specified".
    """
    value = bind.strip()
    if not value or value.startswith(("unix:", "fd://", "/", "./")):
        return None
    if value.startswith("["):  # [::1]:8000 or [::]:8000
        end = value.find("]")
        return value[1:end] if end > 0 else None
    # Split a trailing :port off, but only when the remainder is not itself an
    # unbracketed IPv6 literal (which is full of colons).
    head, sep, tail = value.rpartition(":")
    if sep and head and ":" not in head and tail.isdigit():
        return head
    return value


def _bound_publicly() -> bool:
    """Best guess at whether this process is reachable from outside the host.

    ``auth_secret_or_die`` refuses to invent a key when the answer is yes, so
    this must not under-report a public bind — a false "loopback" is the
    dangerous direction, because it silently substitutes an ephemeral per-process
    key for the missing secret. That is why every bind flag the deployment might
    plausibly use is checked, not just uvicorn's: ``gunicorn -b 0.0.0.0:8848``
    binds every interface while naming it nothing like ``--host``, and under
    multiple workers an ephemeral key is not merely lost on restart, it differs
    between workers, so a token minted by one is rejected by the next.

    Servers in scope default to loopback (uvicorn) or to an explicit bind that
    this function reads, so an unspecified host genuinely is loopback-only.
    """
    host: str | None = None
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg in ("--host", "--bind", "-b") and i + 1 < len(argv):
            host = _host_of(argv[i + 1]) or host
        elif arg.startswith(("--host=", "--bind=")):
            host = _host_of(arg.split("=", 1)[1]) or host
    if host is None:
        raw = (
            os.environ.get("PHAROS_HOST")
            or os.environ.get("HOST")
            or os.environ.get("UVICORN_HOST")
            or os.environ.get("GUNICORN_BIND")
            or os.environ.get("BIND")
        )
        host = _host_of(raw) if raw else None
    if not host:
        return False
    return host.strip().strip("[]").lower() not in _LOOPBACK_HOSTS


def _signing_key() -> str:
    """The HMAC key, or a hard failure if this deployment has no secret."""
    return auth_secret_or_die(_bound_publicly())


def _ttl() -> timedelta:
    """Token lifetime, clamped to something that can actually be used.

    A misconfigured zero or negative TTL would mint tokens that are already
    expired, which presents as "login succeeds then nothing works" — a confusing
    failure worth spending one line to make impossible.
    """
    minutes = get_settings().access_token_ttl_minutes
    return timedelta(minutes=max(1, int(minutes)))


def issue_access_token(user: User) -> tuple[str, datetime]:
    """Mint an access token for ``user``.

    Returns the encoded token and its expiry, so the caller can hand the client
    an absolute time rather than making it decode the token to find out.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + _ttl()
    payload = {
        "sub": user.id,
        "email": user.email,
        "epoch": int(user.token_epoch or 0),
        "token_type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> TokenClaims:
    """Validate ``token`` and return its claims.

    Raises :class:`TokenExpiredError` when it is merely stale and
    :class:`InvalidTokenError` for everything else. Note what this does *not*
    do: it never touches the database, so a caller still has to confirm the user
    exists, is active, and that ``epoch`` still matches.
    """
    if not isinstance(token, str) or not token:
        raise InvalidTokenError("Missing token")
    try:
        payload = jwt.decode(
            token,
            _signing_key(),
            algorithms=[ALGORITHM],  # allow-list: see the module docstring
            options={
                "require": list(_REQUIRED_CLAIMS),
                "verify_signature": True,
                "verify_exp": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, unexpected algorithm, malformed segments, and
        # missing required claims. The reason is not surfaced to the client: it
        # would tell an attacker which part of a forgery attempt was wrong.
        raise InvalidTokenError("Token is not valid") from exc

    if payload.get("token_type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Token is not an access token")

    user_id = payload.get("sub")
    email = payload.get("email")
    epoch = payload.get("epoch")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidTokenError("Token is not valid")
    if not isinstance(email, str):
        raise InvalidTokenError("Token is not valid")
    # bool is an int subclass; a JSON `true` epoch must not pass as 1.
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        raise InvalidTokenError("Token is not valid")

    return TokenClaims(
        user_id=user_id,
        email=email,
        epoch=epoch,
        token_type=ACCESS_TOKEN_TYPE,
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
    )
