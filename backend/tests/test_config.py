"""Settings tests, focused on the LLM provider resolution.

The rules worth protecting: the app must work with no credentials at all, a
half-configured provider must degrade instead of raising, one provider's key
must never leak into another, and no code path may expose a key.
"""

from __future__ import annotations

import pytest
from pharos.config import Settings


def make(**env: str) -> Settings:
    """A Settings built only from the given vars — never the developer's .env."""
    return Settings(
        _env_file=None, **{k.removeprefix("PHAROS_").lower(): v for k, v in env.items()}
    )


def test_works_with_no_credentials() -> None:
    """Out of the box the app still translates, via the free keyless engine."""
    s = make()
    assert s.translator_config().type == "bing"
    assert s.provider_for("chat") is None


def test_deepseek_selected_for_translation() -> None:
    s = make(PHAROS_TRANSLATOR_TYPE="deepseek", PHAROS_DEEPSEEK_API_KEY="sk-ds")
    cfg = s.translator_config()
    assert cfg.type == "deepseek"
    assert cfg.api_key == "sk-ds"
    assert cfg.model == "deepseek-chat"


def test_self_hosted_relay_is_just_a_base_url() -> None:
    """A self-hosted OpenAI-compatible gateway needs no special casing."""
    s = make(
        PHAROS_CHAT_PROVIDER="openai",
        PHAROS_OPENAI_API_KEY="sk-relay",
        PHAROS_OPENAI_BASE_URL="https://my-relay.internal/v1",
        PHAROS_OPENAI_MODEL="gpt-5",
    )
    provider = s.provider_for("chat")
    assert provider is not None
    assert provider.base_url == "https://my-relay.internal/v1"
    assert provider.model == "gpt-5"
    assert provider.translator_type == "openai_compatible"


def test_tasks_can_use_different_providers() -> None:
    """Cheap model for bulk translation, strong model for chat."""
    s = make(
        PHAROS_TRANSLATOR_TYPE="deepseek",
        PHAROS_DEEPSEEK_API_KEY="sk-ds",
        PHAROS_CHAT_PROVIDER="openai",
        PHAROS_OPENAI_API_KEY="sk-relay",
    )
    assert s.translator_config().type == "deepseek"
    chat = s.provider_for("chat")
    assert chat is not None and chat.name == "openai"


@pytest.mark.parametrize("provider", ["deepseek", "openai", "custom"])
def test_provider_without_key_degrades_rather_than_raising(provider: str) -> None:
    """Naming a provider you have no key for must not break the app."""
    s = make(PHAROS_TRANSLATOR_TYPE=provider, PHAROS_CHAT_PROVIDER=provider)
    assert s.translator_config().type == "bing"  # falls back to the free engine
    assert s.provider_for("chat") is None  # callers report "not configured"


def test_legacy_single_provider_settings_still_work() -> None:
    """Existing deployments set PHAROS_LLM_* — they must keep translating."""
    s = make(PHAROS_TRANSLATOR_TYPE="deepseek", PHAROS_LLM_API_KEY="sk-legacy")
    assert s.translator_config().api_key == "sk-legacy"


def test_legacy_key_does_not_leak_into_an_unrelated_provider() -> None:
    """A stale PHAROS_LLM_API_KEY must not authenticate a provider it was never meant for."""
    s = make(PHAROS_TRANSLATOR_TYPE="deepseek", PHAROS_LLM_API_KEY="sk-legacy")
    assert s.providers()["openai"].api_key is None
    assert s.providers()["custom"].api_key is None


def test_redacted_never_exposes_the_key() -> None:
    secret = "sk-must-not-appear-anywhere"
    s = make(PHAROS_CHAT_PROVIDER="openai", PHAROS_OPENAI_API_KEY=secret)
    provider = s.provider_for("chat")
    assert provider is not None
    assert secret not in str(provider.redacted())
    assert provider.redacted()["configured"] is True


def test_zotero_oauth_requires_a_dedicated_credential_secret() -> None:
    common = {
        "PHAROS_AUTH_SECRET": "a" * 48,
        "PHAROS_ZOTERO_OAUTH_CLIENT_KEY": "client",
        "PHAROS_ZOTERO_OAUTH_CLIENT_SECRET": "secret",
        "PHAROS_ZOTERO_OAUTH_CALLBACK_URL": "https://pharos.example/api/zotero/oauth/callback",
    }
    assert make(**common).zotero_oauth_configured is False
    assert make(**common, PHAROS_CREDENTIAL_SECRET="b" * 48).zotero_oauth_configured is True
