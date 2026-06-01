from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from opensvc_gateway_mcp.schemas.ai import LlmProfile


class LlmClientError(Exception):
    """Base exception for upstream LLM provider errors."""


class LlmHttpError(LlmClientError):
    def __init__(self, status_code: int, detail: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LLM HTTP request failed with status {status_code}")


class LlmTransportError(LlmClientError):
    """The LLM provider could not be reached or timed out."""


class LlmProtocolError(LlmClientError):
    """The LLM provider returned an invalid or unsupported response."""


class UnsupportedLlmProvider(LlmClientError):
    def __init__(self, provider: str, supported_providers: list[str]) -> None:
        self.provider = provider
        self.supported_providers = supported_providers
        super().__init__(f"Unsupported LLM provider: {provider}")


@dataclass(frozen=True)
class LlmToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmAssistantMessage:
    content: str
    tool_calls: list[LlmToolCall]
    raw_tool_calls: list[dict[str, Any]]


@dataclass(frozen=True)
class LlmChatCompletion:
    message: LlmAssistantMessage


@dataclass(frozen=True)
class LlmStreamChunk:
    delta: str = ""
    message: LlmAssistantMessage | None = None


class LlmProviderClient(Protocol):
    async def chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LlmChatCompletion:
        ...

    def stream_chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LlmStreamChunk]:
        ...
