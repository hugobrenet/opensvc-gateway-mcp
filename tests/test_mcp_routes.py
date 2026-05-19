import asyncio
import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from opensvc_gateway_mcp.api.dependencies import (
    get_gateway_session_store,
    get_mcp_client_provider,
)
from opensvc_gateway_mcp.clients.mcp import McpClientError, McpJsonRpcError
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore
from opensvc_gateway_mcp.main import create_app


def create_session(store, **kwargs):
    return asyncio.run(store.create(**kwargs))


class FakeMcpClient:
    def __init__(self, *, fail: bool = False, json_rpc_error: bool = False) -> None:
        self.fail = fail
        self.json_rpc_error = json_rpc_error
        self.calls = []
        self.tool_calls = []

    async def list_tools(self, credentials):
        self.calls.append(credentials)
        if self.fail:
            raise McpClientError("upstream failed")
        return {
            "tools": [
                {"name": "search_tools"},
                {"name": "call_tool"},
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
        if self.fail:
            raise McpClientError("upstream failed")
        if self.json_rpc_error:
            raise McpJsonRpcError(
                code=-32602,
                message="Validation error calling get_cluster_nodes",
                data={
                    "tool": "get_cluster_nodes",
                    "expected_input_schema": {
                        "properties": {
                            "request": {
                                "properties": {
                                    "cluster_name": {"type": "string"}
                                }
                            }
                        }
                    },
                },
            )
        if name == "call_tool":
            if arguments and arguments.get("name") == "get_cluster_nodes":
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "error": "Invalid tool arguments",
                                    "tool": "get_cluster_nodes",
                                    "validation_errors": [
                                        {
                                            "type": "missing_argument",
                                            "loc": ["request"],
                                            "msg": "Missing required argument",
                                        }
                                    ],
                                    "expected_input_schema": {
                                        "properties": {
                                            "request": {
                                                "properties": {
                                                    "cluster_name": {
                                                        "type": "string"
                                                    }
                                                }
                                            }
                                        }
                                    },
                                    "hint": (
                                        "Retry with arguments matching "
                                        "expected_input_schema."
                                    ),
                                }
                            ),
                        }
                    ]
                }
            return {
                "structuredContent": {
                    "count": 3,
                    "filters": {"status": "up"},
                }
            }
        return {
            "structuredContent": {
                "result": [
                    {"name": "get_nodes_inventory_stats"},
                    {"name": "count_nodes"},
                ]
            }
        }


def test_mcp_tools_requires_gateway_session_header():
    app = create_app()
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: InMemoryGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.get("/api/v1/mcp/tools")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing OpenSVC AI session"


def test_mcp_tools_rejects_unknown_gateway_session():
    app = create_app()
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: InMemoryGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/mcp/tools",
        headers={"X-OpenSVC-AI-Session": "UNKNOWN"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired OpenSVC AI session"


def test_mcp_tools_uses_gateway_session_credentials():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    mcp = FakeMcpClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    client = TestClient(app)

    response = client.get(
        "/api/v1/mcp/tools",
        headers={"X-OpenSVC-AI-Session": session.session_id},
    )

    assert response.status_code == 200
    assert response.json() == {
        "tools": [
            {"name": "search_tools"},
            {"name": "call_tool"},
        ]
    }
    assert len(mcp.calls) == 1
    assert mcp.calls[0].username == "user-a"
    assert mcp.calls[0].password == "secret"


def test_mcp_tools_maps_mcp_client_error():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = (
        lambda: lambda: FakeMcpClient(fail=True)
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/mcp/tools",
        headers={"X-OpenSVC-AI-Session": session.session_id},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Collector MCP tools list failed"
    assert "secret" not in response.text


def test_mcp_tools_search_calls_search_tools_with_query():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    mcp = FakeMcpClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/search",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"query": "node inventory statistics summary distribution"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "structuredContent": {
            "result": [
                {"name": "get_nodes_inventory_stats"},
                {"name": "count_nodes"},
            ]
        }
    }
    assert len(mcp.tool_calls) == 1
    call = mcp.tool_calls[0]
    assert call["credentials"].username == "user-a"
    assert call["credentials"].password == "secret"
    assert call["name"] == "search_tools"
    assert call["arguments"] == {
        "query": "node inventory statistics summary distribution"
    }


def test_mcp_tools_search_requires_non_empty_query():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/search",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"query": ""},
    )

    assert response.status_code == 422


def test_mcp_tools_search_maps_mcp_client_error():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = (
        lambda: lambda: FakeMcpClient(fail=True)
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/search",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"query": "node inventory"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Collector MCP tool search failed"
    assert "secret" not in response.text


def test_mcp_tools_call_uses_call_tool_proxy():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    mcp = FakeMcpClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={
            "name": "count_nodes",
            "arguments": {"request": {"status": "up"}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "structuredContent": {
            "count": 3,
            "filters": {"status": "up"},
        }
    }
    assert len(mcp.tool_calls) == 1
    call = mcp.tool_calls[0]
    assert call["credentials"].username == "user-a"
    assert call["credentials"].password == "secret"
    assert call["name"] == "call_tool"
    assert call["arguments"] == {
        "name": "count_nodes",
        "arguments": {"request": {"status": "up"}},
    }


def test_mcp_tools_call_defaults_arguments_to_empty_object():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    mcp = FakeMcpClient()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: mcp
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"name": "get_nodes_inventory_stats"},
    )

    assert response.status_code == 200
    assert mcp.tool_calls[0]["arguments"] == {
        "name": "get_nodes_inventory_stats",
        "arguments": {},
    }


def test_mcp_tools_call_requires_non_empty_name():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"name": "", "arguments": {}},
    )

    assert response.status_code == 422


def test_mcp_tools_call_maps_mcp_client_error():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = (
        lambda: lambda: FakeMcpClient(fail=True)
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"name": "count_nodes", "arguments": {"request": {}}},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Collector MCP tool call failed"
    assert "secret" not in response.text


def test_mcp_tools_call_preserves_json_rpc_validation_error():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = (
        lambda: lambda: FakeMcpClient(json_rpc_error=True)
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"name": "get_cluster_nodes", "arguments": {"cluster": "bad"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == -32602
    assert detail["message"] == "Validation error calling get_cluster_nodes"
    assert detail["data"]["tool"] == "get_cluster_nodes"
    assert "expected_input_schema" in detail["data"]
    assert "cluster_name" in str(detail["data"])
    assert "secret" not in response.text


def test_mcp_tools_call_maps_proxied_tool_validation_result_to_422():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    app.dependency_overrides[get_mcp_client_provider] = lambda: lambda: FakeMcpClient()
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/tools/call",
        headers={"X-OpenSVC-AI-Session": session.session_id},
        json={"name": "get_cluster_nodes", "arguments": {"cluster": "bad"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "Invalid tool arguments"
    assert detail["tool"] == "get_cluster_nodes"
    assert "validation_errors" in detail
    assert "expected_input_schema" in detail
    assert "cluster_name" in str(detail["expected_input_schema"])
    assert "Retry with arguments" in detail["hint"]
    assert "secret" not in response.text
