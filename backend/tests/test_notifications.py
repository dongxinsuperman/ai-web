from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiweb.notifications.publisher import KafkaPublisher, ResultPublisher, make_publisher
from aiweb.notifications.service import NotificationService
from aiweb.settings import Settings


def _event_models(*, state: str, status_reason: str, fail_reason: str | None):
    now = datetime.now(timezone.utc)
    submission = SimpleNamespace(id="s1", name="web smoke")
    item = SimpleNamespace(
        id="i1",
        submission_id="s1",
        case_id="c1",
        case_name="login",
        platform="chrome",
        run_content="打开登录页并验证个人中心",
        state=state,
        status_reason=status_reason,
        retry_max=1,
        attempts=1,
        created_at=now,
        report_url="https://example.test/report.html",
    )
    run = SimpleNamespace(
        id="r1",
        fail_reason=fail_reason,
        started_at=now,
        finished_at=now,
        elapsed_ms=10,
        steps=2,
        token_usage={"total_tokens": 100},
    )
    return submission, item, run


class _CollectingPublisher(ResultPublisher):
    name = "collecting"

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_terminal(self, event: dict) -> None:
        self.events.append(dict(event))


class _BlockingPublisher(ResultPublisher):
    name = "blocking"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def publish_terminal(self, event: dict) -> None:
        self.started.set()
        await self.release.wait()


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_publisher_fifo(self) -> None:
        publisher = _CollectingPublisher()
        service = NotificationService(publisher)
        await service.start()
        try:
            service.enqueue({"event": "submission.item.terminal", "submissionId": "s1"})
            service.enqueue({"event": "submission.terminal", "submissionId": "s1"})
            await asyncio.wait_for(service._publisher_queue.join(), timeout=1)
            self.assertEqual(
                [event["event"] for event in publisher.events],
                ["submission.item.terminal", "submission.terminal"],
            )
        finally:
            await service.stop()

    async def test_batch_waits_until_all_item_events_are_enqueued(self) -> None:
        publisher = _CollectingPublisher()
        service = NotificationService(publisher)
        await service.start()
        try:
            service.enqueue({
                "event": "submission.item.terminal",
                "submissionId": "s1",
                "itemId": "i1",
            })
            service.enqueue({
                "event": "submission.terminal",
                "submissionId": "s1",
                "totalItems": 2,
            })
            await asyncio.wait_for(service._publisher_queue.join(), timeout=1)
            self.assertEqual([event["event"] for event in publisher.events], ["submission.item.terminal"])
            self.assertEqual(service.status()["waitingBatchEvents"], 1)

            service.enqueue({
                "event": "submission.item.terminal",
                "submissionId": "s1",
                "itemId": "i2",
            })
            await asyncio.wait_for(service._publisher_queue.join(), timeout=1)
            self.assertEqual(
                [event["event"] for event in publisher.events],
                ["submission.item.terminal", "submission.item.terminal", "submission.terminal"],
            )
            self.assertEqual(service.status()["waitingBatchEvents"], 0)
        finally:
            await service.stop()

    async def test_blocked_kafka_does_not_block_webhook(self) -> None:
        publisher = _BlockingPublisher()
        service = NotificationService(publisher)
        webhook = AsyncMock()
        with patch("aiweb.notifications.service.post_terminal", webhook):
            await service.start()
            try:
                event = {"event": "submission.item.terminal", "submissionId": "s1"}
                service.enqueue(event, callback_url="https://callback.example/result")
                await asyncio.wait_for(publisher.started.wait(), timeout=1)
                await asyncio.wait_for(service._webhook_queue.join(), timeout=1)
                webhook.assert_awaited_once_with("https://callback.example/result", event)
            finally:
                publisher.release.set()
                await service.stop()


class KafkaPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_brokers_is_visible_degradation(self) -> None:
        publisher = KafkaPublisher(brokers="")
        with self.assertLogs("aiweb.broadcast", level="ERROR"):
            await publisher.start()
            await publisher.publish_terminal({"event": "submission.terminal", "submissionId": "s1"})
        status = publisher.status()
        self.assertTrue(status["degraded"])
        self.assertFalse(status["ready"])
        self.assertEqual(status["failed"], 1)

    async def test_real_mode_uses_submission_as_partition_key(self) -> None:
        created: list[object] = []

        class _FakeProducer:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.messages: list[tuple] = []
                created.append(self)

            async def start(self) -> None:
                return None

            async def send_and_wait(self, topic, *, value, key) -> None:
                self.messages.append((topic, value, key))

            async def stop(self) -> None:
                return None

        fake_module = SimpleNamespace(AIOKafkaProducer=_FakeProducer)
        with patch.dict(sys.modules, {"aiokafka": fake_module}):
            publisher = KafkaPublisher(brokers="kafka:9092")
            await publisher.start()
            await publisher.publish_terminal({"event": "submission.terminal", "submissionId": "s1"})
            await publisher.close()

        producer = created[0]
        self.assertEqual(producer.messages[0][0], "ai-web.submission.result")
        self.assertEqual(producer.messages[0][2], b"s1")
        self.assertEqual(producer.kwargs["acks"], "all")
        self.assertTrue(producer.kwargs["enable_idempotence"])

    async def test_unknown_backend_falls_back_to_visible_degraded_stdout(self) -> None:
        settings = Settings(_env_file=None, broadcast_backend="kafak")
        with self.assertLogs("aiweb.broadcast", level="ERROR"):
            publisher = make_publisher(settings)
        self.assertEqual(publisher.name, "stdout")
        self.assertEqual(publisher.status()["requestedBackend"], "kafak")
        self.assertTrue(publisher.status()["degraded"])
        self.assertIn("回退到 stdout", publisher.status()["reason"])


class EventPayloadTests(unittest.TestCase):
    def test_item_event_includes_goal_and_failure_reason(self) -> None:
        from aiweb.notifications.events import build_item_terminal_event

        submission, item, run = _event_models(
            state="failed",
            status_reason="run_failed",
            fail_reason="element not found",
        )
        event = build_item_terminal_event(submission=submission, item=item, run=run)
        self.assertEqual(event["goal"], item.run_content)
        self.assertEqual(event["failureReason"], "element not found")

    def test_success_event_has_no_failure_reason(self) -> None:
        from aiweb.notifications.events import build_item_terminal_event

        submission, item, run = _event_models(
            state="success",
            status_reason="run_success",
            fail_reason="ignored stale value",
        )
        event = build_item_terminal_event(submission=submission, item=item, run=run)
        self.assertIsNone(event["failureReason"])


class HealthContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_degradation_does_not_change_service_health(self) -> None:
        from aiweb.main import health

        broadcast = {
            "backend": "kafka",
            "ready": False,
            "degraded": True,
            "reason": "broker unavailable",
        }
        with patch("aiweb.main.notifications.status", return_value=broadcast):
            payload = await health()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["broadcast"]["degraded"])


if __name__ == "__main__":
    unittest.main()
