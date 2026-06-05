from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import LlmProviderClient, create_llm_client
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.config import Settings, get_settings
from opensvc_gateway_mcp.core.mcp_proxy import McpProxy
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrator
from opensvc_gateway_mcp.core.session_service import GatewaySessionService
from opensvc_gateway_mcp.core.sessions import (
    GatewaySessionStore,
    RedisGatewaySessionStore,
)


def get_collector_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> CollectorClient:
    return CollectorClient(settings)


def get_collector_client_provider() -> Callable[[], CollectorClient]:
    return lambda: CollectorClient(get_settings())


def get_mcp_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> McpClient:
    return McpClient(settings)


def get_mcp_client_provider() -> Callable[[], McpClient]:
    return lambda: McpClient(get_settings())


def get_llm_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LlmProviderClient:
    return create_llm_client(settings)


def get_llm_client_provider() -> Callable[[], LlmProviderClient]:
    return lambda: create_llm_client(get_settings())


def get_ai_orchestrator(
    collector_client_provider: Annotated[
        Callable[[], CollectorClient], Depends(get_collector_client_provider)
    ],
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
    llm_client_provider: Annotated[
        Callable[[], LlmProviderClient], Depends(get_llm_client_provider)
    ],
) -> AiOrchestrator:
    return AiOrchestrator(
        collector=collector_client_provider(),
        mcp_client_provider=mcp_client_provider,
        llm=llm_client_provider(),
    )


def get_mcp_proxy(
    mcp_client_provider: Annotated[
        Callable[[], McpClient], Depends(get_mcp_client_provider)
    ],
) -> McpProxy:
    return McpProxy(mcp_client_provider)


@lru_cache
def get_gateway_session_store() -> GatewaySessionStore:
    settings = get_settings()
    return RedisGatewaySessionStore(
        redis_url=settings.gateway_redis_url,
        key_prefix=settings.gateway_redis_key_prefix,
    )


def get_gateway_session_service(
    settings: Annotated[Settings, Depends(get_settings)],
    collector: Annotated[CollectorClient, Depends(get_collector_client)],
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
) -> GatewaySessionService:
    return GatewaySessionService(
        settings=settings,
        collector=collector,
        store=store,
    )


async def get_gateway_session_credentials(
    store: Annotated[GatewaySessionStore, Depends(get_gateway_session_store)],
    x_opensvc_ai_session: Annotated[str | None, Header()] = None,
) -> HTTPBasicCredentials:
    if not x_opensvc_ai_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OpenSVC AI session",
        )

    session = await store.get(x_opensvc_ai_session)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OpenSVC AI session",
        )

    return HTTPBasicCredentials(
        username=session.username,
        password=session.password.get_secret_value(),
    )


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
