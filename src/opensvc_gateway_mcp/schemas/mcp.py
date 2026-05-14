from typing import Any

from pydantic import BaseModel, Field


class SearchMcpToolsRequest(BaseModel):
    query: str = Field(min_length=1)


class McpToolCallResult(BaseModel):
    result: dict[str, Any]
