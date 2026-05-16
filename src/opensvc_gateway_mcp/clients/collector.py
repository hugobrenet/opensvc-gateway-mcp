from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.schemas.ai import LlmProfile


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

    async def get_ai_config(
        self,
        credentials: HTTPBasicCredentials,
    ) -> LlmProfile:
        headers = {}
        if self.settings.gateway_internal_token:
            headers["X-OpenSVC-Gateway-Token"] = self.settings.gateway_internal_token
        response = await self._get(
            self.settings.collector_ai_config_path,
            credentials=credentials,
            headers=headers,
            auth_error_statuses={401},
        )
        return LlmProfile.model_validate(_unwrap_config_payload(response.json()))

    async def _get(
        self,
        path: str,
        credentials: HTTPBasicCredentials,
        headers: dict[str, str] | None = None,
        auth_error_statuses: set[int] | None = None,
    ) -> httpx.Response:
        url = (
            f"{self.settings.collector_api_base_url.rstrip('/')}/"
            f"{path.lstrip('/')}"
        )
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        async with httpx.AsyncClient(
            timeout=self.settings.collector_request_timeout_seconds,
            verify=self.settings.collector_tls_verify,
            transport=self.transport,
        ) as client:
            response = await client.get(
                url,
                auth=(credentials.username, credentials.password),
                headers=request_headers,
            )

        if auth_error_statuses is None:
            auth_error_statuses = {401, 403}
        if response.status_code in auth_error_statuses:
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


def _unwrap_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("config", "llm", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload
