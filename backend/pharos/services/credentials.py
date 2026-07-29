"""Authenticated encryption for third-party bearer credentials.

Database backups are deliberately portable and therefore easier to copy than a
live process environment. A Zotero API key must not become readable merely
because somebody obtained ``pharos.db``. This module keeps the encryption format
small, versioned, and independent from the ORM so it can also protect temporary
OAuth secrets.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from pharos.config import Settings

_PREFIX = "fernet:v1:"
_CONTEXT = b"pharos:zotero-credentials:v1\x00"
_AI_CONTEXT = b"pharos:ai-provider-credentials:v1\x00"


class CredentialError(RuntimeError):
    """A stored credential cannot be safely decrypted."""


def _fernet(secret: str, context: bytes) -> Fernet:
    raw = hashlib.sha256(context + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


class CredentialCipher:
    """Encrypt with the primary secret and decrypt with primary or previous."""

    def __init__(
        self,
        primary: str | None,
        previous: str | None = None,
        *,
        context: bytes = _CONTEXT,
    ) -> None:
        self._primary = _fernet(primary, context) if primary else None
        self._previous = _fernet(previous, context) if previous else None

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        return cls(settings.stable_credential_secret, settings.credential_secret_previous)

    @classmethod
    def for_ai_provider(cls, settings: Settings) -> CredentialCipher:
        """A separately derived key for user-supplied model credentials.

        Keeping the derivation context distinct prevents a ciphertext copied
        from a Zotero column from being accepted as a model key (and vice
        versa), while still letting one operator-managed secret rotate both.
        """
        return cls(
            settings.stable_credential_secret,
            settings.credential_secret_previous,
            context=_AI_CONTEXT,
        )

    @property
    def configured(self) -> bool:
        return self._primary is not None

    @staticmethod
    def encrypted(value: str) -> bool:
        return value.startswith(_PREFIX)

    def protect(self, value: str) -> str:
        """Encrypt a plaintext value, or preserve it in keyless local dev."""
        if self.encrypted(value):
            # Normalising also verifies the token and rotates it when necessary.
            return self.normalize(value)
        if self._primary is None:
            return value
        token = self._primary.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{_PREFIX}{token}"

    def reveal(self, stored: str) -> str:
        """Return plaintext without ever including it in an exception message."""
        if not self.encrypted(stored):
            return stored
        if self._primary is None:
            raise CredentialError(
                "Stored credentials are encrypted but no credential secret is set."
            )
        token = stored[len(_PREFIX) :].encode("ascii", "strict")
        for cipher in (self._primary, self._previous):
            if cipher is None:
                continue
            try:
                return cipher.decrypt(token).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError, ValueError):
                continue
        raise CredentialError(
            "Stored credentials could not be decrypted with the configured secret."
        )

    def normalize(self, stored: str) -> str:
        """Encrypt legacy plaintext or rotate ciphertext to the primary key."""
        if not self.encrypted(stored):
            return self.protect(stored)
        if self._primary is None:
            raise CredentialError(
                "Stored credentials are encrypted but no credential secret is set."
            )
        token = stored[len(_PREFIX) :].encode("ascii", "strict")
        try:
            self._primary.decrypt(token).decode("utf-8")
            return stored
        except (InvalidToken, UnicodeDecodeError, ValueError):
            pass
        if self._previous is not None:
            try:
                plaintext = self._previous.decrypt(token).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError, ValueError):
                pass
            else:
                fresh = self._primary.encrypt(plaintext.encode("utf-8")).decode("ascii")
                return f"{_PREFIX}{fresh}"
        raise CredentialError(
            "Stored credentials could not be decrypted with the configured secret."
        )
