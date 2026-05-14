from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class CreateGatewaySessionRequest(BaseModel):
    username: str
    password: SecretStr
    ttl_seconds: int | None = Field(default=None, ge=1)


class GatewaySessionResponse(BaseModel):
    session_id: str
    username: str
    expires_at: datetime


class DeleteGatewaySessionResponse(BaseModel):
    deleted: bool
