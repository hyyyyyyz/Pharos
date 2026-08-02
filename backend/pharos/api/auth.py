"""Authentication API: register, login, and self-service account management.

Everything here lives under ``/api/auth``. The endpoints are deliberately few —
this is the whole surface on which an attacker can guess a credential, so every
addition needs a reason. In particular there is no password reset and no refresh
token; see the module notes at the bottom of the file for why.

A running theme is that failures are *uninformative on purpose*. Login answers
identically whether the email is unknown or the password is wrong, and spends
the same amount of CPU either way, because both the status code and the response
time are channels that leak the membership of the user table.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session, get_settings
from pharos.api.schemas import as_utc
from pharos.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from pharos.auth.tokens import issue_access_token
from pharos.config import Settings
from pharos.db.models import Paper, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: Pragmatic address shape: one ``@``, no whitespace, and a dotted domain. This
#: is not RFC 5322 — a full grammar accepts things no mail server will, and the
#: only authoritative validation is sending mail, which Pharos does not do. The
#: goal is to reject obvious typos and anything that would look strange stored.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$")

#: ``users.email`` is String(320) — the RFC's maximum path length.
_MAX_EMAIL_LENGTH = 320
#: ``users.display_name`` is String(128).
_MAX_DISPLAY_NAME = 128

#: Spelled as a literal because Starlette renamed the constant
#: (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT) and deprecated the old name;
#: the number is the one thing that is stable across both.
_UNPROCESSABLE = 422

#: The single message every failed login returns, whatever actually went wrong.
_LOGIN_FAILED = "Incorrect email or password"


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class UserOut(BaseModel):
    """A user as the API exposes them.

    Constructed field-by-field rather than from the ORM object, so that adding a
    sensitive column to :class:`~pharos.db.models.User` later cannot silently
    start serialising it. ``password_hash`` must never appear here: a digest is
    still an offline-crackable credential.
    """

    id: str
    email: str
    display_name: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None
    #: Whether whole-document PDF translation is offered to this account. The
    #: client keys the translate action, the status column and the bilingual
    #: reading modes off this, so it ships with every user payload — register
    #: and login included — rather than needing a second round trip before the
    #: UI knows which controls to draw.
    pdf_translation: bool = True
    #: Operator account. Exposed so a future admin UI can key off it; grants no
    #: special access today (there are no admin-gated endpoints yet).
    is_admin: bool = False


class AuthStatusOut(BaseModel):
    """What an unauthenticated client may know about this instance.

    Deliberately just the one field. Anything a caller can read without a token
    is public, so this answers only the question a sign-in screen actually has —
    whether to offer a sign-up form — and says nothing about who is registered,
    how many there are, or how the instance is configured.
    """

    allow_registration: bool


class AuthResponse(BaseModel):
    """What register and login hand back: a token and who it belongs to."""

    token: str
    expires_at: datetime
    user: UserOut


#: Password fields carry NO pydantic length constraint, and that is a security
#: decision rather than an oversight. Pydantic's validation errors embed the
#: rejected value under ``input``, and FastAPI serialises them straight into the
#: 422 body — so a ``max_length`` on a password means every over-long attempt
#: gets the password itself echoed back, into the client, into any access log
#: that records bodies, and into whatever error tracker sits in front of the app.
#: With a 10 MB body that is also a neat amplifier: 10 MB in, 10 MB back out.
#:
#: The bounds are therefore enforced *in code* instead, by
#: :func:`~pharos.auth.passwords.hash_password` (which raises
#: :class:`PasswordPolicyError`, carrying the rule and never the value) and by
#: :func:`~pharos.auth.passwords.verify_password` (which returns ``False`` for
#: an over-long input without ever handing it to argon2). Nothing reaches the
#: memory-hard KDF unbounded; only the *error path* changed.
#:
#: The bounds are still *documented* (a description carries no validation and so
#: produces no error to echo), so the OpenAPI schema and any generated client
#: still tell a user what the rule is.
Password = Annotated[
    str,
    Field(
        description=(
            f"Between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters. "
            "Checked server-side; a rejection never echoes the value back."
        ),
    ),
]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(max_length=_MAX_EMAIL_LENGTH)]
    password: Password
    display_name: Annotated[str, Field(max_length=_MAX_DISPLAY_NAME)] | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(max_length=_MAX_EMAIL_LENGTH)]
    # Unconstrained for a second reason on top of the echo above: a minimum here
    # would reject a too-short password with a 422 while a wrong one gets a 401,
    # which tells an attacker their guess was at least the right shape. Login
    # validates nothing about the password's form; it either matches the stored
    # digest or it does not.
    password: Password


class UpdateMeRequest(BaseModel):
    """Profile edits. Omitted means "leave alone"; explicit null clears."""

    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str, Field(max_length=_MAX_DISPLAY_NAME)] | None = None
    #: Typed as optional purely so ``exclude_unset`` can tell "not sent" from
    #: "sent as null" — the column is NOT NULL and a preference has no "cleared"
    #: state, so an explicit null is rejected in the handler rather than written.
    pdf_translation: bool | None = None


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Password
    new_password: Password


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def normalize_email(raw: str) -> str:
    """Trim and casefold an address, or reject it.

    Casefolding is what stops ``Ada@x.org`` and ``ada@x.org`` becoming two
    accounts that each believe they own the other's library. It is applied to
    the whole address including the local part: that is technically stricter
    than the RFC, which lets the local part be case-sensitive, but no real mail
    provider treats it that way and the alternative is a confusing near-duplicate
    account. Plus-addressing (``ada+papers@x.org``) is left intact — it is a
    distinct, deliverable address, and collapsing it would let one person block
    another's signup.
    """
    email = raw.strip().casefold()
    if not email or len(email) > _MAX_EMAIL_LENGTH or not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=_UNPROCESSABLE,
            detail="Enter a valid email address",
        )
    return email


def _clean_display_name(value: str | None) -> str | None:
    """Collapse whitespace; blank means "no name", not an empty one."""
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def pdf_translation_enabled(user: User) -> bool:
    """Whether whole-document translation is on for ``user``. NULL counts as on.

    ``users.pdf_translation`` is NOT NULL with a *Python-side* default, which is
    applied on INSERT only — it is not a ``server_default``, so it never reaches
    the DDL. A row written before the column existed therefore holds SQL NULL,
    and ``None`` is falsy: reading the attribute directly would silently opt out
    exactly the long-standing accounts the "default on" decision exists to
    protect, and the product would appear to have deleted its main feature from
    every established user on upgrade.

    So every read of the preference goes through here instead of touching the
    attribute, and unset means on. This mirrors ``int(user.token_epoch or 0)``
    elsewhere in this module: legacy rows predate the column, and the read path
    is what has to absorb that.

    NULL is distinguished from ``False`` explicitly rather than by truthiness, so
    a raw ``0`` arriving from the driver (rather than a converted ``False``)
    still reads as off instead of flipping the feature back on.

    NOTE: this makes the *runtime* correct; it does not make an old database
    openable. See the migration note at the bottom of this module.
    """
    if user.pdf_translation is None:
        return True
    return bool(user.pdf_translation)


def user_out(user: User) -> UserOut:
    """ORM -> API. The only place a ``User`` becomes a response body."""
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=as_utc(user.created_at),
        last_login_at=as_utc(user.last_login_at),
        pdf_translation=pdf_translation_enabled(user),
        is_admin=bool(user.is_admin),
    )


def _auth_response(user: User) -> AuthResponse:
    token, expires_at = issue_access_token(user)
    return AuthResponse(token=token, expires_at=expires_at, user=user_out(user))


def _claim_legacy_papers(session: Session, user: User) -> None:
    """Give every ownerless paper to ``user``.

    ``papers.user_id`` is nullable only because the additive migration had to
    run against a database written before accounts existed. Those rows are the
    library of whoever was running Pharos single-user, and the account they
    create first is theirs — so the first registration adopts them, in the same
    transaction that creates the account. If it were a separate step, a crash in
    between would leave a library permanently invisible to everyone.

    ``WHERE user_id IS NULL`` also makes this safe under a concurrent double
    registration: the loser claims nothing rather than stealing rows.
    """
    session.execute(update(Paper).where(Paper.user_id.is_(None)).values(user_id=user.id))


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("/status", response_model=AuthStatusOut)
def auth_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthStatusOut:
    """Whether this instance is accepting new accounts.

    Anonymous on purpose: a sign-in screen needs the answer before anyone has a
    token, and hiding a sign-up form that would 403 anyway is the difference
    between a closed instance and a broken one.

    Clients must treat an ERROR here as "unknown" rather than "closed" — an
    older self-hosted backend has no such route, and hiding registration because
    a request failed would lock people out of an instance that is in fact open.
    ``POST /register`` remains the authority; this only decides what to draw.
    """
    return AuthStatusOut(allow_registration=bool(settings.allow_registration))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthResponse:
    """Create an account and return a token for it.

    Registration unavoidably reveals that an address is taken — there is no way
    to say "pick another" without saying it. The message stays minimal for that
    reason, and it is the only endpoint that leaks membership.
    """
    if not settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed on this instance",
        )

    email = normalize_email(payload.email)
    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        # str(exc) is the policy rule, never the password itself.
        raise HTTPException(status_code=_UNPROCESSABLE, detail=str(exc)) from exc

    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Counted before the insert: this is "was the table empty", i.e. is this the
    # very first account on this instance.
    is_first_account = session.scalar(select(func.count()).select_from(User)) == 0

    user = User(
        email=email,
        password_hash=password_hash,
        display_name=_clean_display_name(payload.display_name),
        is_active=True,
        last_login_at=datetime.now(timezone.utc),
    )
    session.add(user)
    try:
        # Flush rather than commit: this hits the unique index (closing the race
        # the SELECT above cannot) while leaving the legacy-paper claim below
        # inside the same transaction.
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    if is_first_account:
        # Deliberately not logged with the email or id attached: the interesting
        # fact is the count, and account identifiers do not belong in logs.
        _claim_legacy_papers(session, user)

    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    session: Annotated[Session, Depends(get_session)],
) -> AuthResponse:
    """Exchange an email and password for an access token.

    Every failure — unknown address, wrong password, deactivated account —
    returns the identical 401 and the identical message, and costs one argon2
    verification. The dummy verification on the "no such user" path is the point:
    without it, a missing account answers in microseconds while a real one takes
    ~50ms, and an attacker can enumerate the entire user table by stopwatch.
    """
    email = payload.email.strip().casefold()
    user = session.scalar(select(User).where(User.email == email))

    if user is None:
        dummy_verify(payload.password)
        raise _login_failed()

    if not verify_password(user.password_hash, payload.password):
        raise _login_failed()

    if not user.is_active:
        # Same response as a bad password, on purpose. A distinct "account
        # disabled" would confirm the address exists to anyone who guessed it,
        # which is the enumeration leak this endpoint is built to avoid. The
        # dependency layer rejects deactivated users on every other route too.
        raise _login_failed()

    # Now that the plaintext is momentarily in hand, transparently upgrade a
    # digest made with older (weaker) argon2 parameters.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user.last_login_at = datetime.now(timezone.utc)
    return _auth_response(user)


def _login_failed() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_LOGIN_FAILED,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/me", response_model=UserOut)
def read_me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    """The signed-in user. Also the cheapest way for a client to test a token."""
    return user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UpdateMeRequest,
    user: Annotated[User, Depends(current_user)],
) -> UserOut:
    """Edit the profile. Email is not changeable here.

    Changing the login identifier is an account-takeover-adjacent operation that
    needs address ownership proof (a confirmation mail) to be safe, and Pharos
    sends no mail. Leaving it out is the honest option.
    """
    provided = payload.model_dump(exclude_unset=True)
    if not provided:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided")
    if "display_name" in provided:
        user.display_name = _clean_display_name(provided["display_name"])
    if "pdf_translation" in provided:
        if provided["pdf_translation"] is None:
            # Unlike display_name, this has no "cleared" state to fall back to:
            # the column is NOT NULL, and writing NULL would produce precisely
            # the legacy row that pdf_translation_enabled has to paper over.
            raise HTTPException(
                status_code=_UNPROCESSABLE,
                detail="pdf_translation must be true or false, not null",
            )
        user.pdf_translation = provided["pdf_translation"]
    return user_out(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    user: Annotated[User, Depends(current_user)],
) -> None:
    """Change the password, invalidating every outstanding token.

    The current password is required even though the caller already holds a
    valid token: a token can be stolen, and re-proving the password is what stops
    a stolen one from being upgraded into permanent ownership of the account.

    Bumping ``token_epoch`` kills *all* tokens for this user, the caller's
    included — a single counter cannot spare one, and the alternative (issuing a
    replacement here) would mean a leaked token could survive the very password
    change that was meant to evict it. Clients must sign in again.
    """
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        new_hash = hash_password(payload.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=_UNPROCESSABLE, detail=str(exc)) from exc

    user.password_hash = new_hash
    user.token_epoch = int(user.token_epoch or 0) + 1


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(user: Annotated[User, Depends(current_user)]) -> None:
    """Invalidate every token for this account, on every device.

    Plain logout needs no endpoint — a stateless token is discarded by the client
    dropping it. This exists for the case that actually matters: a device that is
    lost, or a token believed to be copied, where the client can no longer be
    trusted to forget anything.
    """
    user.token_epoch = int(user.token_epoch or 0) + 1


# --------------------------------------------------------------------------
# Deliberately absent
# --------------------------------------------------------------------------
#
# PASSWORD RESET. Pharos sends no email, and a reset flow without a proven
# delivery channel is not a partial feature — it is an account-takeover
# endpoint. (Anything that hands out a credential based on knowing an address,
# or on a "security question", is worse than having no recovery at all.) Until
# there is a mail sender and a single-use, short-lived, hashed-at-rest reset
# token, recovery is an operator action against the database. Note that this is
# the only reason ``token_epoch`` and ``is_active`` exist as separate levers:
# an operator can disable an account without touching its password.
#
# REFRESH TOKENS / "REMEMBER ME". A refresh token is a second, longer-lived
# credential and needs its own storage, rotation, and reuse-detection to be
# worth anything; done casually it is strictly weaker than the access token it
# was meant to protect. The 14-day access token covers the same ground for a
# reading app, and ``token_epoch`` can end every session at once.
#
# RATE LIMITING. Not implemented here — it belongs in front of the app (reverse
# proxy or middleware) so it also covers the rest of the API, and doing it
# per-process would be defeated by a second worker. Argon2's cost is the only
# brute-force brake currently in place; a public deployment should add a real
# per-IP limit on /api/auth/login and /api/auth/register.
#
# KNOWN GAP — users.pdf_translation ON AN EXISTING DATABASE. Not fixable from
# this file, recorded here because this is where the column is read.
#
# The column is NOT NULL with a Python-side default and no server_default, and
# db/session.py::_add_missing_columns refuses outright to ADD a NOT NULL column
# that has no server_default — it raises RuntimeError. init_engine() calls it on
# boot, so *any* database written before this column existed now fails to open
# at all: the process dies at startup rather than degrading. Verified against a
# simulated pre-column database, not inferred.
#
# Relaxing only that guard is not enough either. _add_missing_columns compiles
# just the column *type* into the ALTER (`ADD COLUMN pdf_translation BOOLEAN`),
# dropping any DEFAULT clause, so every pre-existing row lands on NULL — which
# is why pdf_translation_enabled above has to read NULL as "on".
#
# A complete fix needs all three, and touches files this change does not own:
#   1. models.py — give the column server_default=text("1") so the intent lives
#      in the schema rather than only in Python.
#   2. db/session.py — emit the server_default in the ADD COLUMN DDL, and let a
#      NOT NULL column through once it has one.
#   3. a one-shot UPDATE users SET pdf_translation = 1 WHERE pdf_translation IS
#      NULL, for any database already migrated by the current code.
# Until then the read path above is what keeps established accounts on.
#
# REQUEST BODY SIZE. Starlette reads a request body into memory before any
# handler sees it, so a 10 MB login payload costs 10 MB of RAM per concurrent
# request no matter what these models say. The password bounds above keep that
# body away from argon2 and out of the response, which is the expensive and the
# leaky half — but capping the body itself belongs in the same reverse proxy as
# the rate limit (nginx `client_max_body_size`, and note that /api/papers
# legitimately accepts multi-MB PDF uploads, so the cap has to be per-location).
