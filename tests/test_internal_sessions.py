from datetime import UTC, datetime

from fastapi.testclient import TestClient

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client,
    get_gateway_session_store,
)
from opensvc_gateway_mcp.clients.collector import (
    CollectorPrincipal,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.config import Settings, get_settings
from opensvc_gateway_mcp.core.sessions import InMemoryGatewaySessionStore
from opensvc_gateway_mcp.main import create_app


class FakeCollectorClient:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.calls = []

    async def get_self(self, credentials):
        self.calls.append(credentials)
        if self.reject:
            raise InvalidCollectorCredentials
        return CollectorPrincipal(
            username=credentials.username,
            raw={"username": credentials.username},
        )


def test_internal_session_requires_gateway_token():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
    )
    client = TestClient(app)

    response = client.post(
        "/internal/v1/sessions",
        json={"username": "user-a", "password": "secret"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid gateway internal token"


def test_internal_session_validates_collector_credentials_and_stores_session():
    app = create_app()
    collector = FakeCollectorClient()
    store = InMemoryGatewaySessionStore()
    app.dependency_overrides[get_settings] = lambda: Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
        OPENSVC_GATEWAY_SESSION_TTL_SECONDS=60,
    )
    app.dependency_overrides[get_collector_client] = lambda: collector
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    client = TestClient(app)

    response = client.post(
        "/internal/v1/sessions",
        headers={"X-OpenSVC-Gateway-Token": "expected-token"},
        json={"username": "user-a", "password": "secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "user-a"
    assert payload["session_id"]
    assert payload["expires_at"]
    assert len(collector.calls) == 1
    assert collector.calls[0].username == "user-a"
    assert collector.calls[0].password == "secret"
    stored = store.get(payload["session_id"])
    assert stored is not None
    assert stored.username == "user-a"
    assert stored.password == "secret"


def test_internal_session_rejects_invalid_collector_credentials():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
    )
    app.dependency_overrides[get_collector_client] = (
        lambda: FakeCollectorClient(reject=True)
    )
    app.dependency_overrides[get_gateway_session_store] = (
        lambda: InMemoryGatewaySessionStore()
    )
    client = TestClient(app)

    response = client.post(
        "/internal/v1/sessions",
        headers={"X-OpenSVC-Gateway-Token": "expected-token"},
        json={"username": "user-a", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Collector credentials"


def test_internal_session_accepts_requested_ttl():
    app = create_app()
    collector = FakeCollectorClient()
    store = InMemoryGatewaySessionStore()
    app.dependency_overrides[get_settings] = lambda: Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
        OPENSVC_GATEWAY_SESSION_TTL_SECONDS=60,
    )
    app.dependency_overrides[get_collector_client] = lambda: collector
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    client = TestClient(app)

    response = client.post(
        "/internal/v1/sessions",
        headers={"X-OpenSVC-Gateway-Token": "expected-token"},
        json={"username": "user-a", "password": "secret", "ttl_seconds": 3600},
    )

    assert response.status_code == 200
    payload = response.json()
    stored = store.get(payload["session_id"])
    assert stored is not None
    remaining = (stored.expires_at - datetime.now(UTC)).total_seconds()
    assert remaining > 3500


def test_internal_session_delete_removes_session():
    app = create_app()
    store = InMemoryGatewaySessionStore()
    session = store.create(username="user-a", password="secret", ttl_seconds=60)
    app.dependency_overrides[get_settings] = lambda: Settings(
        OPENSVC_COLLECTOR_API_BASE_URL="https://collector.invalid/init/rest/api",
        OPENSVC_GATEWAY_INTERNAL_TOKEN="expected-token",
    )
    app.dependency_overrides[get_gateway_session_store] = lambda: store
    client = TestClient(app)

    response = client.delete(
        f"/internal/v1/sessions/{session.session_id}",
        headers={"X-OpenSVC-Gateway-Token": "expected-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert store.get(session.session_id) is None
