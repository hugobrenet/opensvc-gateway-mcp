import json
from dataclasses import dataclass
from itertools import count
from typing import Any

import httpx
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.config import Settings


MCP_PROTOCOL_VERSION = "2025-06-18"


class McpClientError(Exception):
    """Base exception for MCP gateway client errors."""


class McpHttpError(McpClientError):
    """The MCP endpoint returned an HTTP error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"MCP HTTP request failed with status {status_code}")


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


@dataclass(frozen=True)
class McpSession:
    session_id: str | None
    protocol_version: str
    initialize_result: dict[str, Any]


class McpClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._request_ids = count(1)

    async def initialize(
        self,
        credentials: HTTPBasicCredentials,
    ) -> McpSession:
        response = await self._request(
            credentials=credentials,
            method="initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "opensvc-gateway-mcp",
                    "version": "0.1.0",
                },
            },
        )
        result = _extract_result(response)
        protocol_version = str(result.get("protocolVersion") or MCP_PROTOCOL_VERSION)
        return McpSession(
            session_id=response.headers.get("mcp-session-id"),
            protocol_version=protocol_version,
            initialize_result=result,
        )

    async def list_tools(
        self,
        credentials: HTTPBasicCredentials,
    ) -> dict[str, Any]:
        session = await self.initialize(credentials)
        await self.send_initialized(credentials, session)
        response = await self._request(
            credentials=credentials,
            method="tools/list",
            params={},
            session=session,
        )
        return _extract_result(response)

    async def call_tool(
        self,
        credentials: HTTPBasicCredentials,
        *,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = await self.initialize(credentials)
        await self.send_initialized(credentials, session)
        response = await self._request(
            credentials=credentials,
            method="tools/call",
            params={
                "name": name,
                "arguments": arguments or {},
            },
            session=session,
        )
        return _extract_result(response)

    async def send_initialized(
        self,
        credentials: HTTPBasicCredentials,
        session: McpSession,
    ) -> None:
        await self._notification(
            credentials=credentials,
            method="notifications/initialized",
            params={},
            session=session,
        )

    async def _request(
        self,
        *,
        credentials: HTTPBasicCredentials,
        method: str,
        params: dict[str, Any],
        session: McpSession | None = None,
    ) -> httpx.Response:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._request_ids),
            "method": method,
            "params": params,
        }
        response = await self._post(
            credentials=credentials,
            payload=payload,
            session=session,
        )
        if response.status_code == 202:
            raise McpProtocolError(f"MCP request {method!r} returned no response")
        _extract_message(response)
        return response

    async def _notification(
        self,
        *,
        credentials: HTTPBasicCredentials,
        method: str,
        params: dict[str, Any],
        session: McpSession,
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        response = await self._post(
            credentials=credentials,
            payload=payload,
            session=session,
        )
        if response.status_code != 202:
            _extract_message(response)

    async def _post(
        self,
        *,
        credentials: HTTPBasicCredentials,
        payload: dict[str, Any],
        session: McpSession | None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if session is not None:
            if session.session_id:
                headers["mcp-session-id"] = session.session_id
            headers["mcp-protocol-version"] = session.protocol_version

        async with httpx.AsyncClient(
            timeout=self.settings.mcp_request_timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                self.settings.mcp_url,
                json=payload,
                headers=headers,
                auth=(credentials.username, credentials.password),
            )

        if response.status_code >= 400:
            try:
                _extract_message(response)
            except McpJsonRpcError:
                raise
            except McpProtocolError:
                pass
            raise McpHttpError(response.status_code)
        return response


def _extract_result(response: httpx.Response) -> dict[str, Any]:
    message = _extract_message(response)
    result = message.get("result")
    if not isinstance(result, dict):
        raise McpProtocolError("MCP response did not contain an object result")
    return result


def _extract_message(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        message = response.json()
    elif content_type.startswith("text/event-stream"):
        message = _extract_sse_message(response.text)
    else:
        raise McpProtocolError(
            f"MCP response used unsupported content type {content_type!r}"
        )

    if not isinstance(message, dict):
        raise McpProtocolError("MCP response was not a JSON object")

    error = message.get("error")
    if isinstance(error, dict):
        raise McpJsonRpcError(
            code=error.get("code") if isinstance(error.get("code"), int) else None,
            message=str(error.get("message") or "MCP JSON-RPC error"),
            data=error.get("data"),
        )

    if message.get("jsonrpc") != "2.0":
        raise McpProtocolError("MCP response was not a JSON-RPC 2.0 message")

    return message


def _extract_sse_message(body: str) -> dict[str, Any]:
    for event in body.split("\n\n"):
        data_lines = [
            line.removeprefix("data:").lstrip()
            for line in event.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue

        data = "\n".join(data_lines).strip()
        if not data:
            continue

        message = json.loads(data)
        if isinstance(message, dict):
            return message

    raise McpProtocolError("MCP SSE response did not contain a JSON-RPC message")
