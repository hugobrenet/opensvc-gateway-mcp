import asyncio

import httpx
import pytest
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.config import Settings


def test_get_ai_config_sends_gateway_internal_token():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "provider": "openai_compatible",
                    "base_url": "https://llm.invalid/v1",
                    "model": "model-a",
                    "api_key": "provider-secret",
                    "system_prompt": "Use OpenSVC MCP tools.",
                }
            },
        )

    client = CollectorClient(
        Settings(
            OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
            OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
        ),
        transport=httpx.MockTransport(handler),
    )

    profile = asyncio.run(
        client.get_ai_config(
            HTTPBasicCredentials(username="user-a", password="secret")
        )
    )

    assert profile.model == "model-a"
    assert requests[0].url == "https://collector.invalid/init/rest/api/ai/llm/config"
    assert requests[0].headers["x-opensvc-gateway-token"] == "expected-token"
    assert requests[0].headers["accept"] == "application/json"


def test_get_ai_config_forbidden_is_provider_config_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Invalid AI gateway internal token"})

    client = CollectorClient(
        Settings(
            OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
            OPENSVC_GATEWAY_INTERNAL_TOKEN="wrong-token",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            client.get_ai_config(
                HTTPBasicCredentials(username="user-a", password="secret")
            )
        )
