from collections.abc import Callable
from functools import lru_cache

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import OpenAICompatibleLlmClient
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.config import get_settings
from opensvc_gateway_mcp.core.sessions import (
    GatewaySessionStore,
    InMemoryGatewaySessionStore,
    RedisGatewaySessionStore,
)


def get_collector_client() -> CollectorClient:
    return CollectorClient(get_settings())


def get_collector_client_provider() -> Callable[[], CollectorClient]:
    return get_collector_client


def get_mcp_client() -> McpClient:
    return McpClient(get_settings())


def get_mcp_client_provider() -> Callable[[], McpClient]:
    return get_mcp_client


def get_llm_client() -> OpenAICompatibleLlmClient:
    return OpenAICompatibleLlmClient(get_settings())


def get_llm_client_provider() -> Callable[[], OpenAICompatibleLlmClient]:
    return get_llm_client


@lru_cache
def get_gateway_session_store() -> GatewaySessionStore:
    settings = get_settings()
    if settings.gateway_session_store == "redis":
        return RedisGatewaySessionStore(
            redis_url=settings.gateway_redis_url,
            key_prefix=settings.gateway_redis_key_prefix,
        )
    return InMemoryGatewaySessionStore()
