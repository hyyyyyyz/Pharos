"""Application configuration (env-driven, 12-factor).

Values come from environment variables prefixed ``PHAROS_`` or a ``.env`` file
at the repo root. Never hard-code secrets — API keys live only in the
environment. See ``.env.example``.

LLM providers are *named* rather than singular, because the two jobs have
different economics: translation is high-volume and wants a cheap model, while
the paper AI chat is low-volume and wants the strongest reasoning available. Each
provider is an OpenAI-compatible endpoint, so a self-hosted relay is configured
exactly like a first-party one — only ``base_url`` differs.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pharos.engines.base import TranslatorConfig

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../Pharos

#: Tasks that can be pointed at a provider independently.
Task = str  # "chat" | "translate"


class LLMProvider(BaseModel):
    """One OpenAI-compatible endpoint."""

    name: str
    api_key: str | None = None
    base_url: str | None = None
    model: str = ""
    timeout: float = 60.0
    #: The engine's translator id for this provider ("deepseek" has a dedicated
    #: implementation; anything else routes through the generic OpenAI path).
    translator_type: str = "openai_compatible"

    @property
    def configured(self) -> bool:
        """A provider is usable once it has a key and a model."""
        return bool(self.api_key and self.model)

    def redacted(self) -> dict[str, object]:
        """Safe to log or return over the API — never exposes the key."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "configured": self.configured,
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PHAROS_",
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---
    data_dir: Path = REPO_ROOT / "data"

    # --- compiled web client -------------------------------------------
    #: Optional Vite ``dist`` directory. Development normally leaves this
    #: unset and uses Vite's own server; the production image points it at the
    #: frontend bundle copied into the image.
    web_dir: Path | None = None

    # --- auth -----------------------------------------------------------
    #: Signing key for access tokens. There is deliberately NO usable default:
    #: a hard-coded fallback would let anyone who read this open-source repo
    #: mint a valid token for any account. ``auth_secret_or_die()`` generates a
    #: throwaway key for local dev and refuses to start when bound publicly.
    auth_secret: str | None = None
    access_token_ttl_minutes: int = 60 * 24 * 14  # 14 days
    #: Whether strangers may create accounts. Pharos is meant to be a public
    #: multi-user service, so this is on; set it false to run a private
    #: instance after creating your own account.
    allow_registration: bool = True
    #: Origins allowed to call the API. "*" is only tolerable while there is no
    #: auth; once tokens exist, list the real frontend origins.
    cors_origins: str = "*"

    # --- encrypted third-party credentials -------------------------------
    #: Stable secret used to encrypt bearer credentials stored in SQLite.
    #: It is deliberately independent from PHAROS_AUTH_SECRET: rotating login
    #: tokens must never make an external account credential unreadable. Local
    #: development may omit it and keep the manual-key fallback in plaintext.
    credential_secret: str | None = None
    #: Set temporarily while rotating PHAROS_CREDENTIAL_SECRET. Values that
    #: decrypt only with this key are re-encrypted with the primary key on boot.
    credential_secret_previous: str | None = None

    # --- Zotero OAuth 1.0a key exchange ----------------------------------
    #: Register the application at https://www.zotero.org/oauth/apps. OAuth is
    #: offered only when every required value below and a stable credential
    #: secret are present; the manual API-key path remains available otherwise.
    zotero_oauth_client_key: str | None = None
    zotero_oauth_client_secret: str | None = None
    zotero_oauth_callback_url: str | None = None
    zotero_oauth_return_url: str | None = None
    zotero_oauth_attempt_ttl_seconds: int = 10 * 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def stable_credential_secret(self) -> str | None:
        """The stable key source for encrypted stored credentials, if any."""
        value = self.credential_secret
        return value if value and len(value) >= 32 else None

    @property
    def zotero_oauth_configured(self) -> bool:
        """Whether the server has everything required for a safe OAuth run."""
        return bool(
            self.zotero_oauth_client_key
            and self.zotero_oauth_client_secret
            and self.zotero_oauth_callback_url
            # OAuth creates a long-lived credential on behalf of another
            # service, so require a dedicated key.
            and self.credential_secret
            and len(self.credential_secret) >= 32
        )

    # --- translation engine (subprocess in the engine env) ---
    engine_python: Path = Path.home() / "miniconda3" / "envs" / "pharos-engine" / "bin" / "python"
    qps: int = 4
    max_concurrent_jobs: int = 2

    # --- which provider serves which task -------------------------------
    # "bing"/"google" are free and keyless, and remain the translation default
    # so the app works with no credentials at all.
    translator_type: str = "bing"
    #: Provider name for paper AI chat. Empty => chat is unavailable (the API
    #: returns a clear 503 rather than pretending).
    chat_provider: str = "deepseek"

    # --- provider: DeepSeek ---------------------------------------------
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # --- provider: OpenAI / any OpenAI-compatible relay -------------------
    # Point base_url at a self-hosted gateway to use it exactly like the real
    # thing; the wire format is identical.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # --- provider: a third, fully user-defined endpoint -------------------
    # Escape hatch so a new vendor needs no code change at all.
    custom_api_key: str | None = None
    custom_base_url: str | None = None
    custom_model: str = ""

    # --- deprecated single-provider settings -----------------------------
    # Kept so existing deployments keep working; the named providers win.
    llm_base_url: str | None = Field(default=None, deprecated=True)
    llm_api_key: str | None = Field(default=None, deprecated=True)
    llm_model: str | None = Field(default=None, deprecated=True)

    # --- desktop updates ---------------------------------------------------
    #: GitHub repo whose desktop-v* release tags are the official update
    #: channel. The update endpoint falls back to it when the operator has
    #: not pinned an advertised version.
    desktop_update_repo: str = "hyyyyyyz/Pharos"
    #: Optional operator pin: advertise this exact version as the newest
    #: desktop build, bypassing GitHub. Useful before a release lands publicly
    #: or when GitHub is unreachable from the server. Must look like X.Y.Z.
    desktop_update_version_override: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pharos.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    # ------------------------------------------------------------------ LLM

    def providers(self) -> dict[str, LLMProvider]:
        """All known providers, whether or not they hold credentials."""
        return {
            "deepseek": LLMProvider(
                name="deepseek",
                api_key=self.deepseek_api_key or self._legacy_key("deepseek"),
                base_url=self.deepseek_base_url,
                model=self.deepseek_model,
                translator_type="deepseek",
            ),
            "openai": LLMProvider(
                name="openai",
                api_key=self.openai_api_key or self._legacy_key("openai"),
                base_url=self.openai_base_url,
                model=self.openai_model,
                translator_type="openai_compatible",
            ),
            "custom": LLMProvider(
                name="custom",
                api_key=self.custom_api_key,
                base_url=self.custom_base_url,
                model=self.custom_model,
                translator_type="openai_compatible",
            ),
        }

    def _legacy_key(self, name: str) -> str | None:
        """Honour the old single-provider ``PHAROS_LLM_*`` vars.

        They only apply to whichever provider ``translator_type`` named, so a
        stale key can't leak into an unrelated provider.
        """
        if self.translator_type.lower() in (name, "openai_compatible") and name != "custom":
            return self.llm_api_key
        return None

    def provider_for(self, task: Task) -> LLMProvider | None:
        """The configured provider for ``task``, or None if unusable.

        Returning None (rather than a half-filled provider) is what lets the
        callers fail loudly and specifically — an unconfigured chat endpoint
        should say so, not emit a confusing auth error from a vendor.
        """
        name = (self.chat_provider if task == "chat" else self.translator_type).lower()
        provider = self.providers().get(name)
        if provider is None or not provider.configured:
            return None
        return provider

    def translator_config(self) -> TranslatorConfig:
        """Build the engine's TranslatorConfig.

        Falls back to free Bing whenever an LLM backend is selected but has no
        usable credentials, so the app is always able to translate something.
        """
        t = self.translator_type.lower()
        if t in ("bing", "google"):
            return TranslatorConfig(type=t)

        provider = self.provider_for("translate")
        if provider is None:
            return TranslatorConfig(type="bing")

        if provider.translator_type == "deepseek":
            return TranslatorConfig(type="deepseek", api_key=provider.api_key, model=provider.model)
        return TranslatorConfig(
            type="openai_compatible",
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def auth_secret_or_die(bound_publicly: bool = False) -> str:
    """The token-signing key, or a loud failure.

    Shipping a default secret in an open-source project is equivalent to
    shipping no authentication at all — anyone could sign a token for any
    account. So there is no default. For local development we mint an ephemeral
    key instead (tokens simply stop working across restarts, which is harmless
    on a laptop); for anything reachable from outside, refuse to start.
    """
    secret = get_settings().auth_secret
    if secret:
        if len(secret) < 32:
            raise RuntimeError(
                "PHAROS_AUTH_SECRET is too short; use at least 32 random characters "
                "(e.g. `python -c 'import secrets;print(secrets.token_urlsafe(48))'`)."
            )
        return secret
    if bound_publicly:
        raise RuntimeError(
            "PHAROS_AUTH_SECRET must be set when the API is reachable from "
            "outside localhost. Generate one with "
            "`python -c 'import secrets;print(secrets.token_urlsafe(48))'`."
        )
    return secrets.token_urlsafe(48)
