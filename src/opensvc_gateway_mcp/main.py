import uvicorn
from fastapi import FastAPI

from opensvc_gateway_mcp.api.router import api_router


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
    uvicorn.run(
        "opensvc_gateway_mcp.main:app",
        host="127.0.0.1",
        port=8010,
        reload=False,
    )
