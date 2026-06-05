from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_gateway_session_credentials,
    get_mcp_proxy,
)
from opensvc_gateway_mcp.core.mcp_proxy import McpProxy
from opensvc_gateway_mcp.schemas.mcp import CallMcpToolRequest, SearchMcpToolsRequest


router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


@router.get("/tools")
async def list_mcp_tools(
    credentials: Annotated[
        HTTPBasicCredentials, Depends(get_gateway_session_credentials)
    ],
    proxy: Annotated[McpProxy, Depends(get_mcp_proxy)],
) -> dict[str, Any]:
    return await proxy.list_tools(credentials)


@router.post("/tools/search")
async def search_mcp_tools(
    request: SearchMcpToolsRequest,
    credentials: Annotated[
        HTTPBasicCredentials, Depends(get_gateway_session_credentials)
    ],
    proxy: Annotated[McpProxy, Depends(get_mcp_proxy)],
) -> dict[str, Any]:
    return await proxy.search_tools(credentials, query=request.query)


@router.post("/tools/call")
async def call_mcp_tool(
    request: CallMcpToolRequest,
    credentials: Annotated[
        HTTPBasicCredentials, Depends(get_gateway_session_credentials)
    ],
    proxy: Annotated[McpProxy, Depends(get_mcp_proxy)],
) -> dict[str, Any]:
    return await proxy.call_tool(
        credentials,
        name=request.name,
        arguments=request.arguments,
    )
