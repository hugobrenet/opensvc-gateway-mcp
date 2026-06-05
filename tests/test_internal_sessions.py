import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr

from opensvc_gateway_mcp.api.dependencies import (
    get_collector_client,
    get_gateway_session_store,
)
from opensvc_gateway_mcp.clients.collector import (
    CollectorPrincipal,
    InvalidCollectorCredentials,
)
from opensvc_gateway_mcp.config import Settings, get_settings
from opensvc_gateway_mcp.core.sessions import RedisGatewaySessionStore
from opensvc_gateway_mcp.main import create_app
from tests.fakes import FakeGatewaySessionStore


def create_session(store, **kwargs):
    return asyncio.run(store.create(**kwargs))


def get_session(store,  session_id):
    return asyncio.run(store.get(session_id))


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


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}
        self.ttls = {}

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)


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
    store = FakeGatewaySessionStore()
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
    stored = get_session(store, payload["session_id"])
    assert stored is not None
    assert stored.username == "user-a"
    assert stored.password.get_secret_value() == "secret"
    assert "secret" not in repr(stored)


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
        lambda: FakeGatewaySessionStore()
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
    store = FakeGatewaySessionStore()
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
    stored = get_session(store, payload["session_id"])
    assert stored is not None
    remaining = (stored.expires_at - datetime.now(UTC)).total_seconds()
    assert remaining > 3500


def test_internal_session_delete_removes_session():
    app = create_app()
    store = FakeGatewaySessionStore()
    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )
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
    assert get_session(store, session.session_id) is None


def test_redis_gateway_session_store_round_trips_session_with_ttl():
    redis = FakeRedis()
    store = RedisGatewaySessionStore(
        redis_url="redis://redis.invalid/0",
        key_prefix="test:session:",
        redis_client=redis,
    )

    session = create_session(
        store,
        username="user-a",
        password=SecretStr("secret"),
        ttl_seconds=60,
    )

    assert redis.ttls[f"test:session:{session.session_id}"] == 60
    stored = get_session(store, session.session_id)
    assert stored is not None
    assert stored.username == "user-a"
    assert stored.password.get_secret_value() == "secret"
    assert "secret" not in repr(stored)


def test_redis_gateway_session_store_deletes_corrupt_payload():
    redis = FakeRedis()
    store = RedisGatewaySessionStore(
        redis_url="redis://redis.invalid/0",
        key_prefix="test:session:",
        redis_client=redis,
    )
    redis.values["test:session:bad"] = "{bad json"

    assert get_session(store, "bad") is None
    assert "test:session:bad" not in redis.values
