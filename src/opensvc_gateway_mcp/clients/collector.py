from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.config import Settings


class InvalidCollectorCredentials(Exception):
    """Collector rejected the supplied user credentials."""


@dataclass(frozen=True)
class CollectorPrincipal:
    username: str
    raw: dict[str, Any]


class CollectorClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def get_self(
        self,
        credentials: HTTPBasicCredentials,
    ) -> CollectorPrincipal:
        response = await self._get(
            "/users/self",
            credentials=credentials,
        )
        payload = response.json()
        return CollectorPrincipal(
            username=_extract_username(payload) or credentials.username,
            raw=payload,
        )

    async def _get(
        self,
        path: str,
        credentials: HTTPBasicCredentials,
    ) -> httpx.Response:
        url = (
            f"{self.settings.collector_api_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )
        async with httpx.AsyncClient(
            timeout=self.settings.collector_request_timeout_seconds,
            verify=self.settings.collector_tls_verify,
            transport=self.transport,
        ) as client:
            response = await client.get(
                url,
                auth=(credentials.username, credentials.password),
                headers={"Accept": "application/json"},
            )

        if response.status_code in {401, 403}:
            raise InvalidCollectorCredentials

        response.raise_for_status()
        return response


def _extract_username(payload: dict[str, Any]) -> str | None:
    for key in ("username", "email"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    rows = payload.get("user")
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            for key in ("username", "email"):
                value = first.get(key)
                if isinstance(value, str) and value:
                    return value

    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for key in ("username", "email"):
                value = first.get(key)
                if isinstance(value, str) and value:
                    return value

    return None
