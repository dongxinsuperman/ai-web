"""Webhook 终态回调（best-effort，超时不重试，失败仅记日志）。"""
from __future__ import annotations

import logging

import httpx

from aiweb.settings import get_settings

logger = logging.getLogger("aiweb.webhook")


async def post_terminal(url: str | None, payload: dict) -> None:
    if not url:
        return
    if not url.startswith(("http://", "https://")):
        logger.warning("跳过非法 callbackUrl: %s", url)
        return
    timeout = get_settings().webhook_timeout_sec
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(url, json=payload)
    except (httpx.HTTPError, ValueError) as e:  # best-effort
        logger.warning("Webhook 投递失败 %s: %s", url, e)


async def fire_item_terminal(url, *, submission, item, run, result) -> None:
    from aiweb.notifications.events import build_item_terminal_event

    await post_terminal(url, build_item_terminal_event(submission=submission, item=item, run=run))


async def fire_submission_terminal(url, *, submission) -> None:
    # 旧入口仅供兼容；新链路使用 notifications.events 构造完整批次事件。
    payload = {
        "event": "submission.terminal",
        "version": 1,
        "submissionId": submission.id,
        "submissionName": submission.name,
        "submissionState": submission.state,
        "counts": submission.counts or {},
        "summaryReportUrl": submission.summary_report_url,
    }
    await post_terminal(url, payload)
