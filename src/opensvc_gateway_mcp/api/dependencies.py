from functools import lru_cache

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.config import get_settings
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore


def get_collector_client() -> CollectorClient:
    return CollectorClient(get_settings())


def get_mcp_client() -> McpClient:
    return McpClient(get_settings())


@lru_cache
def get_gateway_session_store() -> InMemoryGatewaySessionStore:
    return InMemoryGatewaySessionStore()
