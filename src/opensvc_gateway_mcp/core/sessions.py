import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Protocol

from pydantic import SecretStr


@dataclass(frozen=True)
class GatewaySession:
    session_id: str
    username: str
    password: SecretStr
    expires_at: datetime


class GatewaySessionStore(Protocol):
    async def create(
        self, *, username: str, password: SecretStr, ttl_seconds: int
    ) -> GatewaySession: ...

    async def delete(self, session_id: str) -> bool: ...

    async def get(self, session_id: str) -> GatewaySession | None: ...


class RedisGatewaySessionStore:
    def __init__(
        self,
        *,
        redis_url: str,
        key_prefix: str = "ai_gateway:session:",
        redis_client=None,
    ) -> None:
        self.key_prefix = key_prefix
        if redis_client is not None:
            self.redis = redis_client
            return

        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis session store requires the 'redis' Python package"
            ) from exc

        self.redis = Redis.from_url(redis_url, decode_responses=True)

    async def create(
        self, *, username: str, password: SecretStr, ttl_seconds: int
    ) -> GatewaySession:
        session = GatewaySession(
            session_id=token_urlsafe(32),
            username=username,
            password=password,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        await self.redis.setex(
            self._key(session.session_id),
            ttl_seconds,
            json.dumps(
                {
                    "session_id": session.session_id,
                    "username": session.username,
                    "password": session.password.get_secret_value(),
                    "expires_at": session.expires_at.isoformat(),
                }
            ),
        )
        return session

    async def delete(self, session_id: str) -> bool:
        return bool(await self.redis.delete(self._key(session_id)))

    async def get(self, session_id: str) -> GatewaySession | None:
        raw = await self.redis.get(self._key(session_id))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            expires_at = _parse_datetime(payload["expires_at"])
            session = GatewaySession(
                session_id=payload["session_id"],
                username=payload["username"],
                password=SecretStr(payload["password"]),
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self.delete(session_id)
            return None

        if session.expires_at <= datetime.now(UTC):
            await self.delete(session_id)
            return None
        return session

    def _key(self, session_id: str) -> str:
        return f"{self.key_prefix}{session_id}"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
