import asyncio
import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client_provider,
    get_gateway_session_store,
    get_llm_client_provider,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.llm import (
    LlmAssistantMessage,
    LlmChatCompletion,
    LlmToolCall,
)
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore
from opensvc_gateway_mcp.main import create_app
from opensvc_gateway_mcp.schemas.ai import LlmProfile


def create_session(store, **kwargs):
    return asyncio.run(store.create(**kwargs))


class FakeCollectorClient:
    def __init__(self) -> None:
        self.credentials = []

    async def get_ai_config(self, credentials):
        self.credentials.append(credentials)
        return LlmProfile(
            provider="openai_compatible",
            base_url="http://llm.invalid/v1",
            model="local-model",
            api_key=SecretStr("provider-secret"),
            system_prompt="Use MCP proxy tools to answer OpenSVC questions.",
            max_tool_iterations=4,
        )


class FakeMcpClient:
    def __init__(self) -> None:
        self.list_credentials = []
        self.tool_calls = []

    async def list_tools(self, credentials):
        self.list_credentials.append(credentials)
        return {
            "tools": [
                {
                    "name": "search_tools",
                    "description": "Search MCP tools",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "call_tool",
                    "description": "Call one searched MCP tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {"type": "object"},
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "count_nodes",
                    "description": "Must not be exposed directly to the LLM",
                    "inputSchema": {"type": "object"},
                },
            ]
        }

    async def call_tool(self, credentials, *, name, arguments=None):
        self.tool_calls.append(
            {
                "credentials": credentials,
                "name": name,
                "arguments": arguments,
            }
        )
        if name == "search_tools":
            return {
                "structuredContent": {
                    "result": [
                        {
                            "name": "count_nodes",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"request": {"type": "object"}},
                            },
                        }
                    ]
                },
                "isError": False,
            }
        if name == "call_tool":
            return {
                "structuredContent": {
                    "count": 4,
                    "filters": {"status": "down"},
                },
                "isError": False,
            }
        raise AssertionError(f"unexpected MCP tool {name}")


class FakeLlmClient:
    def __init__(self) -> None:
        self.calls = []

    async def chat(self, *, profile, messages, tools=None):
        self.calls.append(
            {
                "profile": profile,
                "messages": messages,
                "tools": tools,
            }
        )
        if len(self.calls) == 1:
            return _completion_with_tool_call(
                call_id="search-1",
                name="search_tools",
                arguments={"query": "count nodes status down"},
            )
        if len(self.calls) == 2:
            assert any(message["role"] == "tool" for message in messages)
            return _completion_with_tool_call(
                call_id="call-1",
                name="call_tool",
                arguments={
                    "name": "count_nodes",
                    "arguments": {"request": {"status": "down"}},
                },
            )
        if len(self.calls) == 3:
            assert any(
                '"count": 4' in message.get("content", "")
                for message in messages
                if message["role"] == "tool"
            )
            return LlmChatCompletion(
                message=LlmAssistantMessage(
                    content="There are 4 down nodes.",
                    tool_calls=[],
                    raw_tool_calls=[],
                )
            )
        raise AssertionError("unexpected extra LLM call")


def _completion_with_tool_call(
    *,
    call_id: str,
    name: str,
    arguments: dict,
) -> LlmChatCompletion:
    raw_tool_call = {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }
    return LlmChatCompletion(
        message=LlmAssistantMessage(
            content="",
            tool_calls=[
                LlmToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                )
            ],
            raw_tool_calls=[raw_tool_call],
        )
    )


def test_ai_chat_orchestrates_llm_search_and_call_tool():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    collector = FakeCollectorClient()
    mcp = FakeMcpClient()
    llm = FakeLlmClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_collector_client_provider] = lambda: lambda: collector
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    app.dependency_overrides[get_llm_client_provider] = lambda: lambda: llm
    client = TestClient(app)

    response = client.post(
        "/api/v1/ai/chat",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"message": "How many nodes are down?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "There are 4 down nodes.",
        "provider": "openai_compatible",
        "model": "local-model",
        "tool_calls": [
            {
                "name": "search_tools",
                "arguments": {"query": "count nodes status down"},
                "ok": True,
            },
            {
                "name": "call_tool",
                "arguments": {
                    "name": "count_nodes",
                    "arguments": {"request": {"status": "down"}},
                },
                "ok": True,
            },
        ],
    }
    assert collector.credentials[0].username == "user-a"
    assert mcp.list_credentials[0].password == "secret"
    assert [call["name"] for call in mcp.tool_calls] == [
        "search_tools",
        "call_tool",
    ]
    first_llm_tools = llm.calls[0]["tools"]
    assert [tool["function"]["name"] for tool in first_llm_tools] == [
        "search_tools",
        "call_tool",
    ]
    call_tool = first_llm_tools[1]["function"]
    assert '"arguments":{"request":{"nodename":"node1"}}' in call_tool["description"]
    assert '"request":{"nodename":"node1"}' in call_tool["description"]
    assert "Do not put target tool fields at the top level" in (
        call_tool["parameters"]["properties"]["arguments"]["description"]
    )
    assert "count_nodes" not in str(first_llm_tools)
    assert "provider-secret" not in response.text
    assert "secret" not in response.text


def test_ai_chat_requires_gateway_session_header():
    app = create_app()
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: InMemoryGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.post("/api/v1/ai/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing OpenSVC AI session"
