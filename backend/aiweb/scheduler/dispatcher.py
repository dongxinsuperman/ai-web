"""常驻调度器：按引擎槽位领取（每引擎独立台数）+ DB 行锁（多 Pod 安全）。"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select, text

from aiweb.agent_hub import agent_hub
from aiweb.db import session_scope
from aiweb.models.item import Item
from aiweb.models.run import RUN_RUNNING, Run
from aiweb.scheduler.worker import create_run_for_item, finalize_run, _failed_result
from aiweb.slots import canon, get_node_slots
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
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

    async def _running_counts(self) -> dict[tuple[str, str], int]:
        """各节点/引擎执行中数量（DB 权威，多 Pod 共享）。"""
        async with session_scope() as s:
            rows = (await s.execute(
                select(Run.claimed_by, Item.platform, func.count())
                .join(Item, Run.item_id == Item.id)
                .where(Run.state == RUN_RUNNING)
                .group_by(Run.claimed_by, Item.platform)
            )).all()
        out: dict[tuple[str, str], int] = {}
        for claimed_by, plat, c in rows:
            if not claimed_by:
                continue
            node = claimed_by
            eng = canon(plat)
            out[(node, eng)] = out.get((node, eng), 0) + int(c)
        return out

    async def _claim_one(self, plat: str) -> str | None:
        async with session_scope() as s:
            row = (await s.execute(_CLAIM_SQL, {"plat": plat})).first()
            return row[0] if row else None

    async def _dispatch_agent(self, item_id: str, agent_id: str) -> None:
        run_payload = await create_run_for_item(item_id, claimed_by=agent_id)
        if not run_payload:
            return
        try:
            await agent_hub.send_start_run(agent_id, run_payload)
        except Exception as exc:
            logger.warning("派发 Agent 失败 agent=%s item=%s: %s", agent_id, item_id, exc)
            await finalize_run(
                run_payload["runId"],
                _failed_result(0, f"dispatch_failed: {exc}"),
                elapsed_ms=0,
            )

    async def _loop(self) -> None:
        poll = self.settings.poll_interval_ms / 1000.0
        while not self._stop.is_set():
            try:
                async with session_scope() as s:
                    node_slots = await get_node_slots(s)  # {节点: {引擎: 台数}}
                running = await self._running_counts()
                for node_id, slots in node_slots.items():
                    if not agent_hub.online(node_id):
                        continue
                    for eng, cap in slots.items():
                        cur = running.get((node_id, eng), 0)
                        while cur < cap:
                            item_id = await self._claim_one(eng)
                            if not item_id:
                                break
                            await self._dispatch_agent(item_id, node_id)
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


dispatcher = Dispatcher()
