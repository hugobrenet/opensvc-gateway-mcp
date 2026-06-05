from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from opensvc_gateway_mcp.core.sessions import GatewaySession


class FakeGatewaySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, GatewaySession] = {}

    async def create(self, *, username, password, ttl_seconds):
        session = GatewaySession(
            session_id=token_urlsafe(32),
            username=username,
            password=password,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        self.sessions[session.session_id] = session
        return session

    async def delete(self, session_id):
        return self.sessions.pop(session_id, None) is not None

    async def get(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= datetime.now(UTC):
            await self.delete(session_id)
            return None
        return session
