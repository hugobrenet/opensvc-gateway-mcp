import asyncio

import httpx
import pytest
from fastapi.security import HTTPBasicCredentials
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, ListToolsResult, Tool

from opensvc_gateway_mcp.clients.mcp import (
    McpClient,
    McpJsonRpcError,
    McpTransportError,
)
from opensvc_gateway_mcp.config import Settings


def _settings() -> Settings:
    return Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_MCP_URL="https://mcp.invalid/mcp",
    )


def _credentials() -> HTTPBasicCredentials:
    return HTTPBasicCredentials(username="user-a", password="secret")


class FakeFastMcpClient:
    def __init__(
        self,
        *,
        list_tools_result: ListToolsResult | None = None,
        call_tool_result: CallToolResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.list_tools_result = list_tools_result
        self.call_tool_result = call_tool_result
        self.exc = exc
        self.tool_calls = []
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        if self.exc is not None:
            raise self.exc
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True

    async def list_tools_mcp(self, *, cursor=None):
        if self.exc is not None:
            raise self.exc
        return self.list_tools_result or ListToolsResult(
            tools=[
                Tool(name="search_tools", inputSchema={"type": "object"}),
                Tool(name="call_tool", inputSchema={"type": "object"}),
            ]
        )

    async def call_tool_mcp(self, name, arguments, **kwargs):
        self.tool_calls.append({"name": name, "arguments": arguments})
        if self.exc is not None:
            raise self.exc
        return self.call_tool_result or CallToolResult(
            content=[],
            structuredContent={"count": 2},
            isError=False,
        )


def test_mcp_client_list_tools_uses_native_client_context():
    credentials_seen = []
    request_ids_seen = []
    native_client = FakeFastMcpClient()

    def factory(credentials, request_id=None):
        credentials_seen.append(credentials)
        request_ids_seen.append(request_id)
        return native_client

    client = McpClient(_settings(), client_factory=factory)

    result = asyncio.run(client.list_tools(_credentials(), request_id="ai_test"))

    assert [tool["name"] for tool in result["tools"]] == [
        "search_tools",
        "call_tool",
    ]
    assert credentials_seen[0].username == "user-a"
    assert credentials_seen[0].password == "secret"
    assert request_ids_seen == ["ai_test"]
    assert native_client.entered is True
    assert native_client.exited is True


def test_mcp_client_call_tool_sends_name_and_arguments():
    native_client = FakeFastMcpClient()
    request_ids_seen = []

    def factory(credentials, request_id=None):
        request_ids_seen.append(request_id)
        return native_client

    client = McpClient(_settings(), client_factory=factory)

    result = asyncio.run(
        client.call_tool(
            _credentials(),
            name="count_nodes",
            arguments={"request": {"filters": {"asset_env": "lab"}}},
            request_id="ai_call",
        )
    )

    assert result == {
        "content": [],
        "structuredContent": {"count": 2},
        "isError": False,
    }
    assert native_client.tool_calls == [
        {
            "name": "count_nodes",
            "arguments": {"request": {"filters": {"asset_env": "lab"}}},
        }
    ]
    assert request_ids_seen == ["ai_call"]


def test_mcp_client_builds_transport_with_request_id_header():
    client = McpClient(_settings())

    native_client = client._build_fastmcp_client(  # noqa: SLF001
        _credentials(),
        request_id="ai_header",
    )

    headers = native_client.transport.headers
    assert headers["X-OpenSVC-AI-Request-ID"] == "ai_header"


def test_mcp_client_raises_json_rpc_error_without_exposing_credentials():
    native_client = FakeFastMcpClient(
        exc=McpError(
            ErrorData(
                code=-32001,
                message="Collector authentication failed",
            )
        )
    )
    client = McpClient(
        _settings(),
        client_factory=lambda credentials, request_id=None: native_client,
    )

    with pytest.raises(McpJsonRpcError) as exc_info:
        asyncio.run(client.list_tools(_credentials()))

    assert exc_info.value.code == -32001
    assert str(exc_info.value) == "Collector authentication failed"
    assert "secret" not in str(exc_info.value)


def test_mcp_client_wraps_httpx_transport_errors_without_exposing_credentials():
    native_client = FakeFastMcpClient(exc=httpx.ReadTimeout("timed out"))
    client = McpClient(
        _settings(),
        client_factory=lambda credentials, request_id=None: native_client,
    )

    with pytest.raises(McpTransportError) as exc_info:
        asyncio.run(client.list_tools(_credentials()))

    assert "ReadTimeout" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)
