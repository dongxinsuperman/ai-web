"""终态通知：Kafka 主广播 + Webhook 旁路回调。"""

from aiweb.notifications.events import build_item_terminal_event, build_submission_terminal_event
from aiweb.notifications.publisher import KafkaPublisher, NullPublisher, ResultPublisher, StdoutPublisher
from aiweb.notifications.service import NotificationService, notifications

__all__ = [
    "KafkaPublisher",
    "NotificationService",
    "NullPublisher",
    "ResultPublisher",
    "StdoutPublisher",
    "build_item_terminal_event",
    "build_submission_terminal_event",
    "notifications",
]
