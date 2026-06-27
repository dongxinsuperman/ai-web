"""心跳回收：把心跳超时的 running 任务重排，防 Pod 崩溃后卡死。"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from aiweb.db import session_scope
from aiweb.models.base import utcnow
from aiweb.models.item import ITEM_FAILED, ITEM_QUEUED, ITEM_RUNNING, Item
from aiweb.models.run import RUN_FAILED, RUN_RUNNING, Run
from aiweb.settings import get_settings

logger = logging.getLogger("aiweb.reaper")


class Reaper:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def recover_on_startup(self) -> None:
        """启动时回收本 Pod 名下残留 running（进程重启场景）。"""
        pod = self.settings.pod_id
        async with session_scope() as s:
            runs = (await s.execute(
                select(Run).where(Run.state == RUN_RUNNING, Run.claimed_by == pod)
            )).scalars().all()
            for run in runs:
                run.state = RUN_FAILED
                run.fail_reason = "pod 重启回收"
                run.finished_at = utcnow()
                item = await s.get(Item, run.item_id)
                if item and item.state == ITEM_RUNNING:
                    item.state = ITEM_QUEUED if item.attempts <= item.retry_max else ITEM_FAILED
                    item.status_reason = "recovered"

    async def _sweep(self) -> None:
        ttl = self.settings.run_heartbeat_ttl_sec
        deadline = utcnow() - timedelta(seconds=ttl)
        async with session_scope() as s:
            runs = (await s.execute(
                select(Run).where(Run.state == RUN_RUNNING, Run.heartbeat_at < deadline)
            )).scalars().all()
            for run in runs:
                run.state = RUN_FAILED
                run.fail_reason = "心跳超时回收"
                run.finished_at = utcnow()
                item = await s.get(Item, run.item_id)
                if item and item.state == ITEM_RUNNING:
                    item.state = ITEM_QUEUED if item.attempts <= item.retry_max else ITEM_FAILED
                    item.status_reason = "heartbeat_timeout"
                    logger.warning("回收超时任务 item=%s", item.id)

    async def _loop(self) -> None:
        interval = max(30, self.settings.run_heartbeat_ttl_sec // 2)
        while not self._stop.is_set():
            try:
                await self._sweep()
            except Exception:
                logger.exception("reaper sweep 异常")
            await asyncio.sleep(interval)

    async def start(self) -> None:
        await self.recover_on_startup()
        self._task = asyncio.create_task(self._loop())
        logger.info("reaper 启动")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()


reaper = Reaper()
