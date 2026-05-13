from fastapi.testclient import TestClient

from opensvc_gateway_mcp.api.dependencies import get_collector_client
from opensvc_gateway_mcp.clients.collector import (
    CollectorPrincipal,
    InvalidCollectorCredentials,
)
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


def test_auth_check_requires_basic_auth():
    client = TestClient(create_app())

    response = client.get("/api/v1/auth/check")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert response.json()["detail"] == "Missing Basic Auth credentials"


def test_auth_check_validates_credentials_against_collector():
    app = create_app()
    collector = FakeCollectorClient()
    app.dependency_overrides[get_collector_client] = lambda: collector
    client = TestClient(app)

    response = client.get("/api/v1/auth/check", auth=("user-a", "secret"))

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "username": "user-a"}
    assert len(collector.calls) == 1
    assert collector.calls[0].username == "user-a"
    assert collector.calls[0].password == "secret"


def test_auth_check_rejects_invalid_collector_credentials():
    app = create_app()
    app.dependency_overrides[get_collector_client] = (
        lambda: FakeCollectorClient(reject=True)
    )
    client = TestClient(app)

    response = client.get("/api/v1/auth/check", auth=("user-a", "wrong"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert response.json()["detail"] == "Invalid Collector credentials"
