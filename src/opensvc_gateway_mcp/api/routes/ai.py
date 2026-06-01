from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client_provider,
    get_gateway_session_store,
    get_llm_client_provider,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import LlmProviderClient
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrator
from opensvc_gateway_mcp.core.sessions import GatewaySessionStore
from opensvc_gateway_mcp.core.streaming import stream_ai_sse_events
from opensvc_gateway_mcp.schemas.ai import AiChatRequest


router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


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


@router.post("/chat/stream")
async def chat_stream(
    request: AiChatRequest,
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
    collector_client_provider: Annotated[
        Callable[[], CollectorClient], Depends(get_collector_client_provider)
    ],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    llm_client_provider: Annotated[
        Callable[[], LlmProviderClient], Depends(get_llm_client_provider)
    ],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    credentials = await _credentials_from_gateway_session(
        session_id=x_opensvc_ai_session,
        store=store,
    )
    orchestrator = AiOrchestrator(
        collector=collector_client_provider(),
        mcp_client_provider=mcp_client_provider,
        llm=llm_client_provider(),
    )

    return StreamingResponse(
        stream_ai_sse_events(orchestrator, credentials=credentials, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
