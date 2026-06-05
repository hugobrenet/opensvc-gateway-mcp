from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import (
    CollectorClient,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.core.sessions import GatewaySessionStore
from opensvc_gateway_mcp.schemas.sessions import (
    CreateGatewaySessionRequest,
    DeleteGatewaySessionResponse,
    GatewaySessionResponse,
)


class GatewaySessionCredentialsError(Exception):
    pass


class GatewaySessionService:
    def __init__(
        self,
        *,
        settings: Settings,
        collector: CollectorClient,
        store: GatewaySessionStore,
    ) -> None:
        self._settings = settings
        self._collector = collector
        self._store = store

    async def create(
        self,
        request: CreateGatewaySessionRequest,
    ) -> GatewaySessionResponse:
        credentials = HTTPBasicCredentials(
            username=request.username,
            password=request.password.get_secret_value(),
        )
        try:
            principal = await self._collector.get_self(credentials)
        except InvalidCollectorCredentials as exc:
            raise GatewaySessionCredentialsError from exc

        ttl_seconds = request.ttl_seconds or self._settings.gateway_session_ttl_seconds
        session = await self._store.create(
            username=principal.username,
            password=request.password,
            ttl_seconds=ttl_seconds,
        )
        return GatewaySessionResponse(
            session_id=session.session_id,
            username=session.username,
            expires_at=session.expires_at,
        )

    async def delete(self, session_id: str) -> DeleteGatewaySessionResponse:
        return DeleteGatewaySessionResponse(
            deleted=await self._store.delete(session_id),
        )
