from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import get_collector_client
from opensvc_gateway_mcp.clients.collector import (
    CollectorClient,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.core.security import require_basic_credentials
from opensvc_gateway_mcp.schemas.auth import AuthCheckResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/check", response_model=AuthCheckResponse)
async def check_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(require_basic_credentials)],
    collector: Annotated[CollectorClient, Depends(get_collector_client)],
) -> AuthCheckResponse:
    try:
        principal = await collector.get_self(credentials)
    except InvalidCollectorCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Collector credentials",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc

    return AuthCheckResponse(authenticated=True, username=principal.username)
