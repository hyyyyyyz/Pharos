"""Authentication core: password hashing and stateless access tokens.

Split in two on purpose. :mod:`pharos.auth.passwords` owns the one-way
transformation of a secret the user chose; :mod:`pharos.auth.tokens` owns the
short-lived, signed assertion that they already proved it. Neither imports the
API layer, so both stay testable without a running app.

What is deliberately absent: refresh tokens, "remember me", and password reset.
See the report accompanying this module — a half-finished reset flow is a
credential-takeover vector, not a partial feature.
"""

from __future__ import annotations

from pharos.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from pharos.auth.tokens import (
    ACCESS_TOKEN_TYPE,
    ALGORITHM,
    InvalidTokenError,
    TokenClaims,
    TokenError,
    TokenExpiredError,
    decode_access_token,
    issue_access_token,
)

__all__ = [
    "ACCESS_TOKEN_TYPE",
    "ALGORITHM",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "InvalidTokenError",
    "PasswordPolicyError",
    "TokenClaims",
    "TokenError",
    "TokenExpiredError",
    "decode_access_token",
    "dummy_verify",
    "hash_password",
    "issue_access_token",
    "needs_rehash",
    "verify_password",
]
