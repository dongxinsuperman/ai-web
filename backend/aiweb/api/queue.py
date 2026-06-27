"""队列快照：排队中 / 执行中 / 最近完成，给前端看板用。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from aiweb.db import session_scope
from aiweb.models.base import utcnow
from aiweb.models.item import (ITEM_CANCELLED, ITEM_FAILED, ITEM_QUEUED, ITEM_SUCCESS, Item)
from aiweb.models.run import RUN_RUNNING, Run
from aiweb.slots import get_slots

router = APIRouter(tags=["queue"])


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


def _item_brief(it: Item) -> dict:
    return {
        "itemId": it.id,
        "caseId": it.case_id,
        "caseName": it.case_name,
        "runContent": it.run_content,
        "submissionId": it.submission_id,
        "state": it.state,
        "statusReason": it.status_reason,
        "attempts": it.attempts,
        "reportUrl": it.report_url,
    }


@router.get("/queue", dependencies=[_guard()])
async def queue_snapshot(recent: int = Query(default=20, le=100)):
    async with session_scope() as s:
        slots = await get_slots(s)  # {引擎: 台数}
        concurrency = sum(slots.values())  # 总并发=各引擎台数之和

        queued = (await s.execute(
            select(Item).where(Item.state == ITEM_QUEUED).order_by(Item.created_at)
        )).scalars().all()

        runs = (await s.execute(
            select(Run).where(Run.state == RUN_RUNNING).order_by(Run.started_at)
        )).scalars().all()
        running = []
        now = utcnow()
        for r in runs:
            it = await s.get(Item, r.item_id)
            if not it:
                continue
            elapsed_ms = int((now - r.started_at).total_seconds() * 1000) if r.started_at else None
            brief = _item_brief(it)
            brief.update({"runId": r.id, "claimedBy": r.claimed_by, "elapsedMs": elapsed_ms, "steps": r.steps})
            running.append(brief)

        recent_items = (await s.execute(
            select(Item)
            .where(Item.state.in_([ITEM_SUCCESS, ITEM_FAILED, ITEM_CANCELLED]))
            .order_by(Item.updated_at.desc())
            .limit(recent)
        )).scalars().all()

        return {
            "concurrency": concurrency,
            "slots": slots,
            "queued": [_item_brief(it) for it in queued],
            "running": running,
            "recent": [_item_brief(it) for it in recent_items],
        }
