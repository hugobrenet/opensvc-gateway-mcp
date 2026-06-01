import json

import httpx
from fastapi.security import HTTPBasicCredentials
from pydantic import ValidationError

from opensvc_gateway_mcp.clients.llm import (
    LlmClientError,
    LlmHttpError,
    UnsupportedLlmProvider,
)
from opensvc_gateway_mcp.clients.mcp import McpClientError
from opensvc_gateway_mcp.core.orchestrator import AiOrchestrationError, AiOrchestrator
from opensvc_gateway_mcp.schemas.ai import AiChatRequest


async def stream_ai_sse_events(
    orchestrator: AiOrchestrator,
    *,
    credentials: HTTPBasicCredentials,
    request: AiChatRequest,
):
    try:
        async for item in orchestrator.stream_chat(
            credentials=credentials,
            request=request,
        ):
            yield encode_sse(item.event, item.data)
    except ValidationError:
        yield encode_sse("error", {"error": "Collector AI configuration is invalid"})
    except httpx.HTTPError:
        yield encode_sse("error", {"error": "Collector AI configuration fetch failed"})
    except McpClientError:
        yield encode_sse("error", {"error": "Collector MCP orchestration failed"})
    except LlmHttpError as exc:
        detail: dict[str, object] = {
            "error": "LLM provider request failed",
            "status_code": exc.status_code,
        }
        if exc.detail is not None:
            detail["provider_error"] = exc.detail
        yield encode_sse("error", detail)
    except UnsupportedLlmProvider as exc:
        yield encode_sse(
            "error",
            {
                "error": "LLM provider is not supported by this gateway",
                "provider": exc.provider,
                "supported_providers": exc.supported_providers,
            },
        )
    except LlmClientError:
        yield encode_sse("error", {"error": "LLM provider request failed"})
    except AiOrchestrationError as exc:
        yield encode_sse("error", {"error": str(exc)})


def encode_sse(event: str, data: dict[str, object]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )
