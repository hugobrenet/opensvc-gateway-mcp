import json
from typing import Any

import httpx

from opensvc_gateway_mcp.clients.llm.base import (
    LlmAssistantMessage,
    LlmChatCompletion,
    LlmHttpError,
    LlmProtocolError,
    LlmStreamChunk,
    LlmToolCall,
    LlmTransportError,
)
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.schemas.ai import LlmProfile


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
        payload = _chat_payload(profile=profile, messages=messages, tools=tools)
        response = await self._post(profile=profile, payload=payload)
        return _parse_chat_completion(response.json())

    async def stream_chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        payload = _chat_payload(profile=profile, messages=messages, tools=tools)
        payload["stream"] = True
        accumulator = _OpenAIStreamAccumulator()

        async for payload in self._stream(profile=profile, payload=payload):
            chunk = accumulator.apply(payload)
            if chunk is not None:
                yield chunk

        yield LlmStreamChunk(message=accumulator.message())

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

    async def _stream(
        self,
        *,
        profile: LlmProfile,
        payload: dict[str, Any],
    ):
        url = f"{profile.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Accept": "text/event-stream",
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
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise LlmHttpError(
                            response.status_code,
                            detail=_extract_error_detail_from_bytes(body),
                        )
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise LlmProtocolError(
                                "LLM stream returned invalid JSON"
                            ) from exc
                        if not isinstance(parsed, dict):
                            raise LlmProtocolError(
                                "LLM stream returned a non-object event"
                            )
                        yield parsed
            except LlmHttpError:
                raise
            except httpx.HTTPError as exc:
                raise LlmTransportError(
                    f"LLM HTTP request failed: {type(exc).__name__}"
                ) from exc


def _chat_payload(
    *,
    profile: LlmProfile,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    return payload


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


def _extract_error_detail_from_bytes(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None

    error = payload.get("error") if isinstance(payload, dict) else None
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


class _OpenAIStreamAccumulator:
    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}

    def apply(self, payload: dict[str, Any]) -> LlmStreamChunk | None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return None

        content = delta.get("content")
        if isinstance(content, str) and content:
            self.content_parts.append(content)

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw in tool_calls:
                self._append_tool_call(raw)
        if isinstance(content, str) and content:
            return LlmStreamChunk(delta=content)
        return None

    def message(self) -> LlmAssistantMessage:
        raw_tool_calls = [
            self._raw_tool_call(self.tool_calls[index])
            for index in sorted(self.tool_calls)
        ]
        parsed_tool_calls = []
        for index, raw in enumerate(raw_tool_calls):
            function = raw["function"]
            parsed_tool_calls.append(
                LlmToolCall(
                    id=str(raw.get("id") or f"tool_call_{index}"),
                    name=function["name"],
                    arguments=_parse_tool_arguments(function.get("arguments")),
                )
            )

        return LlmAssistantMessage(
            content="".join(self.content_parts),
            tool_calls=parsed_tool_calls,
            raw_tool_calls=raw_tool_calls,
        )

    def _append_tool_call(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        index = raw.get("index")
        if not isinstance(index, int):
            index = len(self.tool_calls)
        current = self.tool_calls.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "name": "",
                "arguments": "",
            },
        )
        tool_call_id = raw.get("id")
        if isinstance(tool_call_id, str) and tool_call_id:
            current["id"] = tool_call_id
        tool_call_type = raw.get("type")
        if isinstance(tool_call_type, str) and tool_call_type:
            current["type"] = tool_call_type

        function = raw.get("function")
        if not isinstance(function, dict):
            return
        name = function.get("name")
        if isinstance(name, str) and name:
            current["name"] += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            current["arguments"] += arguments

    def _raw_tool_call(self, value: dict[str, Any]) -> dict[str, Any]:
        if not value["name"]:
            raise LlmProtocolError("LLM stream returned a tool call without a name")
        return {
            "id": value["id"],
            "type": value["type"],
            "function": {
                "name": value["name"],
                "arguments": value["arguments"],
            },
        }
