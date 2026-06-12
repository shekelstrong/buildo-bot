"""Buildo bot configuration.

All env-vars collected via pydantic-settings. Real secrets live in `.env`
(which is gitignored) - never in the repo. Use `.env.example` as template.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== Telegram =====
    telegram_bot_token: str = Field(default="dummy_token_for_ci")
    admin_telegram_id: int = Field(default=6318513424)

    # ===== Database (self-hosted PostgreSQL, primary path) =====
    postgres_dsn: str = Field(
        default="postgresql://buildo:***@buildo-postgres:5432/buildo"
    )
    postgres_password: str = "dummy_postgres"
    redis_password: str = "dummy_redis"

    # ===== Supabase (legacy/optional, NOT primary) =====
    supabase_url: str = "https://your-project.supabase.co"
    supabase_service_key: str = "dummy_service_key"
    supabase_anon_key: str = "dummy_anon_key"

    # ===== LLM =====
    openrouter_api_key: str = "dummy_or_key"
    llm_api_key: str = "dummy_llm_key"
    llm_base_url: str = "https://api.minimaxi.chat/v1"
    llm_model: str = "MiniMax-Text-01"

    # ===== Redis FSM =====
    # In production set REDIS_URL in .env (e.g. redis://default:YOURPASS@host:6379/0)
    redis_url: str = Field(default="redis://localhost:***@0")

    # ===== Layero (hosting) =====
    layero_api_token: str = "dummy_layero_token"

    # ===== GitHub (export sites to shared repo for Pages hosting) =====
    github_token: str = "dummy_github_token"

    # GitHub OAuth (Device Flow) - https://github.com/settings/developers
    # Buildo Bot OAuth App: Client ID = Ov23liOTYx0Y4LeTu1gW
    # Device Flow does NOT need a client secret (public client).
    github_oauth_client_id: str = "Ov23liOTYx0Y4LeTu1gW"
    github_oauth_scopes: str = "repo"

    # ===== Encryption (Fernet key for user GitHub tokens at rest) =====
    encryption_key: str = ""

    # ===== reg.ru (domains) =====
    regru_api_login: str = ""
    regru_api_password: str = ""

    # ===== Beget (optional, Phase 1.5) =====
    beget_api_login: str = ""
    beget_api_password: str = ""

    # ===== Payments =====
    # YuKassa (fiat: SBP / cards / SberPay / T-Pay / Mir Pay)
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = "dummy_yookassa"
    # Cryptobot (crypto: BTC / USDT / ETH / TON)
    cryptobot_api_token: str = "dummy_cryptobot"
    # Telegram Stars - no key needed, native in bot

    # ===== Runtime =====
    environment: str = "production"
    log_level: str = "INFO"
    health_port: int = 8080
    webhook_url: str = ""

    @field_validator("admin_telegram_id")
    @classmethod
    def _admin_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("admin_telegram_id must be a positive integer")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
