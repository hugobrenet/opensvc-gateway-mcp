from collections.abc import Callable
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_gateway_session_store,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.mcp import McpClient, McpClientError, McpJsonRpcError
from opensvc_gateway_mcp.core.sessions import GatewaySessionStore
from opensvc_gateway_mcp.schemas.mcp import CallMcpToolRequest, SearchMcpToolsRequest


router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


def _mcp_json_rpc_error_detail(exc: McpJsonRpcError) -> dict[str, Any]:
    detail = {
        "message": str(exc),
        "code": exc.code,
    }
    if exc.data is not None:
        detail["data"] = exc.data
    return detail


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
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=payload,
            )


async def _credentials_from_gateway_session(
    *,
    session_id: str | None,
    store: GatewaySessionStore,
) -> HTTPBasicCredentials:
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OpenSVC AI session",
        )

    session = await store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OpenSVC AI session",
        )

    return HTTPBasicCredentials(
        username=session.username,
        password=session.password.get_secret_value(),
    )


@router.get("/tools")
async def list_mcp_tools(
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    credentials = await _credentials_from_gateway_session(
        session_id=x_opensvc_ai_session,
        store=store,
    )
    try:
        mcp = mcp_client_provider()
        return await mcp.list_tools(credentials)
    except McpJsonRpcError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_mcp_json_rpc_error_detail(exc),
        ) from exc
    except McpClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector MCP tools list failed",
        ) from exc


@router.post("/tools/search")
async def search_mcp_tools(
    request: SearchMcpToolsRequest,
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    credentials = await _credentials_from_gateway_session(
        session_id=x_opensvc_ai_session,
        store=store,
    )
    try:
        mcp = mcp_client_provider()
        return await mcp.call_tool(
            credentials,
            name="search_tools",
            arguments={"query": request.query},
        )
    except McpJsonRpcError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_mcp_json_rpc_error_detail(exc),
        ) from exc
    except McpClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector MCP tool search failed",
        ) from exc


@router.post("/tools/call")
async def call_mcp_tool(
    request: CallMcpToolRequest,
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    credentials = await _credentials_from_gateway_session(
        session_id=x_opensvc_ai_session,
        store=store,
    )
    try:
        mcp = mcp_client_provider()
        result = await mcp.call_tool(
            credentials,
            name="call_tool",
            arguments={
                "name": request.name,
                "arguments": request.arguments,
            },
        )
        _raise_for_proxied_tool_error(result)
        return result
    except McpJsonRpcError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_mcp_json_rpc_error_detail(exc),
        ) from exc
    except McpClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector MCP tool call failed",
        ) from exc
