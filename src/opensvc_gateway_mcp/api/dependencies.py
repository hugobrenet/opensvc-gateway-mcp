from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.config import get_settings


def get_collector_client() -> CollectorClient:
    return CollectorClient(get_settings())
