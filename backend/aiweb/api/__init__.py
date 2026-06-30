"""API 路由聚合 + 鉴权依赖。"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from aiweb.settings import get_settings


async def auth_guard(authorization: str | None = Header(default=None)) -> None:
    """若配置了 api_token，则要求 Authorization: Bearer <token>；否则匿名放行。"""
    token = get_settings().api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="未授权")


# 注意：路由模块在装饰阶段会引用 auth_guard，必须在其定义之后再导入。
from aiweb.api import assets, browser_agents, config, devices, queue, sites, submissions  # noqa: E402

api_router = APIRouter(prefix="/api")
api_router.include_router(queue.router)
api_router.include_router(devices.router)
api_router.include_router(submissions.router)
api_router.include_router(assets.router)
api_router.include_router(config.router)
api_router.include_router(sites.router)
api_router.include_router(browser_agents.router)
