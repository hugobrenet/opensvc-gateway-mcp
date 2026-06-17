import asyncio
import copy
import json
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi.security import HTTPBasicCredentials

from opensvc_gateway_mcp.clients.collector import CollectorClient
from opensvc_gateway_mcp.clients.llm import LlmProviderClient
from opensvc_gateway_mcp.clients.mcp import McpClient
from opensvc_gateway_mcp.schemas.ai import (
    AiChatRequest,
    AiChatResponse,
    AiStreamEvent,
    AiToolCallSummary,
    LlmProfile,
)


LLM_VISIBLE_MCP_TOOLS = {"search_tools", "call_tool"}
_MCP_LIST_TOOLS_CACHE = None


class AiOrchestrationError(Exception):
    """The gateway could not complete an AI orchestration turn."""


class _McpListToolsCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        *,
        key: str,
        ttl_seconds: float,
        fetch: Callable[[], Any],
    ) -> tuple[dict[str, Any], bool]:
        now = time.monotonic()
        async with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                expires_at, value = cached
                if expires_at > now:
                    return copy.deepcopy(value), True
                self._values.pop(key, None)

        value = await fetch()
        async with self._lock:
            self._values[key] = (time.monotonic() + ttl_seconds, copy.deepcopy(value))
        return value, False

    async def clear(self) -> None:
        async with self._lock:
            self._values.clear()


async def _list_mcp_tools(
    mcp: McpClient,
    credentials: HTTPBasicCredentials,
    *,
    request_id: str,
    cache_ttl_seconds: float,
) -> tuple[dict[str, Any], bool]:
    cache_key = getattr(mcp, "list_tools_cache_key", None)
    if not isinstance(cache_key, str) or not cache_key.strip() or cache_ttl_seconds <= 0:
        return await mcp.list_tools(credentials, request_id=request_id), False

    global _MCP_LIST_TOOLS_CACHE
    if _MCP_LIST_TOOLS_CACHE is None:
        _MCP_LIST_TOOLS_CACHE = _McpListToolsCache()

    async def fetch() -> dict[str, Any]:
        return await mcp.list_tools(credentials, request_id=request_id)

    return await _MCP_LIST_TOOLS_CACHE.get_or_fetch(
        key=cache_key.strip(),
        ttl_seconds=cache_ttl_seconds,
        fetch=fetch,
    )


async def clear_mcp_list_tools_cache() -> None:
    if _MCP_LIST_TOOLS_CACHE is not None:
        await _MCP_LIST_TOOLS_CACHE.clear()


def _mcp_list_tools_cache_ttl_seconds(mcp: McpClient, *, default: float) -> float:
    ttl = getattr(mcp, "list_tools_cache_ttl_seconds", default)
    if isinstance(ttl, (int, float)):
        return float(ttl)
    return default


class AiOrchestrator:
    def __init__(
        self,
        *,
        collector: CollectorClient,
        mcp_client_provider: Callable[[], McpClient],
        llm: LlmProviderClient,
        mcp_list_tools_cache_ttl_seconds: float = 1800.0,
    ) -> None:
        self.collector = collector
        self.mcp_client_provider = mcp_client_provider
        self.llm = llm
        self.mcp_list_tools_cache_ttl_seconds = mcp_list_tools_cache_ttl_seconds

    async def stream_chat(
        self,
        *,
        credentials: HTTPBasicCredentials,
        request: AiChatRequest,
    ):
        request_id = _new_ai_request_id()
        profile = await self.collector.get_ai_config(credentials)
        mcp = self.mcp_client_provider()
        tools_result, _list_tools_cache_hit = await _list_mcp_tools(
            mcp,
            credentials,
            request_id=request_id,
            cache_ttl_seconds=_mcp_list_tools_cache_ttl_seconds(
                mcp,
                default=self.mcp_list_tools_cache_ttl_seconds,
            ),
        )
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
            assistant_message = None
            async for chunk in self.llm.stream_chat(
                profile=profile,
                messages=messages,
                tools=tools if iteration < max_iterations else None,
            ):
                if chunk.delta:
                    yield AiStreamEvent(
                        event="delta",
                        data={"content": chunk.delta},
                    )
                if chunk.message is not None:
                    assistant_message = chunk.message

            if assistant_message is None:
                raise AiOrchestrationError("LLM stream ended without a message")

            message: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content or None,
            }
            if assistant_message.raw_tool_calls:
                message["tool_calls"] = assistant_message.raw_tool_calls
            messages.append(message)

            if not assistant_message.tool_calls:
                yield AiStreamEvent(
                    event="done",
                    data=AiChatResponse(
                        message=assistant_message.content,
                        provider=profile.provider,
                        model=profile.model,
                        tool_calls=tool_summaries,
                    ).model_dump(),
                )
                return

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
                    blocked_result = _blocked_confirmation_result(
                        latest_user_message=request.message,
                        proxy_tool_name=tool_call.name,
                        proxy_arguments=tool_call.arguments,
                    )
                    if blocked_result is not None:
                        tool_result = blocked_result
                        ok = False
                    else:
                        result = await mcp.call_tool(
                            credentials,
                            name=tool_call.name,
                            arguments=tool_call.arguments,
                            request_id=request_id,
                        )
                        tool_result = result
                        ok = not bool(result.get("isError"))

                summary = AiToolCallSummary(
                    name=_tool_call_summary_name(tool_call.name, tool_call.arguments),
                    ok=ok,
                )
                tool_summaries.append(summary)
                yield AiStreamEvent(
                    event="tool_call",
                    data=summary.model_dump(),
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


def _new_ai_request_id() -> str:
    return f"ai_{uuid4().hex}"


def _tool_call_summary_name(name: str, arguments: dict[str, Any]) -> str:
    if name != "call_tool":
        return name

    target_name = arguments.get("name")
    if isinstance(target_name, str) and target_name.strip():
        return target_name.strip()
    return name


def _blocked_confirmation_result(
    *,
    latest_user_message: str,
    proxy_tool_name: str,
    proxy_arguments: dict[str, Any],
) -> dict[str, Any] | None:
    if proxy_tool_name != "call_tool":
        return None

    target_arguments = proxy_arguments.get("arguments")
    if not isinstance(target_arguments, dict):
        return None

    request_payload = target_arguments.get("request")
    if not isinstance(request_payload, dict):
        return None

    confirmation = request_payload.get("confirmation")
    if confirmation is None:
        return None
    if not isinstance(confirmation, dict):
        return _confirmation_error(
            "request.confirmation must be an object containing phrase"
        )

    phrase = confirmation.get("phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        return _confirmation_error(
            "request.confirmation.phrase must be a non-empty string"
        )

    phrase = phrase.strip()
    if phrase not in latest_user_message:
        return _confirmation_error(
            "request.confirmation.phrase must appear verbatim in the latest "
            "user message before the gateway forwards this state-changing tool"
        )

    return None


def _confirmation_error(message: str) -> dict[str, Any]:
    return {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": "state_changing_tool_confirmation_required",
                        "message": message,
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    }


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
