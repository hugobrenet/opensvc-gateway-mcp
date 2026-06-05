from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    gateway_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_HOST",
            "GATEWAY_HOST",
        ),
    )
    gateway_port: int = Field(
        default=8010,
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_PORT",
            "GATEWAY_PORT",
        ),
    )

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
    collector_ai_config_path: str = Field(
        default="/ai/llm/config",
        validation_alias=AliasChoices(
            "OPENSVC_COLLECTOR_AI_CONFIG_PATH",
            "COLLECTOR_AI_CONFIG_PATH",
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
    gateway_redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_REDIS_URL",
            "GATEWAY_REDIS_URL",
        ),
    )
    gateway_redis_key_prefix: str = Field(
        default="ai_gateway:session:",
        validation_alias=AliasChoices(
            "OPENSVC_GATEWAY_REDIS_KEY_PREFIX",
            "GATEWAY_REDIS_KEY_PREFIX",
        ),
    )

    mcp_url: str = Field(
        default="http://127.0.0.1:8011/mcp",
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
    llm_request_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "OPENSVC_LLM_REQUEST_TIMEOUT_SECONDS",
            "LLM_REQUEST_TIMEOUT_SECONDS",
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
