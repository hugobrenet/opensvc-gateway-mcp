from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import (
    CollectorClient,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.schemas.auth import AuthCheckResponse


class CollectorAuthCredentialsError(Exception):
    pass


async def check_collector_auth(
    *,
    collector: CollectorClient,
    credentials: HTTPBasicCredentials,
) -> AuthCheckResponse:
    try:
        principal = await collector.get_self(credentials)
    except InvalidCollectorCredentials as exc:
        raise CollectorAuthCredentialsError from exc

    return AuthCheckResponse(authenticated=True, username=principal.username)
