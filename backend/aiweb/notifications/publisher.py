"""Submission 终态主广播：stdout / Kafka / null。"""
from __future__ import annotations

import abc
import json
import logging
from typing import Any

from aiweb.settings import Settings, get_settings

logger = logging.getLogger("aiweb.broadcast")
DEFAULT_KAFKA_TOPIC = "ai-web.submission.result"


class ResultPublisher(abc.ABC):
    name = "abstract"

    async def start(self) -> None:
        return None

    @abc.abstractmethod
    async def publish_terminal(self, event: dict[str, Any]) -> None:
        """发送终态事件；实现必须自行处理异常，不影响任务终态。"""

    async def close(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "ready": True, "degraded": False}


class NullPublisher(ResultPublisher):
    name = "null"

    async def publish_terminal(self, event: dict[str, Any]) -> None:
        return None


class StdoutPublisher(ResultPublisher):
    name = "stdout"

    def __init__(
        self,
        *,
        requested_backend: str = "stdout",
        degraded_reason: str | None = None,
    ) -> None:
        self._requested_backend = requested_backend
        self._degraded_reason = degraded_reason

    async def publish_terminal(self, event: dict[str, Any]) -> None:
        try:
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError, OverflowError) as exc:
            logger.warning("终态事件 JSON 序列化失败: %s", exc)
            return
        logger.info("[broadcast:stdout] %s", payload)

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "requestedBackend": self._requested_backend,
            "ready": True,
            "degraded": self._degraded_reason is not None,
            "reason": self._degraded_reason,
        }


class KafkaPublisher(ResultPublisher):
    """aiokafka producer。Broker 故障会显式降级并记录未发送数。"""

    name = "kafka"

    def __init__(
        self,
        *,
        brokers: str,
        topic: str = DEFAULT_KAFKA_TOPIC,
        sasl_username: str = "",
        sasl_password: str = "",
    ) -> None:
        self._brokers = brokers.strip()
        self._topic = topic.strip() or DEFAULT_KAFKA_TOPIC
        self._sasl_username = sasl_username
        self._sasl_password = sasl_password
        self._producer: Any = None
        self._ready = False
        self._degraded_reason: str | None = None
        self._sent = 0
        self._failed = 0

    async def start(self) -> None:
        if not self._brokers:
            self._degrade("AIWEB_KAFKA_BROKERS 未配置，Kafka 消息不会真实发送")
            return
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError:
            self._degrade("aiokafka 未安装，Kafka 消息不会真实发送")
            return

        kwargs: dict[str, Any] = {
            "bootstrap_servers": self._brokers,
            "linger_ms": 20,
            "acks": "all",
            "enable_idempotence": True,
            "request_timeout_ms": 10_000,
            "max_request_size": 4 * 1024 * 1024,
        }
        if self._sasl_username and self._sasl_password:
            kwargs.update({
                "security_protocol": "SASL_PLAINTEXT",
                "sasl_mechanism": "PLAIN",
                "sasl_plain_username": self._sasl_username,
                "sasl_plain_password": self._sasl_password,
            })
        try:
            self._producer = AIOKafkaProducer(**kwargs)
            await self._producer.start()
            self._ready = True
            self._degraded_reason = None
            logger.info("Kafka producer 已连接 topic=%s", self._topic)
        except Exception as exc:  # noqa: BLE001  # broker/网络/SASL 客户端异常必须统一降级
            producer = self._producer
            self._producer = None
            if producer is not None:
                try:
                    await producer.stop()
                except Exception as stop_exc:  # noqa: BLE001
                    logger.warning("Kafka producer 启动失败后关闭也异常: %s", stop_exc)
            self._degrade(f"Kafka producer 启动失败: {exc}")

    def _degrade(self, reason: str) -> None:
        self._ready = False
        self._degraded_reason = reason
        logger.error("[broadcast:kafka:degraded] %s", reason)

    async def publish_terminal(self, event: dict[str, Any]) -> None:
        try:
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            self._failed += 1
            self._degrade(f"终态事件 JSON 序列化失败: {exc}")
            return
        if self._producer is None:
            self._failed += 1
            logger.error(
                "[broadcast:kafka:not-sent] topic=%s submission=%s event=%s reason=%s",
                self._topic,
                event.get("submissionId"),
                event.get("event"),
                self._degraded_reason or "producer 未就绪",
            )
            return
        try:
            key = str(event.get("submissionId") or "").encode("utf-8") or None
            await self._producer.send_and_wait(self._topic, value=payload, key=key)
            self._sent += 1
            self._ready = True
            self._degraded_reason = None
        except Exception as exc:  # noqa: BLE001  # aiokafka 可透传多种网络异常
            self._failed += 1
            self._degrade(f"Kafka 发送失败: {exc}")

    async def close(self) -> None:
        if self._producer is None:
            return
        try:
            await self._producer.stop()
        except Exception as exc:  # noqa: BLE001  # 关闭不能阻塞 Server 退出
            logger.warning("Kafka producer 关闭异常: %s", exc)
        finally:
            self._producer = None
            self._ready = False

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "ready": self._ready,
            "degraded": self._degraded_reason is not None,
            "reason": self._degraded_reason,
            "topic": self._topic,
            "sent": self._sent,
            "failed": self._failed,
        }


def make_publisher(settings: Settings | None = None) -> ResultPublisher:
    current = settings or get_settings()
    backend = (current.broadcast_backend or "stdout").strip().lower()
    if backend == "kafka":
        return KafkaPublisher(
            brokers=current.kafka_brokers,
            topic=current.kafka_topic,
            sasl_username=current.kafka_sasl_username,
            sasl_password=current.kafka_sasl_password,
        )
    if backend == "stdout":
        return StdoutPublisher()
    if backend in {"null", "none", "off", "disable"}:
        return NullPublisher()
    reason = (
        f"不支持的 AIWEB_BROADCAST_BACKEND: {current.broadcast_backend!r}；"
        "已显式回退到 stdout，Kafka 消息不会发送"
    )
    logger.error("[broadcast:config:degraded] %s", reason)
    return StdoutPublisher(requested_backend=backend, degraded_reason=reason)
