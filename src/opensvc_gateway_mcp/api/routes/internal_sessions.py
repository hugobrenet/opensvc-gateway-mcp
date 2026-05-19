from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client,
    get_gateway_session_store,
)
from opensvc_gateway_mcp.clients.collector import (
    CollectorClient,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.config import Settings, get_settings
from opensvc_gateway_mcp.core.sessions import GatewaySessionStore
from opensvc_gateway_mcp.schemas.sessions import (
    CreateGatewaySessionRequest,
    DeleteGatewaySessionResponse,
    GatewaySessionResponse,
)


router = APIRouter(prefix="/internal/v1/sessions", tags=["internal"])


def require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_opensvc_gateway_token: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.gateway_internal_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway internal token is not configured",
        )
    if x_opensvc_gateway_token != settings.gateway_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid gateway internal token",
        )


@router.post("", response_model=GatewaySessionResponse)
async def create_gateway_session(
    request: CreateGatewaySessionRequest,
    _: Annotated[None, Depends(require_internal_token)],
    settings: Annotated[Settings, Depends(get_settings)],
    collector: Annotated[CollectorClient, Depends(get_collector_client)],
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
) -> GatewaySessionResponse:
    credentials = HTTPBasicCredentials(
        username=request.username,
        password=request.password.get_secret_value(),
    )
    try:
        principal = await collector.get_self(credentials)
    except InvalidCollectorCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Collector credentials",
        ) from exc

    ttl_seconds = request.ttl_seconds or settings.gateway_session_ttl_seconds
    session = await store.create(
        username=principal.username,
        password=request.password,
        ttl_seconds=ttl_seconds,
    )
    return GatewaySessionResponse(
        session_id=session.session_id,
        username=session.username,
        expires_at=session.expires_at,
    )


@router.delete("/{session_id}", response_model=DeleteGatewaySessionResponse)
async def delete_gateway_session(
    session_id: str,
    _: Annotated[None, Depends(require_internal_token)],
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
) -> DeleteGatewaySessionResponse:
    return DeleteGatewaySessionResponse(deleted=await store.delete(session_id))
