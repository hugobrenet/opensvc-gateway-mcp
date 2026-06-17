from collections.abc import Callable
from typing import Any, Protocol

import httpx
from fastmcp import Client as FastMcpClient
from fastmcp.client.transports import StreamableHttpTransport
from fastapi.security import HTTPBasicCredentials
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ListToolsResult

from opensvc_gateway_mcp.config import Settings


class McpClientError(Exception):
    """Base exception for MCP gateway client errors."""


class McpHttpError(McpClientError):
    """The MCP endpoint returned an HTTP error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"MCP HTTP request failed with status {status_code}")


class McpTransportError(McpClientError):
    """The MCP endpoint could not be reached or timed out."""


class McpJsonRpcError(McpClientError):
    """The MCP endpoint returned a JSON-RPC error."""

    def __init__(
        self,
        *,
        code: int | None,
        message: str,
        data: Any = None,
    ) -> None:
        self.code = code
        self.data = data
        super().__init__(message)


class McpProtocolError(McpClientError):
    """The MCP endpoint returned an invalid or unexpected response."""


class FastMcpClientProtocol(Protocol):
    async def __aenter__(self) -> "FastMcpClientProtocol": ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def list_tools_mcp(self, *, cursor: str | None = None) -> ListToolsResult: ...

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> CallToolResult: ...


FastMcpClientFactory = Callable[
    [HTTPBasicCredentials, str | None],
    FastMcpClientProtocol,
]


class McpClient:
    def __init__(
        self,
        settings: Settings,
        client_factory: FastMcpClientFactory | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or self._build_fastmcp_client

    @property
    def list_tools_cache_key(self) -> str:
        return self.settings.mcp_url.rstrip("/")

    @property
    def list_tools_cache_ttl_seconds(self) -> float:
        if self.settings.mcp_list_tools_cache_ttl_seconds is not None:
            return self.settings.mcp_list_tools_cache_ttl_seconds
        return float(self.settings.gateway_session_ttl_seconds)

    async def list_tools(
        self,
        credentials: HTTPBasicCredentials,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._client_factory(credentials, request_id) as client:
                result = await client.list_tools_mcp()
        except McpError as exc:
            raise _json_rpc_error_from_mcp_error(exc) from exc
        except httpx.HTTPStatusError as exc:
            raise McpHttpError(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise McpTransportError(
                f"MCP HTTP request failed: {type(exc).__name__}"
            ) from exc
        except RuntimeError as exc:
            raise McpProtocolError(str(exc)) from exc
        return _model_dump(result)

    async def call_tool(
        self,
        credentials: HTTPBasicCredentials,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._client_factory(credentials, request_id) as client:
                result = await client.call_tool_mcp(name, arguments or {})
        except McpError as exc:
            raise _json_rpc_error_from_mcp_error(exc) from exc
        except httpx.HTTPStatusError as exc:
            raise McpHttpError(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise McpTransportError(
                f"MCP HTTP request failed: {type(exc).__name__}"
            ) from exc
        except RuntimeError as exc:
            raise McpProtocolError(str(exc)) from exc
        return _model_dump(result)

    def _build_fastmcp_client(
        self,
        credentials: HTTPBasicCredentials,
        request_id: str | None = None,
    ) -> FastMcpClientProtocol:
        headers = {}
        if request_id is not None:
            headers["X-OpenSVC-AI-Request-ID"] = request_id
        transport = StreamableHttpTransport(
            url=self.settings.mcp_url,
            headers=headers or None,
            auth=httpx.BasicAuth(credentials.username, credentials.password),
        )
        return FastMcpClient(
            transport,
            name="opensvc-gateway-mcp",
            timeout=self.settings.mcp_request_timeout_seconds,
        )


def _json_rpc_error_from_mcp_error(exc: McpError) -> McpJsonRpcError:
    return McpJsonRpcError(
        code=exc.error.code,
        message=exc.error.message,
        data=exc.error.data,
    )


def _model_dump(result: Any) -> dict[str, Any]:
    if not hasattr(result, "model_dump"):
        raise McpProtocolError("MCP response did not contain a Pydantic result")
    payload = result.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(payload, dict):
        raise McpProtocolError("MCP response did not contain an object result")
    return payload
