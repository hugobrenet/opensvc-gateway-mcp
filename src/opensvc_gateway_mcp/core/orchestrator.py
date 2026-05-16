import json
from collections.abc import Callable
from typing import Any

from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import OpenAICompatibleLlmClient
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.schemas.ai import (
    AiChatRequest,
    AiChatResponse,
    AiToolCallSummary,
    LlmProfile,
)


LLM_VISIBLE_MCP_TOOLS = {"search_tools", "call_tool"}


class AiOrchestrationError(Exception):
    """The gateway could not complete an AI orchestration turn."""


class AiOrchestrator:
    def __init__(
        self,
        *,
        collector: CollectorClient,
        mcp_client_provider: Callable[[], McpClient],
        llm: OpenAICompatibleLlmClient,
    ) -> None:
        self.collector = collector
        self.mcp_client_provider = mcp_client_provider
        self.llm = llm

    async def chat(
        self,
        *,
        credentials: HTTPBasicCredentials,
        request: AiChatRequest,
    ) -> AiChatResponse:
        profile = await self.collector.get_ai_config(credentials)
        mcp = self.mcp_client_provider()
        tools_result = await mcp.list_tools(credentials)
        tools = _mcp_tools_to_openai_tools(
            tools_result,
            allowed_names=LLM_VISIBLE_MCP_TOOLS,
        )
        allowed_tool_names = {
            tool["function"]["name"]
            for tool in tools
            if isinstance(tool.get("function"), dict)
        }
        missing_tool_names = LLM_VISIBLE_MCP_TOOLS - allowed_tool_names
        if missing_tool_names:
            raise AiOrchestrationError(
                "MCP did not expose required proxy tools: "
                + ", ".join(sorted(missing_tool_names))
            )
        messages = _initial_messages(profile, request)
        max_iterations = (
            request.max_tool_iterations
            if request.max_tool_iterations is not None
            else profile.max_tool_iterations
        )
        tool_summaries: list[AiToolCallSummary] = []

        for iteration in range(max_iterations + 1):
            completion = await self.llm.chat(
                profile=profile,
                messages=messages,
                tools=tools if iteration < max_iterations else None,
            )
            assistant_message = completion.message
            message: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content or None,
            }
            if assistant_message.raw_tool_calls:
                message["tool_calls"] = assistant_message.raw_tool_calls
            messages.append(message)

            if not assistant_message.tool_calls:
                return AiChatResponse(
                    message=assistant_message.content,
                    provider=profile.provider,
                    model=profile.model,
                    tool_calls=tool_summaries,
                )

            if iteration >= max_iterations:
                raise AiOrchestrationError("LLM exceeded the configured tool call limit")

            for tool_call in assistant_message.tool_calls:
                if tool_call.name not in allowed_tool_names:
                    tool_result: dict[str, Any] = {
                        "error": "Unknown tool requested by LLM",
                        "tool": tool_call.name,
                    }
                    ok = False
                else:
                    result = await mcp.call_tool(
                        credentials,
                        name=tool_call.name,
                        arguments=tool_call.arguments,
                    )
                    tool_result = result
                    ok = not bool(result.get("isError"))

                tool_summaries.append(
                    AiToolCallSummary(
                        name=tool_call.name,
                        arguments=tool_call.arguments,
                        ok=ok,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": _serialize_tool_result(
                            tool_result,
                            max_chars=profile.tool_result_max_chars,
                        ),
                    }
                )

        raise AiOrchestrationError("LLM orchestration ended without a final message")


def _initial_messages(
    profile: LlmProfile,
    request: AiChatRequest,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if profile.system_prompt:
        messages.append({"role": "system", "content": profile.system_prompt})
    for message in request.history:
        messages.append({"role": message.role, "content": message.content})
    messages.append({"role": "user", "content": request.message})
    return messages


def _mcp_tools_to_openai_tools(
    tools_result: dict[str, Any],
    *,
    allowed_names: set[str],
) -> list[dict[str, Any]]:
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise AiOrchestrationError("MCP tools/list response did not contain tools")

    openai_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name not in allowed_names:
            continue
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _llm_proxy_tool_description(name),
                    "parameters": _llm_proxy_tool_schema(name),
                },
            }
        )
    return openai_tools


def _llm_proxy_tool_description(name: str) -> str:
    if name == "search_tools":
        return (
            "Search the OpenSVC Collector MCP tool catalog using concise English "
            "keywords. Use this before call_tool."
        )
    if name == "call_tool":
        return (
            "Call one OpenSVC Collector MCP tool selected from search_tools "
            "results. Always put the selected target tool input inside this "
            "proxy tool's arguments object. Correct call_tool payload: "
            '{"name":"get_node","arguments":{"request":{"nodename":"node1"}}}. '
            "Incorrect: "
            '{"name":"get_node","request":{"nodename":"node1"}}.'
        )
    return ""


def _llm_proxy_tool_schema(name: str) -> dict[str, Any]:
    if name == "search_tools":
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Concise English keywords describing the OpenSVC "
                        "Collector information needed."
                    ),
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    if name == "call_tool":
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the Collector MCP tool selected from search_tools results.",
                },
                "arguments": {
                    "type": "object",
                    "description": (
                        "Required target tool input object. Put the full input "
                        "schema required by the selected tool here. Do not put "
                        "target tool fields at the top level of call_tool. "
                        "For a target schema requiring request.nodename, use "
                        '{"request":{"nodename":"node1"}} as arguments.'
                    ),
                },
            },
            "required": ["name", "arguments"],
            "additionalProperties": False,
        }
    return {"type": "object", "properties": {}}


def _serialize_tool_result(result: dict[str, Any], *, max_chars: int) -> str:
    content = json.dumps(result, ensure_ascii=False, default=str)
    if len(content) <= max_chars:
        return content
    return content[: max_chars - 32] + "...[truncated tool result]"
