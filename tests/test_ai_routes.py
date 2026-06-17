import asyncio
import json

from fastapi.security import HTTPBasicCredentials
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
    LlmStreamChunk,
    LlmToolCall,
    create_llm_client,
)
from opensvc_gateway_mcp.config import Settings
from tests.fakes import FakeGatewaySessionStore
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrator, clear_mcp_list_tools_cache
from opensvc_gateway_mcp.main import create_app
from opensvc_gateway_mcp.schemas.ai import AiChatRequest, LlmProfile


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
        self.list_request_ids = []
        self.tool_calls = []

    async def list_tools(self, credentials, *, request_id=None):
        self.list_credentials.append(credentials)
        self.list_request_ids.append(request_id)
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

    async def call_tool(self, credentials, *, name, arguments=None, request_id=None):
        self.tool_calls.append(
            {
                "credentials": credentials,
                "name": name,
                "arguments": arguments,
                "request_id": request_id,
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


class CacheableFakeMcpClient(FakeMcpClient):
    list_tools_cache_key = "fake-mcp-cache-key"


class FakeLlmClient:
    def __init__(self) -> None:
        self.calls = []

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


class FakeStreamingToolLlmClient:
    def __init__(self) -> None:
        self.calls = []

    async def stream_chat(self, *, profile, messages, tools=None):
        self.calls.append(
            {
                "profile": profile,
                "messages": messages,
                "tools": tools,
                "stream": True,
            }
        )
        if len(self.calls) == 1:
            yield LlmStreamChunk(
                message=_assistant_message_with_tool_call(
                    call_id="search-1",
                    name="search_tools",
                    arguments={"query": "count nodes status down"},
                )
            )
            return
        if len(self.calls) == 2:
            yield LlmStreamChunk(
                message=_assistant_message_with_tool_call(
                    call_id="call-1",
                    name="call_tool",
                    arguments={
                        "name": "count_nodes",
                        "arguments": {"request": {"status": "down"}},
                    },
                )
            )
            return
        if len(self.calls) == 3:
            yield LlmStreamChunk(
                message=LlmAssistantMessage(
                    content="There are 4 down nodes.",
                    tool_calls=[],
                    raw_tool_calls=[],
                )
            )
            return
        raise AssertionError("unexpected extra streaming LLM call")


class FakeConfirmationToolLlmClient:
    def __init__(self) -> None:
        self.calls = []

    async def stream_chat(self, *, profile, messages, tools=None):
        self.calls.append(
            {
                "profile": profile,
                "messages": messages,
                "tools": tools,
                "stream": True,
            }
        )
        if len(self.calls) == 1:
            yield LlmStreamChunk(
                message=_assistant_message_with_tool_call(
                    call_id="delete-1",
                    name="call_tool",
                    arguments={
                        "name": "delete_node",
                        "arguments": {
                            "request": {
                                "node_id": "node-id-1",
                                "confirm_node_id": "node-id-1",
                                "confirm_nodename": "node-a",
                                "confirmation": {
                                    "phrase": "DELETE node node-id-1 node-a"
                                },
                            }
                        },
                    },
                )
            )
            return
        if len(self.calls) == 2:
            yield LlmStreamChunk(
                message=LlmAssistantMessage(
                    content="Deletion confirmation is required.",
                    tool_calls=[],
                    raw_tool_calls=[],
                )
            )
            return
        raise AssertionError("unexpected extra streaming LLM call")


def _assistant_message_with_tool_call(
    *,
    call_id: str,
    name: str,
    arguments: dict,
) -> LlmAssistantMessage:
    raw_tool_call = {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }
    return LlmAssistantMessage(
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


def test_ai_chat_stream_requires_gateway_session_header():
    app = create_app()
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: FakeGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.post("/api/v1/ai/chat/stream", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing OpenSVC AI session"


def test_ai_chat_stream_returns_sse_deltas_and_done_event():
    app = create_app()
    store = FakeGatewaySessionStore()
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
    assert len(mcp.list_request_ids) == 1
    assert mcp.list_request_ids[0].startswith("ai_")


def test_ai_orchestrator_caches_mcp_list_tools_between_turns():
    async def run_test():
        await clear_mcp_list_tools_cache()
        collector = FakeCollectorClient()
        mcp = CacheableFakeMcpClient()
        credentials = HTTPBasicCredentials(username="user-a", password="secret")

        first_llm = FakeLlmClient()
        first = AiOrchestrator(
            collector=collector,
            mcp_client_provider=lambda: mcp,
            llm=first_llm,
            mcp_list_tools_cache_ttl_seconds=60,
        )
        async for _event in first.stream_chat(
            credentials=credentials,
            request=AiChatRequest(message="hello"),
        ):
            pass

        second_llm = FakeLlmClient()
        second = AiOrchestrator(
            collector=collector,
            mcp_client_provider=lambda: mcp,
            llm=second_llm,
            mcp_list_tools_cache_ttl_seconds=60,
        )
        async for _event in second.stream_chat(
            credentials=credentials,
            request=AiChatRequest(message="hello again"),
        ):
            pass

        assert len(mcp.list_request_ids) == 1
        assert mcp.list_request_ids[0].startswith("ai_")
        assert first_llm.calls[0]["tools"] == second_llm.calls[0]["tools"]
        await clear_mcp_list_tools_cache()

    asyncio.run(run_test())


def test_ai_chat_stream_reuses_request_id_for_mcp_tool_calls():
    app = create_app()
    store = FakeGatewaySessionStore()
    collector = FakeCollectorClient()
    mcp = FakeMcpClient()
    llm = FakeStreamingToolLlmClient()
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
    assert "event: done" in body
    assert 'event: tool_call\ndata: {"name": "count_nodes", "ok": true}' in body
    assert '"arguments"' not in body
    assert '"request"' not in body
    assert '"status": "down"' not in body
    assert len(mcp.list_request_ids) == 1
    request_id = mcp.list_request_ids[0]
    assert request_id.startswith("ai_")
    assert [call["name"] for call in mcp.tool_calls] == [
        "search_tools",
        "call_tool",
    ]
    assert mcp.tool_calls[1]["arguments"] == {
        "name": "count_nodes",
        "arguments": {"request": {"status": "down"}},
    }
    assert {call["request_id"] for call in mcp.tool_calls} == {request_id}


def test_ai_chat_stream_blocks_confirmation_phrase_missing_from_latest_user_message():
    app = create_app()
    store = FakeGatewaySessionStore()
    collector = FakeCollectorClient()
    mcp = FakeMcpClient()
    llm = FakeConfirmationToolLlmClient()
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
        json={"message": "I confirm"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: done" in body
    assert 'event: tool_call\ndata: {"name": "delete_node", "ok": false}' in body
    assert "Deletion confirmation is required." in body
    assert mcp.tool_calls == []
    tool_message = llm.calls[1]["messages"][-2]
    assert tool_message["role"] == "tool"
    assert "state_changing_tool_confirmation_required" in tool_message["content"]


def test_ai_chat_stream_allows_confirmation_phrase_in_latest_user_message():
    app = create_app()
    store = FakeGatewaySessionStore()
    collector = FakeCollectorClient()
    mcp = FakeMcpClient()
    llm = FakeConfirmationToolLlmClient()
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
        json={"message": "DELETE node node-id-1 node-a"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: done" in body
    assert 'event: tool_call\ndata: {"name": "delete_node", "ok": true}' in body
    assert [call["name"] for call in mcp.tool_calls] == ["call_tool"]
    assert mcp.tool_calls[0]["arguments"] == {
        "name": "delete_node",
        "arguments": {
            "request": {
                "node_id": "node-id-1",
                "confirm_node_id": "node-id-1",
                "confirm_nodename": "node-a",
                "confirmation": {"phrase": "DELETE node node-id-1 node-a"},
            }
        },
    }


def test_ai_chat_stream_rejects_unimplemented_llm_provider():
    app = create_app()
    store = FakeGatewaySessionStore()
    collector = FakeCollectorClient(provider="mistral")
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
    assert '"provider": "mistral"' in body
    assert '"supported_providers": ["anthropic", "openai_compatible"]' in body
    assert mcp.list_credentials[0].username == "user-a"
