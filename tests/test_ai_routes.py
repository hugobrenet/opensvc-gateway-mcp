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
    LlmStreamChunk,
    LlmToolCall,
    create_llm_client,
)
from opensvc_gateway_mcp.config import Settings
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore
from opensvc_gateway_mcp.main import create_app
from opensvc_gateway_mcp.schemas.ai import LlmProfile


def create_session(store, **kwargs):
    return asyncio.run(store.create(**kwargs))


class FakeCollectorClient:
    def __init__(self, *, provider: str = "openai_compatible") -> None:
        self.provider = provider
        self.credentials = []

    async def get_ai_config(self, credentials):
        self.credentials.append(credentials)
        return LlmProfile(
            provider=self.provider,
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

    async def stream_chat(self, *, profile, messages, tools=None):
        self.calls.append(
            {
                "profile": profile,
                "messages": messages,
                "tools": tools,
                "stream": True,
            }
        )
        yield LlmStreamChunk(delta="There are ")
        yield LlmStreamChunk(delta="4 down nodes.")
        yield LlmStreamChunk(
            message=LlmAssistantMessage(
                content="There are 4 down nodes.",
                tool_calls=[],
                raw_tool_calls=[],
            )
        )


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


def test_ai_chat_stream_requires_gateway_session_header():
    app = create_app()
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: InMemoryGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.post("/api/v1/ai/chat/stream", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing OpenSVC AI session"


def test_ai_chat_stream_returns_sse_deltas_and_done_event():
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

    with client.stream(
        "POST",
        "/api/v1/ai/chat/stream",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"message": "How many nodes are down?"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: delta\ndata: {"content": "There are "}' in body
    assert 'event: delta\ndata: {"content": "4 down nodes."}' in body
    assert "event: done" in body
    assert '"message": "There are 4 down nodes."' in body
    assert '"provider": "openai_compatible"' in body
    assert "provider-secret" not in body
    assert "secret" not in body
    assert llm.calls[0]["stream"] is True


def test_ai_chat_stream_rejects_unimplemented_llm_provider():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    collector = FakeCollectorClient(provider="anthropic")
    mcp = FakeMcpClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    settings = Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api"
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_collector_client_provider] = lambda: lambda: collector
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    app.dependency_overrides[get_llm_client_provider] = (
        lambda: lambda: create_llm_client(settings)
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/ai/chat/stream",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"message": "hello"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: error" in body
    assert "LLM provider is not supported by this gateway" in body
    assert '"provider": "anthropic"' in body
    assert '"supported_providers": ["openai_compatible"]' in body
    assert mcp.list_credentials[0].username == "user-a"
