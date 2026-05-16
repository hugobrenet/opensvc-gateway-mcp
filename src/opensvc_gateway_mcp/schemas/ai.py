from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class AiChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class AiChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[AiChatMessage] = Field(default_factory=list)
    max_tool_iterations: int | None = Field(default=None, ge=0, le=20)


class LlmProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: SecretStr | None = None
    system_prompt: str = ""
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    completion_token_parameter: Literal["max_completion_tokens", "max_tokens"] = (
        "max_completion_tokens"
    )
    max_tool_iterations: int = Field(default=5, ge=0, le=20)
    tool_result_max_chars: int = Field(default=20000, ge=1000, le=200000)

    @model_validator(mode="before")
    @classmethod
    def accept_prompt_alias(cls, value: Any) -> Any:
        if isinstance(value, dict) and "system_prompt" not in value:
            prompt = value.get("prompt")
            if isinstance(prompt, str):
                value = {**value, "system_prompt": prompt}
        return value


class AiToolCallSummary(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool


class AiChatResponse(BaseModel):
    message: str
    provider: str
    model: str
    tool_calls: list[AiToolCallSummary] = Field(default_factory=list)
