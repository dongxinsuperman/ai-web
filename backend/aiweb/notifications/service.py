"""终态通知调度：Kafka 与 Webhook 各自 FIFO，不阻塞任务收口。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiweb.notifications.publisher import ResultPublisher, make_publisher
from aiweb.webhook.publisher import post_terminal

logger = logging.getLogger("aiweb.notifications")


class NotificationService:
    def __init__(self, publisher: ResultPublisher | None = None) -> None:
        self._publisher = publisher
        self._publisher_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._webhook_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._publisher_task: asyncio.Task | None = None
        self._webhook_task: asyncio.Task | None = None
        self._seen_item_ids: dict[str, set[str]] = {}
        self._pending_batch: dict[str, tuple[dict[str, Any], str | None]] = {}
        self._completed_submissions: set[str] = set()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._publisher = self._publisher or make_publisher()
        await self._publisher.start()
        self._publisher_task = asyncio.create_task(self._publisher_worker(), name="notification-kafka")
        self._webhook_task = asyncio.create_task(self._webhook_worker(), name="notification-webhook")
        self._started = True
        logger.info("终态通知已启动 backend=%s", self._publisher.name)

    def enqueue(self, event: dict[str, Any], *, callback_url: str | None = None) -> None:
        if not self._started:
            logger.error("终态通知未启动，消息未入队 event=%s", event.get("event"))
            return
        event_type = event.get("event")
        submission_id = str(event.get("submissionId") or "")
        if event_type == "submission.item.terminal" and submission_id:
            item_id = str(event.get("itemId") or "")
            if item_id:
                if submission_id in self._completed_submissions:
                    logger.warning("忽略已收口批次的重复 item 终态 item=%s", item_id)
                    return
                seen_ids = self._seen_item_ids.setdefault(submission_id, set())
                if item_id in seen_ids:
                    logger.warning("忽略重复的 item 终态 item=%s", item_id)
                    return
                seen_ids.add(item_id)
            self._enqueue_now(event, callback_url)
            self._release_batch_if_ready(submission_id)
            return
        if event_type == "submission.terminal" and submission_id:
            if submission_id in self._completed_submissions:
                logger.warning("忽略重复的 submission.terminal submission=%s", submission_id)
                return
            total_items = int(event.get("totalItems") or 0)
            seen = len(self._seen_item_ids.get(submission_id, set()))
            if total_items > seen:
                self._pending_batch[submission_id] = (dict(event), callback_url)
                logger.info(
                    "整批终态等待单条事件 submission=%s seen=%s total=%s",
                    submission_id,
                    seen,
                    total_items,
                )
                return
            self._enqueue_batch(submission_id, event, callback_url)
            return
        self._enqueue_now(event, callback_url)

    def _enqueue_now(self, event: dict[str, Any], callback_url: str | None) -> None:
        self._publisher_queue.put_nowait(dict(event))
        if callback_url:
            self._webhook_queue.put_nowait((callback_url, dict(event)))

    def _release_batch_if_ready(self, submission_id: str) -> None:
        pending = self._pending_batch.get(submission_id)
        if pending is None:
            return
        event, callback_url = pending
        total_items = int(event.get("totalItems") or 0)
        if len(self._seen_item_ids.get(submission_id, set())) < total_items:
            return
        self._pending_batch.pop(submission_id, None)
        self._enqueue_batch(submission_id, event, callback_url)

    def _enqueue_batch(self, submission_id: str, event: dict[str, Any], callback_url: str | None) -> None:
        self._enqueue_now(event, callback_url)
        self._completed_submissions.add(submission_id)
        self._seen_item_ids.pop(submission_id, None)

    async def _publisher_worker(self) -> None:
        while True:
            event = await self._publisher_queue.get()
            try:
                assert self._publisher is not None
                await self._publisher.publish_terminal(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # 通知副作用不得杀死 worker
                logger.exception("Kafka 终态通知异常（任务结果已保留）")
            finally:
                self._publisher_queue.task_done()

    async def _webhook_worker(self) -> None:
        while True:
            callback_url, event = await self._webhook_queue.get()
            try:
                await post_terminal(callback_url, event)
            except asyncio.CancelledError:
                raise
            except Exception:  # 通知副作用不得杀死 worker
                logger.exception("Webhook 终态通知异常（任务结果已保留）")
            finally:
                self._webhook_queue.task_done()

    async def stop(self) -> None:
        if not self._started:
            return
        for queue, name in ((self._publisher_queue, "Kafka"), (self._webhook_queue, "Webhook")):
            try:
                await asyncio.wait_for(queue.join(), timeout=2.0)
            except TimeoutError:
                logger.error("%s 通知队列未排空，剩余=%s", name, queue.qsize())
        for task in (self._publisher_task, self._webhook_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._publisher is not None:
            await self._publisher.close()
        self._publisher_task = None
        self._webhook_task = None
        self._seen_item_ids.clear()
        self._pending_batch.clear()
        self._completed_submissions.clear()
        self._started = False

    def status(self) -> dict[str, Any]:
        publisher_status = (
            self._publisher.status()
            if self._publisher is not None
            else {"backend": "not_started", "ready": False, "degraded": False}
        )
        return {
            **publisher_status,
            "pendingKafka": self._publisher_queue.qsize(),
            "pendingWebhook": self._webhook_queue.qsize(),
            "waitingBatchEvents": len(self._pending_batch),
        }


notifications = NotificationService()
