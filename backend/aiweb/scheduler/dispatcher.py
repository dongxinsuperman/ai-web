"""常驻调度器：按引擎槽位领取（每引擎独立台数）+ DB 行锁（多 Pod 安全）。"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select, text

from aiweb.db import session_scope
from aiweb.models.item import ITEM_RUNNING, Item
from aiweb.scheduler.worker import run_item
from aiweb.slots import canon, get_slots
from aiweb.settings import get_settings

logger = logging.getLogger("aiweb.dispatcher")

# 原子领取一条指定引擎的 queued 任务（并发安全）
_CLAIM_SQL = text(
    """
    UPDATE t_aiweb_item
    SET state='running', attempts=attempts+1, updated_at=now()
    WHERE id = (
        SELECT id FROM t_aiweb_item
        WHERE state='queued' AND platform = :plat
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


class Dispatcher:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    async def _running_counts(self) -> dict[str, int]:
        """各引擎执行中数量（DB 权威，多 Pod 共享）。"""
        async with session_scope() as s:
            rows = (await s.execute(
                select(Item.platform, func.count())
                .where(Item.state == ITEM_RUNNING)
                .group_by(Item.platform)
            )).all()
        out: dict[str, int] = {}
        for plat, c in rows:
            eng = canon(plat)
            out[eng] = out.get(eng, 0) + int(c)
        return out

    async def _claim_one(self, plat: str) -> str | None:
        async with session_scope() as s:
            row = (await s.execute(_CLAIM_SQL, {"plat": plat})).first()
            return row[0] if row else None

    def _spawn(self, item_id: str) -> None:
        task = asyncio.create_task(self._guarded_run(item_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guarded_run(self, item_id: str) -> None:
        try:
            await run_item(item_id)
        except Exception:
            logger.exception("run_item 顶层异常 item=%s", item_id)

    async def _loop(self) -> None:
        poll = self.settings.poll_interval_ms / 1000.0
        while not self._stop.is_set():
            try:
                async with session_scope() as s:
                    slots = await get_slots(s)  # {引擎: 台数}
                running = await self._running_counts()
                for eng, cap in slots.items():
                    cur = running.get(eng, 0)
                    while cur < cap:
                        item_id = await self._claim_one(eng)
                        if not item_id:
                            break
                        self._spawn(item_id)
                        cur += 1
            except Exception:
                logger.exception("dispatcher loop 异常")
            await asyncio.sleep(poll)

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("dispatcher 启动")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self._tasks):
            t.cancel()


dispatcher = Dispatcher()
