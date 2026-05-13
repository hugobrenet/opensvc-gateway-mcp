from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe


@dataclass(frozen=True)
class GatewaySession:
    session_id: str
    username: str
    password: str
    expires_at: datetime


class InMemoryGatewaySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, GatewaySession] = {}

    def create(self, *, username: str, password: str, ttl_seconds: int) -> GatewaySession:
        self.cleanup_expired()
        session = GatewaySession(
            session_id=token_urlsafe(32),
            username=username,
            password=password,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        self._sessions[session.session_id] = session
        return session

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def get(self, session_id: str) -> GatewaySession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= datetime.now(UTC):
            self.delete(session_id)
            return None
        return session

    def cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self.delete(session_id)
