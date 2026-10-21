"""Explicit configuration; no committed signing secret."""
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8", extra="ignore",
    )

    mongodb_uri: str = "mongodb://127.0.0.1:27018/?replicaSet=rs0"
    mongodb_database: str = "survey_system"
    secret_key: str = Field(min_length=64, repr=False)
    access_token_minutes: int = Field(default=120, ge=1, le=10080)
    submission_token_minutes: int = Field(default=1440, ge=1, le=10080)
    auto_init_db: bool = True
    cors_origins: list[str] = []
    max_request_bytes: int = Field(default=12 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)

    @field_validator("secret_key")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(set(value)) < 12 or any(x in value.lower() for x in ("change-me", "replace-me", "your-secret")):
            raise ValueError("SECRET_KEY 必须是独立生成的至少 64 字符随机密钥")
        return value

    @field_validator("mongodb_database")
    @classmethod
    def validate_database(cls, value: str) -> str:
        if not value or any(c in value for c in '/\\. "$*<>:|?\x00'):
            raise ValueError("数据库名称不合法")
        if value in {"admin", "local", "config"}:
            raise ValueError("不能使用 MongoDB 系统数据库")
        return value
