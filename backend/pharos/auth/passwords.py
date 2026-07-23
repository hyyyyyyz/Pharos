"""Password hashing: argon2id, with the policy that surrounds it.

Argon2id is the only algorithm here on purpose. It is memory-hard, which is what
makes a stolen ``users`` table expensive to crack on GPUs, and ``argon2-cffi``
encodes the parameters into the digest itself — so raising the cost later is a
matter of bumping the hasher and letting :func:`needs_rehash` upgrade each user
on their next successful login, with no migration and no forced password reset.

Nothing in this module ever logs, returns, or embeds a password or a digest in
an exception message: an exception string reliably ends up in a log file, a
crash reporter, or an HTTP 500 body, and any of those turns a transient secret
into a stored one.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

#: Shortest password we will hash. Eight characters is a floor, not a
#: recommendation; length is the only property that actually helps, so the UI
#: should encourage a passphrase rather than symbol soup.
MIN_PASSWORD_LENGTH = 8

#: Longest password we will hash, in characters. Argon2 itself has no practical
#: limit, and that is exactly the problem: hashing is *designed* to be slow and
#: memory-hungry, so an endpoint that accepts a 10 MB password lets one
#: unauthenticated request burn CPU and RAM on demand. The cap is far above any
#: real passphrase and turns that into a cheap rejection.
MAX_PASSWORD_LENGTH = 1024

#: Tuned by argon2-cffi's own recommendations. Bump these (never lower them) to
#: raise the cost; existing users migrate on their next login via needs_rehash.
_HASHER = PasswordHasher()


class PasswordPolicyError(ValueError):
    """A password was rejected before it was ever hashed.

    Carries only the rule that was broken — never the password.
    """


def _check_policy(password: str) -> None:
    """Raise :class:`PasswordPolicyError` unless ``password`` is acceptable.

    Length is measured in characters rather than bytes because that is what the
    user sees and what the frontend counts; the byte cap that actually protects
    the hasher follows from it (a character is at most 4 UTF-8 bytes).
    """
    if not isinstance(password, str):  # defensive: JSON can deliver anything
        raise PasswordPolicyError("Password must be a string")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")


def hash_password(password: str) -> str:
    """Hash a new password with argon2id, enforcing the length policy.

    Returns the full PHC-format digest (algorithm, parameters, salt, and tag),
    which is what makes :func:`needs_rehash` possible later.
    """
    _check_policy(password)
    try:
        return _HASHER.hash(password)
    except HashingError as exc:  # out of memory, or a broken backend
        # Deliberately does not chain the original message: argon2's errors do
        # not contain the password, but re-raising the raw text is how that
        # invariant quietly stops being true after a library upgrade.
        raise RuntimeError("Password hashing failed") from exc


def verify_password(password_hash: str, password: str) -> bool:
    """Check ``password`` against ``password_hash``; never raise on a bad input.

    Every failure mode — wrong password, malformed digest, a hash written by an
    algorithm we no longer support — collapses to ``False``. Callers are
    authentication paths, and an exception escaping one of them turns a routine
    wrong-password into a 500 that distinguishes "no such user" from "bad
    password" by status code alone, which is precisely the enumeration leak the
    login endpoint works to avoid.
    """
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    if not password_hash:
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        # Rejected before argon2 sees it, for the DoS reason above. No real
        # password reaches this branch, so it costs nobody a login.
        return False
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether ``password_hash`` was made with weaker parameters than current.

    Call this only *after* a successful :func:`verify_password`, when the
    plaintext is briefly in hand and can be re-hashed at the new cost. A digest
    we cannot even parse returns ``False``: it cannot be upgraded in place, and
    reporting it as "needs rehash" would invite a caller to overwrite a
    credential it never verified.
    """
    if not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


#: A digest of a random throwaway password, computed once at import so that the
#: cost is paid at boot rather than on the first unauthenticated request.
#: :func:`dummy_verify` runs a real argon2 verification against it when a login
#: names an unknown account, so that "no such user" takes the same wall-clock
#: time as "wrong password" — without it, an attacker times the response and
#: harvests which emails are registered.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))


def dummy_verify(password: str) -> bool:
    """Burn one argon2 verification, always returning ``False``.

    Used on the "user not found" branch of login purely for timing symmetry.
    """
    verify_password(_DUMMY_HASH, password)
    return False
