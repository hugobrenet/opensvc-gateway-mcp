from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    collector_api_base_url: str = Field(
        validation_alias=AliasChoices(
            "OPENSVC_COLLECTOR_API_BASE_URL",
            "OPENSVC_API_BASE_URL",
        )
    )
    collector_request_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "OPENSVC_COLLECTOR_REQUEST_TIMEOUT_SECONDS",
            "OPENSVC_REQUEST_TIMEOUT_SECONDS",
        ),
    )
    collector_tls_verify: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "OPENSVC_COLLECTOR_TLS_VERIFY",
            "OPENSVC_TLS_VERIFY",
        ),
    )

    gateway_internal_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_INTERNAL_TOKEN",
            "GATEWAY_INTERNAL_TOKEN",
        ),
    )
    gateway_session_ttl_seconds: int = Field(
        default=1800,
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_SESSION_TTL_SECONDS",
            "GATEWAY_SESSION_TTL_SECONDS",
        ),
    )

    mcp_url: str = Field(
        default="http://127.0.0.1:8001/mcp",
        validation_alias=AliasChoices(
            "OPENSVC_MCP_URL",
            "MCP_URL",
        ),
    )
    mcp_request_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "OPENSVC_MCP_REQUEST_TIMEOUT_SECONDS",
            "MCP_REQUEST_TIMEOUT_SECONDS",
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
