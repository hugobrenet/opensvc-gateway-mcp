from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_gateway_session_store,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.mcp import McpClient, McpClientError
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore


router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


@router.get("/tools")
async def list_mcp_tools(
    store: Annotated[InMemoryGatewaySessionStore, Depends(get_gateway_session_store)],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if not x_opensvc_ai_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OpenSVC AI session",
        )

    session = store.get(x_opensvc_ai_session)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OpenSVC AI session",
        )

    credentials = HTTPBasicCredentials(
        username=session.username,
        password=session.password.get_secret_value(),
    )
    try:
        mcp = mcp_client_provider()
        return await mcp.list_tools(credentials)
    except McpClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector MCP tools list failed",
        ) from exc
