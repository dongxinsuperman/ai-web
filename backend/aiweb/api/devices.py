"""浏览器（设备）就绪端点 —— 与 ai-phone 同形，便于工作台统一接入。

AI Web 没有真实设备：按"每个引擎配几台"投影成浏览器槽（chrome-1..N、firefox-1..M ...），
按该引擎执行中数量标忙/闲。同引擎内各槽等价；跨引擎是真实不同的浏览器。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from aiweb.db import session_scope
from aiweb.models.item import ITEM_RUNNING, Item
from aiweb.settings import get_settings
from aiweb.slots import META, canon, get_slots

router = APIRouter(tags=["devices"])


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


async def _slots() -> list[dict]:
    settings = get_settings()
    w, h = settings.viewport_size
    async with session_scope() as s:
        slots = await get_slots(s)  # {引擎: 台数}
        rows = (await s.execute(
            select(Item.platform, func.count())
            .where(Item.state == ITEM_RUNNING)
            .group_by(Item.platform)
        )).all()
    running: dict[str, int] = {}
    for plat, c in rows:
        eng = canon(plat)
        running[eng] = running.get(eng, 0) + int(c)

    out = []
    for eng, cap in slots.items():
        label, brand = META.get(eng, (eng, eng))
        run_n = running.get(eng, 0)
        for i in range(1, cap + 1):
            busy = i <= run_n  # 该引擎执行中的占满前 run_n 个
            out.append({
                "serial": f"{eng}-{i}",
                "alias": f"{label} #{i}",
                "platform": eng,
                "brand": brand,
                "model": brand,
                "osVersion": "",
                "screenWidth": w,
                "screenHeight": h,
                "status": "online",
                "effectiveStatus": "busy" if busy else "idle",
                "lock": {"holderType": "auto"} if busy else None,
            })
    return out


@router.get("/devices/statuses", dependencies=[_guard()])
async def devices_statuses():
    """全量浏览器槽（含忙/闲）。"""
    return await _slots()


@router.get("/devices/available", dependencies=[_guard()])
async def devices_available():
    """仅空闲（可接单）浏览器槽，字段精简。"""
    slots = await _slots()
    idle = [s for s in slots if s["effectiveStatus"] == "idle"]
    return {"devices": [
        {"serial": s["serial"], "alias": s["alias"], "platform": s["platform"],
         "screenWidth": s["screenWidth"], "screenHeight": s["screenHeight"]}
        for s in idle
    ]}
