from pydantic import BaseModel


class AuthCheckResponse(BaseModel):
    authenticated: bool
    username: str
