from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from opensvc_gateway_mcp.core.auth_service import CollectorAuthCredentialsError
from opensvc_gateway_mcp.core.mcp_proxy import (
    McpProxyClientError,
    McpProxyJsonRpcError,
    McpProxyToolValidationError,
)
from opensvc_gateway_mcp.core.session_service import GatewaySessionCredentialsError


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        CollectorAuthCredentialsError,
        _collector_auth_credentials_error,
    )
    app.add_exception_handler(
        GatewaySessionCredentialsError,
        _gateway_session_credentials_error,
    )
    app.add_exception_handler(McpProxyClientError, _mcp_proxy_client_error)
    app.add_exception_handler(McpProxyJsonRpcError, _mcp_proxy_json_rpc_error)
    app.add_exception_handler(
        McpProxyToolValidationError,
        _mcp_proxy_tool_validation_error,
    )


async def _collector_auth_credentials_error(
    _request: Request,
    _exc: CollectorAuthCredentialsError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid Collector credentials"},
        headers={"WWW-Authenticate": "Basic"},
    )


async def _gateway_session_credentials_error(
    _request: Request,
    _exc: GatewaySessionCredentialsError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Invalid Collector credentials"},
    )


async def _mcp_proxy_client_error(
    _request: Request,
    exc: McpProxyClientError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": exc.detail},
    )


async def _mcp_proxy_json_rpc_error(
    _request: Request,
    exc: McpProxyJsonRpcError,
) -> JSONResponse:
    status_code = (
        status.HTTP_502_BAD_GATEWAY
        if exc.operation == "list_tools"
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return JSONResponse(
        status_code=status_code,
        content={"detail": _mcp_json_rpc_error_detail(exc)},
    )


async def _mcp_proxy_tool_validation_error(
    _request: Request,
    exc: McpProxyToolValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": exc.payload},
    )


def _mcp_json_rpc_error_detail(exc: McpProxyJsonRpcError) -> dict[str, Any]:
    detail = {
        "message": exc.message,
        "code": exc.code,
    }
    if exc.data is not None:
        detail["data"] = exc.data
    return detail
