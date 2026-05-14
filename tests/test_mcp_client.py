import asyncio
import base64
import json

import httpx
import pytest
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.mcp import McpClient, McpJsonRpcError
from opensvc_gateway_mcp.config import Settings


def _settings() -> Settings:
    return Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_MCP_URL="https://mcp.invalid/mcp",
    )


def _credentials() -> HTTPBasicCredentials:
    return HTTPBasicCredentials(username="user-a", password="secret")


def _basic_auth_value(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def _json_response(payload: dict, *, headers: dict[str, str] | None = None):
    return httpx.Response(
        200,
        json=payload,
        headers={"Content-Type": "application/json", **(headers or {})},
    )


def _sse_response(payload: dict):
    return httpx.Response(
        200,
        text=f"event: message\ndata: {json.dumps(payload)}\n\n",
        headers={"Content-Type": "text/event-stream"},
    )


def test_mcp_client_initialize_posts_basic_auth_and_returns_session():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "collector-mcp"},
                },
            },
            headers={"mcp-session-id": "MCP-SESSION"},
        )

    client = McpClient(_settings(), transport=httpx.MockTransport(handler))

    session = asyncio.run(client.initialize(_credentials()))

    assert session.session_id == "MCP-SESSION"
    assert session.protocol_version == "2025-06-18"
    assert session.initialize_result["serverInfo"]["name"] == "collector-mcp"
    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://mcp.invalid/mcp"
    assert request.headers["authorization"] == _basic_auth_value("user-a", "secret")
    assert request.headers["accept"] == "application/json, text/event-stream"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content)["method"] == "initialize"


def test_mcp_client_list_tools_initializes_then_uses_mcp_session():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "initialize":
            return _json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
                headers={"mcp-session-id": "MCP-SESSION"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202, headers={"Content-Type": "application/json"})
        if method == "tools/list":
            assert request.headers["mcp-session-id"] == "MCP-SESSION"
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {"name": "search_tools"},
                            {"name": "call_tool"},
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected MCP method {method}")

    client = McpClient(_settings(), transport=httpx.MockTransport(handler))

    result = asyncio.run(client.list_tools(_credentials()))

    assert [tool["name"] for tool in result["tools"]] == [
        "search_tools",
        "call_tool",
    ]
    assert [json.loads(request.content)["method"] for request in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_mcp_client_call_tool_sends_name_and_arguments():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        method = payload["method"]
        if method == "initialize":
            return _json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
                headers={"mcp-session-id": "MCP-SESSION"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202, headers={"Content-Type": "application/json"})
        if method == "tools/call":
            assert payload["params"] == {
                "name": "count_nodes",
                "arguments": {"request": {"filters": {"asset_env": "lab"}}},
            }
            return _json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"structuredContent": {"count": 2}},
                }
            )
        raise AssertionError(f"unexpected MCP method {method}")

    client = McpClient(_settings(), transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.call_tool(
            _credentials(),
            name="count_nodes",
            arguments={"request": {"filters": {"asset_env": "lab"}}},
        )
    )

    assert result == {"structuredContent": {"count": 2}}
    assert [call["method"] for call in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


def test_mcp_client_raises_json_rpc_error_without_exposing_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32001,
                    "message": "Collector authentication failed",
                },
            }
        )

    client = McpClient(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(McpJsonRpcError) as exc_info:
        asyncio.run(client.initialize(_credentials()))

    assert exc_info.value.code == -32001
    assert str(exc_info.value) == "Collector authentication failed"
    assert "secret" not in str(exc_info.value)
