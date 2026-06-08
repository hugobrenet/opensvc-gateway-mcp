import json
from typing import Any

import httpx

from opensvc_gateway_mcp.clients.llm.base import (
    LlmAssistantMessage,
    LlmHttpError,
    LlmProtocolError,
    LlmStreamChunk,
    LlmToolCall,
    LlmTransportError,
)
from opensvc_gateway_mcp.clients.llm.openai_compatible import (
    _extract_error_detail_from_bytes,
)
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.schemas.ai import LlmProfile


ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicLlmClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def stream_chat(
        self,
        *,
        profile: LlmProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        payload = _messages_payload(profile=profile, messages=messages, tools=tools)
        payload["stream"] = True
        accumulator = _AnthropicStreamAccumulator()

        async for event in self._stream(profile=profile, payload=payload):
            chunk = accumulator.apply(event)
            if chunk is not None:
                yield chunk

        yield LlmStreamChunk(message=accumulator.message())

    async def _stream(
        self,
        *,
        profile: LlmProfile,
        payload: dict[str, Any],
    ):
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if profile.api_key is not None:
            headers["x-api-key"] = profile.api_key.get_secret_value()

        async with httpx.AsyncClient(
            timeout=self.settings.llm_request_timeout_seconds,
            transport=self.transport,
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    _messages_url(profile.base_url),
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
                        if parsed.get("type") == "error":
                            error = parsed.get("error")
                            if isinstance(error, dict):
                                message = error.get("message")
                                if isinstance(message, str) and message:
                                    raise LlmProtocolError(message)
                            raise LlmProtocolError("LLM stream returned an error event")
                        yield parsed
            except LlmHttpError:
                raise
            except httpx.HTTPError as exc:
                raise LlmTransportError(
                    f"LLM HTTP request failed: {type(exc).__name__}"
                ) from exc


def _messages_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _messages_payload(
    *,
    profile: LlmProfile,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    system_prompt, anthropic_messages = _anthropic_messages(messages)
    payload: dict[str, Any] = {
        "model": profile.model,
        "max_tokens": profile.max_tokens or DEFAULT_MAX_TOKENS,
        "messages": anthropic_messages,
    }
    if system_prompt:
        payload["system"] = system_prompt
    if tools:
        payload["tools"] = _anthropic_tools(tools)
    if profile.temperature is not None:
        payload["temperature"] = profile.temperature
    return payload


def _anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue

        if role == "tool":
            pending_tool_results.append(_anthropic_tool_result(message))
            continue

        if pending_tool_results:
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": pending_tool_results,
                }
            )
            pending_tool_results = []

        if role == "user":
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": _string_content(message.get("content")),
                }
            )
            continue

        if role == "assistant":
            anthropic_messages.append(
                {
                    "role": "assistant",
                    "content": _anthropic_assistant_content(message),
                }
            )
            continue

    if pending_tool_results:
        anthropic_messages.append(
            {
                "role": "user",
                "content": pending_tool_results,
            }
        )

    return "\n\n".join(system_parts) or None, anthropic_messages


def _string_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _anthropic_assistant_content(message: dict[str, Any]) -> list[dict[str, Any]]:
    content_blocks: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        content_blocks.append({"type": "text", "text": content})

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for raw_tool_call in tool_calls:
            tool_call = _tool_call_from_raw(raw_tool_call)
            if tool_call is not None:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.arguments,
                    }
                )

    return content_blocks or [{"type": "text", "text": ""}]


def _tool_call_from_raw(raw: Any) -> LlmToolCall | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("type") == "tool_use":
        tool_id = raw.get("id")
        name = raw.get("name")
        arguments = raw.get("input")
        if isinstance(tool_id, str) and isinstance(name, str):
            return LlmToolCall(
                id=tool_id,
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        return None

    function = raw.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    return LlmToolCall(
        id=str(raw.get("id") or name),
        name=name,
        arguments=_parse_tool_arguments(function.get("arguments")),
    )


def _anthropic_tool_result(message: dict[str, Any]) -> dict[str, Any]:
    tool_call_id = message.get("tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise LlmProtocolError("Tool result message is missing tool_call_id")
    return {
        "type": "tool_result",
        "tool_use_id": tool_call_id,
        "content": _string_content(message.get("content")),
    }


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anthropic_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        input_schema = function.get("parameters")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        anthropic_tools.append(
            {
                "name": name,
                "description": _string_content(function.get("description")),
                "input_schema": input_schema,
            }
        )
    return anthropic_tools


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


class _AnthropicStreamAccumulator:
    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.blocks: dict[int, dict[str, Any]] = {}

    def apply(self, event: dict[str, Any]) -> LlmStreamChunk | None:
        event_type = event.get("type")
        if event_type == "content_block_start":
            self._start_block(event)
            return None
        if event_type == "content_block_delta":
            return self._apply_delta(event)
        return None

    def message(self) -> LlmAssistantMessage:
        raw_tool_calls = []
        parsed_tool_calls = []
        for index in sorted(self.blocks):
            block = self.blocks[index]
            if block.get("type") != "tool_use":
                continue
            raw_tool_call = self._raw_tool_call(block)
            raw_tool_calls.append(raw_tool_call)
            parsed_tool_calls.append(
                LlmToolCall(
                    id=raw_tool_call["id"],
                    name=raw_tool_call["function"]["name"],
                    arguments=_parse_tool_arguments(
                        raw_tool_call["function"]["arguments"]
                    ),
                )
            )

        return LlmAssistantMessage(
            content="".join(self.content_parts),
            tool_calls=parsed_tool_calls,
            raw_tool_calls=raw_tool_calls,
        )

    def _start_block(self, event: dict[str, Any]) -> None:
        index = event.get("index")
        content_block = event.get("content_block")
        if not isinstance(index, int) or not isinstance(content_block, dict):
            return
        block_type = content_block.get("type")
        if block_type == "text":
            self.blocks[index] = {
                "type": "text",
                "text": _string_content(content_block.get("text")),
            }
            text = self.blocks[index]["text"]
            if text:
                self.content_parts.append(text)
            return
        if block_type == "tool_use":
            initial_input = content_block.get("input")
            self.blocks[index] = {
                "type": "tool_use",
                "id": _string_content(content_block.get("id")),
                "name": _string_content(content_block.get("name")),
                "input_json": (
                    json.dumps(initial_input)
                    if isinstance(initial_input, dict) and initial_input
                    else ""
                ),
            }

    def _apply_delta(self, event: dict[str, Any]) -> LlmStreamChunk | None:
        index = event.get("index")
        delta = event.get("delta")
        if not isinstance(index, int) or not isinstance(delta, dict):
            return None

        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                self.content_parts.append(text)
                return LlmStreamChunk(delta=text)
            return None

        if delta_type == "input_json_delta":
            block = self.blocks.setdefault(
                index,
                {
                    "type": "tool_use",
                    "id": "",
                    "name": "",
                    "input_json": "",
                },
            )
            partial_json = delta.get("partial_json")
            if isinstance(partial_json, str):
                block["input_json"] += partial_json
        return None

    def _raw_tool_call(self, block: dict[str, Any]) -> dict[str, Any]:
        if not block.get("name"):
            raise LlmProtocolError("LLM stream returned a tool call without a name")
        return {
            "id": _string_content(block.get("id")),
            "type": "function",
            "function": {
                "name": _string_content(block.get("name")),
                "arguments": _string_content(block.get("input_json")),
            },
        }
