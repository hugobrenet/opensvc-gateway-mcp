import uvicorn
from fastapi import FastAPI

from opensvc_gateway_mcp.api.router import api_router
from opensvc_gateway_mcp.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenSVC MCP Gateway",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.include_router(api_router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "opensvc_gateway_mcp.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        reload=False,
    )
