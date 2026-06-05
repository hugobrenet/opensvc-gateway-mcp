from typing import Annotated

from fastapi import APIRouter, Depends

from opensvc_gateway_mcp.api.dependencies import (
    get_gateway_session_service,
    require_internal_token,
)
from opensvc_gateway_mcp.core.session_service import GatewaySessionService
from opensvc_gateway_mcp.schemas.sessions import (
    CreateGatewaySessionRequest,
    DeleteGatewaySessionResponse,
    GatewaySessionResponse,
)


router = APIRouter(prefix="/internal/v1/sessions", tags=["internal"])


@router.post("", response_model=GatewaySessionResponse)
async def create_gateway_session(
    request: CreateGatewaySessionRequest,
    _: Annotated[None, Depends(require_internal_token)],
    service: Annotated[GatewaySessionService, Depends(get_gateway_session_service)],
) -> GatewaySessionResponse:
    return await service.create(request)


@router.delete("/{session_id}", response_model=DeleteGatewaySessionResponse)
async def delete_gateway_session(
    session_id: str,
    _: Annotated[None, Depends(require_internal_token)],
    service: Annotated[GatewaySessionService, Depends(get_gateway_session_service)],
) -> DeleteGatewaySessionResponse:
    return await service.delete(session_id)
