import json
from dataclasses import dataclass
from typing import Any

import httpx

from opensvc_gateway_mcp.config import Settings
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


class OpenAICompatibleLlmClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LlmChatCompletion:
        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if profile.temperature is not None:
            payload["temperature"] = profile.temperature
        if profile.max_tokens is not None:
            payload[profile.completion_token_parameter] = profile.max_tokens

        response = await self._post(profile=profile, payload=payload)
        return _parse_chat_completion(response.json())

    async def _post(
        self,
        *,
        profile: LlmProfile,
        payload: dict[str, Any],
    ) -> httpx.Response:
        url = f"{profile.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if profile.api_key is not None:
            headers["Authorization"] = (
                f"Bearer {profile.api_key.get_secret_value()}"
            )

        async with httpx.AsyncClient(
            timeout=self.settings.llm_request_timeout_seconds,
            transport=self.transport,
        ) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise LlmTransportError(
                    f"LLM HTTP request failed: {type(exc).__name__}"
                ) from exc

        if response.status_code >= 400:
            raise LlmHttpError(
                response.status_code,
                detail=_extract_error_detail(response),
            )
        return response


def _extract_error_detail(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    error = payload.get("error")
    if not isinstance(error, dict):
        return None

    detail: dict[str, Any] = {}
    for key in ("message", "type", "code", "param"):
        value = error.get(key)
        if isinstance(value, str) or value is None:
            detail[key] = value
    return detail or None


def _parse_chat_completion(payload: dict[str, Any]) -> LlmChatCompletion:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmProtocolError("LLM response did not contain choices")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LlmProtocolError("LLM response did not contain a message")

    content = message.get("content")
    tool_calls = message.get("tool_calls")
    parsed_tool_calls = []
    raw_tool_calls = []
    if isinstance(tool_calls, list):
        for index, raw in enumerate(tool_calls):
            if not isinstance(raw, dict):
                continue
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            parsed_tool_calls.append(
                LlmToolCall(
                    id=str(raw.get("id") or f"tool_call_{index}"),
                    name=name,
                    arguments=_parse_tool_arguments(function.get("arguments")),
                )
            )
            raw_tool_calls.append(raw)

    return LlmChatCompletion(
        message=LlmAssistantMessage(
            content=content if isinstance(content, str) else "",
            tool_calls=parsed_tool_calls,
            raw_tool_calls=raw_tool_calls,
        )
    )


def _parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        raise LlmProtocolError("LLM tool call arguments were not a JSON object")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LlmProtocolError("LLM tool call arguments were not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LlmProtocolError("LLM tool call arguments were not a JSON object")
    return parsed
