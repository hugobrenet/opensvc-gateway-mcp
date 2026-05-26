from collections.abc import Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPBasicCredentials
from pydantic import ValidationError

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client_provider,
    get_gateway_session_store,
    get_llm_client_provider,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import (
    LlmClientError,
    LlmHttpError,
    LlmProviderClient,
    UnsupportedLlmProvider,
)
from opensvc_gateway_mcp.clients.mcp import McpClient, McpClientError
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrationError, AiOrchestrator
from opensvc_gateway_mcp.core.sessions import GatewaySessionStore
from opensvc_gateway_mcp.schemas.ai import AiChatRequest, AiChatResponse


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


@router.post("/chat", response_model=AiChatResponse)
async def chat(
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
) -> AiChatResponse:
    credentials = await _credentials_from_gateway_session(
        session_id=x_opensvc_ai_session,
        store=store,
    )
    orchestrator = AiOrchestrator(
        collector=collector_client_provider(),
        mcp_client_provider=mcp_client_provider,
        llm=llm_client_provider(),
    )

    try:
        return await orchestrator.chat(credentials=credentials, request=request)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector AI configuration is invalid",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector AI configuration fetch failed",
        ) from exc
    except McpClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Collector MCP orchestration failed",
        ) from exc
    except LlmHttpError as exc:
        detail: str | dict[str, object]
        detail = {
            "message": "LLM provider request failed",
            "status_code": exc.status_code,
        }
        if exc.detail is not None:
            detail["provider_error"] = exc.detail
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from exc
    except UnsupportedLlmProvider as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "LLM provider is not supported by this gateway",
                "provider": exc.provider,
                "supported_providers": exc.supported_providers,
            },
        ) from exc
    except LlmClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM provider request failed",
        ) from exc
    except AiOrchestrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
