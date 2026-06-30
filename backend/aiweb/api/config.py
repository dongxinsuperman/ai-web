"""平台配置：热调浏览器槽位 / 有头无头。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select

from aiweb.db import session_scope
from aiweb.models.config import ConfigKV
from aiweb.settings import get_settings
from aiweb.slots import default_node_slots, normalize_slots_value

router = APIRouter(tags=["config"])

# 仅这两项允许热调（缓存/分段阈值是内核写死逻辑，不对外暴露）。
_ALLOWED_KEYS = {"browser_slots", "headless"}


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


def _normalize_slots(value) -> str:
    """前端可传旧对象 {chrome:2,...} 或新对象 {local:{chrome:2},...}。"""
    return normalize_slots_value(value)


@router.get("/config", dependencies=[_guard()])
async def get_config():
    settings = get_settings()
    result = {
        "browser_slots": normalize_slots_value(default_node_slots()),
        "headless": str(settings.headless).lower(),
    }
    async with session_scope() as s:
        rows = (await s.execute(select(ConfigKV))).scalars().all()
        for r in rows:
            if r.key not in _ALLOWED_KEYS:
                continue
            result[r.key] = r.value
    # browser_slots 统一规范化为 JSON，方便前端解析
    result["browser_slots"] = _normalize_slots(result["browser_slots"])
    return result


@router.put("/config", dependencies=[_guard()])
async def update_config(payload: dict = Body(...)):
    async with session_scope() as s:
        for key, value in payload.items():
            if key not in _ALLOWED_KEYS:
                continue
            stored = _normalize_slots(value) if key == "browser_slots" else str(value)
            cfg = await s.get(ConfigKV, key)
            if cfg:
                cfg.value = stored
            else:
                s.add(ConfigKV(key=key, value=stored))
    return await get_config()
