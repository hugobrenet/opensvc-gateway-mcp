import httpx

from opensvc_gateway_mcp.clients.llm.base import LlmProviderClient
from opensvc_gateway_mcp.clients.llm.openai_compatible import OpenAICompatibleLlmClient
from opensvc_gateway_mcp.config import Settings


def create_llm_client(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmProviderClient:
    return OpenAICompatibleLlmClient(settings, transport=transport)
