from typing import Any

from pydantic import BaseModel, Field


class SearchMcpToolsRequest(BaseModel):
    query: str = Field(min_length=1)


class CallMcpToolRequest(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
