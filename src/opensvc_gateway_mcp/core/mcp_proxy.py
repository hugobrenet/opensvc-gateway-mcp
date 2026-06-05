import json
from collections.abc import Callable
from typing import Any, Literal

from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.mcp import McpClient, McpClientError, McpJsonRpcError

McpProxyOperation = Literal["list_tools", "search_tools", "call_tool"]


class McpProxyClientError(Exception):
    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class McpProxyJsonRpcError(Exception):
    def __init__(
        self,
        *,
        operation: McpProxyOperation,
        message: str,
        code: int,
        data: Any,
    ) -> None:
        self.operation = operation
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)


class McpProxyToolValidationError(Exception):
    def __init__(self, *, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("Proxied MCP tool validation failed")


class McpProxy:
    def __init__(self, mcp_client_provider: Callable[[], McpClient]) -> None:
        self._mcp_client_provider = mcp_client_provider

    async def list_tools(
        self,
        credentials: HTTPBasicCredentials,
    ) -> dict[str, Any]:
        mcp = self._mcp_client_provider()
        try:
            return await mcp.list_tools(credentials)
        except McpJsonRpcError as exc:
            raise _json_rpc_proxy_error("list_tools", exc) from exc
        except McpClientError as exc:
            raise McpProxyClientError(
                detail="Collector MCP tools list failed",
            ) from exc

    async def search_tools(
        self,
        credentials: HTTPBasicCredentials,
        *,
        query: str,
    ) -> dict[str, Any]:
        mcp = self._mcp_client_provider()
        try:
            return await mcp.call_tool(
                credentials,
                name="search_tools",
                arguments={"query": query},
            )
        except McpJsonRpcError as exc:
            raise _json_rpc_proxy_error("search_tools", exc) from exc
        except McpClientError as exc:
            raise McpProxyClientError(
                detail="Collector MCP tool search failed",
            ) from exc

    async def call_tool(
        self,
        credentials: HTTPBasicCredentials,
        *,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        mcp = self._mcp_client_provider()
        try:
            result = await mcp.call_tool(
                credentials,
                name="call_tool",
                arguments={
                    "name": name,
                    "arguments": arguments,
                },
            )
            _raise_for_proxied_tool_error(result)
            return result
        except McpJsonRpcError as exc:
            raise _json_rpc_proxy_error("call_tool", exc) from exc
        except McpClientError as exc:
            raise McpProxyClientError(
                detail="Collector MCP tool call failed",
            ) from exc


def _json_rpc_proxy_error(
    operation: McpProxyOperation,
    exc: McpJsonRpcError,
) -> McpProxyJsonRpcError:
    return McpProxyJsonRpcError(
        operation=operation,
        message=str(exc),
        code=exc.code,
        data=exc.data,
    )


def _raise_for_proxied_tool_error(result: dict[str, Any]) -> None:
    content = result.get("content")
    if not isinstance(content, list):
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error") and (
            "expected_input_schema" in payload or "validation_errors" in payload
        ):
            raise McpProxyToolValidationError(payload=payload)
