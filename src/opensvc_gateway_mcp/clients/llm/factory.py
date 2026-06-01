from collections.abc import AsyncIterator
from typing import Any

import httpx

from opensvc_gateway_mcp.clients.llm.base import (
    LlmChatCompletion,
    LlmProviderClient,
    LlmStreamChunk,
    UnsupportedLlmProvider,
)
from opensvc_gateway_mcp.clients.llm.openai_compatible import OpenAICompatibleLlmClient
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.schemas.ai import LlmProfile


class LlmProviderRouter:
    def __init__(self, clients: dict[str, LlmProviderClient]) -> None:
        self.clients = clients

    @property
    def supported_providers(self) -> list[str]:
        return sorted(self.clients)

    async def chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LlmChatCompletion:
        client = self.clients.get(profile.provider)
        if client is None:
            raise UnsupportedLlmProvider(
                profile.provider,
                supported_providers=self.supported_providers,
            )
        return await client.chat(profile=profile, messages=messages, tools=tools)

    def stream_chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LlmStreamChunk]:
        client = self.clients.get(profile.provider)
        if client is None:
            raise UnsupportedLlmProvider(
                profile.provider,
                supported_providers=self.supported_providers,
            )
        return client.stream_chat(profile=profile, messages=messages, tools=tools)


def create_llm_client(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmProviderClient:
    return LlmProviderRouter(
        {
            "openai_compatible": OpenAICompatibleLlmClient(
                settings,
                transport=transport,
            ),
        }
    )
