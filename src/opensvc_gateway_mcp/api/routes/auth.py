from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import get_collector_client
from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.core.auth_service import check_collector_auth
from opensvc_gateway_mcp.core.security import require_basic_credentials
from opensvc_gateway_mcp.schemas.auth import AuthCheckResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/check", response_model=AuthCheckResponse)
async def check_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(require_basic_credentials)],
    collector: Annotated[CollectorClient, Depends(get_collector_client)],
) -> AuthCheckResponse:
    return await check_collector_auth(collector=collector, credentials=credentials)
