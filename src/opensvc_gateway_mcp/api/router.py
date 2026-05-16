from fastapi import APIRouter

from opensvc_gateway_mcp.api.routes.ai import router as ai_router
from opensvc_gateway_mcp.api.routes.auth import router as auth_router
from opensvc_gateway_mcp.api.routes.health import router as health_router
from opensvc_gateway_mcp.api.routes.internal_sessions import router as internal_sessions_router
from opensvc_gateway_mcp.api.routes.mcp import router as mcp_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(internal_sessions_router)
api_router.include_router(mcp_router)
api_router.include_router(ai_router)
