from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.api.dependencies import (
    get_ai_orchestrator,
    get_gateway_session_credentials,
)
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrator
from opensvc_gateway_mcp.core.streaming import stream_ai_sse_events
from opensvc_gateway_mcp.schemas.ai import AiChatRequest


router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.post("/chat/stream")
async def chat_stream(
    request: AiChatRequest,
    credentials: Annotated[
        HTTPBasicCredentials, Depends(get_gateway_session_credentials)
    ],
    orchestrator: Annotated[AiOrchestrator, Depends(get_ai_orchestrator)],
) -> StreamingResponse:
    return StreamingResponse(
        stream_ai_sse_events(orchestrator, credentials=credentials, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
