import json

import httpx
import pytest
from pydantic import SecretStr

from opensvc_gateway_mcp.clients.llm import AnthropicLlmClient
from opensvc_gateway_mcp.clients.llm.base import LlmHttpError
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.schemas.ai import LlmProfile


def _settings() -> Settings:
    return Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api"
    )


@pytest.mark.anyio
async def test_anthropic_stream_chat_sends_messages_request_and_streams_text():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="\n".join(
                [
                    'data: {"type":"message_start","message":{"id":"msg_1"}}',
                    'data: {"type":"content_block_start","index":0,'
                    '"content_block":{"type":"text","text":""}}',
                    'data: {"type":"content_block_delta","index":0,'
                    '"delta":{"type":"text_delta","text":"Hello"}}',
                    'data: {"type":"content_block_delta","index":0,'
                    '"delta":{"type":"text_delta","text":" world"}}',
                    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
                    'data: {"type":"message_stop"}',
                ]
            ),
        )

    client = AnthropicLlmClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    profile = LlmProfile(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key=SecretStr("anthropic-secret"),
        system_prompt="Use Collector tools.",
        max_tokens=512,
    )

    chunks = [
        chunk
        async for chunk in client.stream_chat(
            profile=profile,
            messages=[{"role": "system", "content": "System from messages."}, {"role": "user", "content": "hello"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search_tools",
                        "description": "Search tools",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    },
                }
            ],
        )
    ]

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "anthropic-secret"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"] == {
        "model": "claude-test",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "hello"}],
        "system": "System from messages.",
        "tools": [
            {
                "name": "search_tools",
                "description": "Search tools",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }
        ],
        "stream": True,
    }
    assert [chunk.delta for chunk in chunks if chunk.delta] == ["Hello", " world"]
    assert chunks[-1].message is not None
    assert chunks[-1].message.content == "Hello world"
    assert chunks[-1].message.tool_calls == []


@pytest.mark.anyio
async def test_anthropic_stream_chat_parses_tool_use():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="\n".join(
                [
                    'data: {"type":"message_start","message":{"id":"msg_1"}}',
                    'data: {"type":"content_block_start","index":0,'
                    '"content_block":{"type":"tool_use","id":"toolu_1",'
                    '"name":"call_tool","input":{}}}',
                    'data: {"type":"content_block_delta","index":0,'
                    '"delta":{"type":"input_json_delta","partial_json":"{'
                    '\\"name\\":\\"count_nodes\\",\\"arguments\\":{'
                    '\\"request\\":{}}}"}}',
                    'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
                    'data: {"type":"message_stop"}',
                ]
            ),
        )

    client = AnthropicLlmClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    profile = LlmProfile(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        model="claude-test",
        api_key=SecretStr("anthropic-secret"),
    )

    chunks = [
        chunk
        async for chunk in client.stream_chat(
            profile=profile,
            messages=[{"role": "user", "content": "count nodes"}],
            tools=None,
        )
    ]

    message = chunks[-1].message
    assert message is not None
    assert message.content == ""
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0].id == "toolu_1"
    assert message.tool_calls[0].name == "call_tool"
    assert message.tool_calls[0].arguments == {
        "name": "count_nodes",
        "arguments": {"request": {}},
    }
    assert message.raw_tool_calls == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {
                "name": "call_tool",
                "arguments": '{"name":"count_nodes","arguments":{"request":{}}}',
            },
        }
    ]


@pytest.mark.anyio
async def test_anthropic_stream_chat_converts_previous_tool_results():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="\n".join(
                [
                    'data: {"type":"content_block_start","index":0,'
                    '"content_block":{"type":"text","text":""}}',
                    'data: {"type":"content_block_delta","index":0,'
                    '"delta":{"type":"text_delta","text":"done"}}',
                    'data: {"type":"message_stop"}',
                ]
            ),
        )

    client = AnthropicLlmClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    profile = LlmProfile(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key=SecretStr("anthropic-secret"),
    )

    chunks = [
        chunk
        async for chunk in client.stream_chat(
            profile=profile,
            messages=[
                {"role": "user", "content": "count nodes"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "type": "function",
                            "function": {
                                "name": "call_tool",
                                "arguments": '{"name":"count_nodes","arguments":{}}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "toolu_1",
                    "content": '{"structuredContent":{"count":4}}',
                },
                {"role": "user", "content": "final answer"},
            ],
        )
    ]

    assert chunks[-1].message is not None
    assert captured["payload"]["messages"] == [
        {"role": "user", "content": "count nodes"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "call_tool",
                    "input": {"name": "count_nodes", "arguments": {}},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"structuredContent":{"count":4}}',
                }
            ],
        },
        {"role": "user", "content": "final answer"},
    ]


@pytest.mark.anyio
async def test_anthropic_stream_chat_maps_http_errors():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "type": "authentication_error",
                    "message": "invalid api key",
                }
            },
        )

    client = AnthropicLlmClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    profile = LlmProfile(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key=SecretStr("anthropic-secret"),
    )

    with pytest.raises(LlmHttpError) as exc_info:
        [
            chunk
            async for chunk in client.stream_chat(
                profile=profile,
                messages=[{"role": "user", "content": "hello"}],
            )
        ]

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == {
        "code": None,
        "message": "invalid api key",
        "param": None,
        "type": "authentication_error",
    }
