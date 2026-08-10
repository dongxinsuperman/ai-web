"""Kafka 与 Webhook 共用的终态事件构造器。"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from aiweb.models.item import Item
from aiweb.models.run import Run
from aiweb.models.submission import Submission

ITEM_TERMINAL_EVENT = "submission.item.terminal"
SUBMISSION_TERMINAL_EVENT = "submission.terminal"
EVENT_VERSION = 1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def build_item_terminal_event(*, submission: Submission, item: Item, run: Run | None) -> dict:
    return {
        "event": ITEM_TERMINAL_EVENT,
        "version": EVENT_VERSION,
        "ts": datetime.now(UTC).isoformat(),
        "submissionId": submission.id,
        "submissionName": submission.name or submission.id,
        "itemId": item.id,
        "caseId": item.case_id,
        "caseName": item.case_name or item.case_id,
        "platform": item.platform,
        "engine": "web-vlm" if run else None,
        "state": item.state,
        "statusReason": item.status_reason,
        "runId": run.id if run else None,
        "retryMax": item.retry_max,
        "attempts": item.attempts,
        "enqueuedAt": _iso(item.created_at),
        "startedAt": _iso(run.started_at) if run else None,
        "finishedAt": _iso(run.finished_at) if run else None,
        "elapsedMs": run.elapsed_ms if run else None,
        "steps": run.steps if run else 0,
        "tokenStats": (run.token_usage if run else {}) or {},
        "reportUrl": item.report_url,
    }


def build_submission_terminal_event(
    *, submission: Submission, items: Iterable[Item]
) -> dict:
    item_list = list(items)
    counts: dict[str, int] = {}
    platform_counts: dict[str, int] = {}
    platform_state_counts: dict[str, dict[str, int]] = {}
    for item in item_list:
        counts[item.state] = counts.get(item.state, 0) + 1
        platform_counts[item.platform] = platform_counts.get(item.platform, 0) + 1
        state_counts = platform_state_counts.setdefault(item.platform, {})
        state_counts[item.state] = state_counts.get(item.state, 0) + 1

    return {
        "event": SUBMISSION_TERMINAL_EVENT,
        "version": EVENT_VERSION,
        "ts": datetime.now(UTC).isoformat(),
        "submissionId": submission.id,
        "submissionName": submission.name or submission.id,
        "submissionState": submission.state,
        "totalItems": len(item_list),
        "counts": counts,
        "platformCounts": platform_counts,
        "platformStateCounts": platform_state_counts,
        "summaryReportUrl": submission.summary_report_url,
    }
