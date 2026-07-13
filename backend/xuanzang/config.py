"""Application configuration (env-driven, 12-factor).

Values come from environment variables prefixed ``XUANZANG_`` or a ``.env`` file
at the repo root. Never hard-code secrets — the DeepSeek/LLM key lives only in
the environment. See ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from xuanzang.engines.base import TranslatorConfig

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../Xuanzang


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XUANZANG_",
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage ---
    data_dir: Path = REPO_ROOT / "data"

    # --- translation engine (subprocess in the engine env) ---
    engine_python: Path = Path.home() / "miniconda3" / "envs" / "xuanzang-engine" / "bin" / "python"
    qps: int = 4
    max_concurrent_jobs: int = 2

    # --- translation backend ---
    # "bing"/"google" are free & keyless; "deepseek"/"openai"/"openai_compatible" need a key.
    translator_type: str = "bing"
    llm_base_url: str | None = Field(default=None)
    llm_api_key: str | None = Field(default=None)
    llm_model: str | None = Field(default=None)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "xuanzang.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    def translator_config(self) -> TranslatorConfig:
        """Build the engine's TranslatorConfig from settings.

        Falls back to free Bing if an LLM backend is selected but no key is set,
        so the app is always usable out of the box.
        """
        t = self.translator_type.lower()
        if t in ("deepseek", "openai", "openai_compatible") and not self.llm_api_key:
            return TranslatorConfig(type="bing")
        if t == "deepseek":
            return TranslatorConfig(type="deepseek", api_key=self.llm_api_key, model=self.llm_model or "deepseek-chat")
        if t == "openai":
            return TranslatorConfig(
                type="openai", api_key=self.llm_api_key, base_url=self.llm_base_url, model=self.llm_model
            )
        if t == "openai_compatible":
            return TranslatorConfig(
                type="openai_compatible",
                api_key=self.llm_api_key,
                base_url=self.llm_base_url,
                model=self.llm_model,
            )
        return TranslatorConfig(type=t)  # bing / google


@lru_cache
def get_settings() -> Settings:
    return Settings()
