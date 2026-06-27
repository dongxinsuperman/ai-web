"""Webhook 终态回调（best-effort，超时不重试，失败仅记日志）。"""
from __future__ import annotations

import logging

import httpx

from aiweb.settings import get_settings

logger = logging.getLogger("aiweb.webhook")


async def _post(url: str | None, payload: dict) -> None:
    if not url:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        logger.warning("跳过非法 callbackUrl: %s", url)
        return
    timeout = get_settings().webhook_timeout_sec
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(url, json=payload)
    except Exception as e:  # best-effort
        logger.warning("Webhook 投递失败 %s: %s", url, e)


async def fire_item_terminal(url, *, submission, item, run, result) -> None:
    payload = {
        "event": "submission.item.terminal",
        "version": 1,
        "submissionId": submission.id,
        "submissionName": submission.name,
        "itemId": item.id,
        "caseId": item.case_id,
        "caseName": item.case_name,
        "platform": item.platform,
        "engine": "web-vlm",
        "state": item.state,
        "statusReason": item.status_reason,
        "runId": run.id if run else None,
        "attempts": item.attempts,
        "elapsedMs": run.elapsed_ms if run else None,
        "steps": run.steps if run else 0,
        "tokenStats": (run.token_usage if run else {}) or {},
        "reportUrl": item.report_url,
    }
    await _post(url, payload)


async def fire_submission_terminal(url, *, submission) -> None:
    payload = {
        "event": "submission.terminal",
        "version": 1,
        "submissionId": submission.id,
        "submissionName": submission.name,
        "submissionState": submission.state,
        "counts": submission.counts or {},
        "summaryReportUrl": submission.summary_report_url,
    }
    await _post(url, payload)
