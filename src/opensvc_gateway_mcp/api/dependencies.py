from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.config import get_settings
from functools import lru_cache
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore


def get_collector_client() -> CollectorClient:
    return CollectorClient(get_settings())

@lru_cache
def get_gateway_session_store() -> InMemoryGatewaySessionStore:
    return InMemoryGatewaySessionStore()
