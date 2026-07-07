"""单任务执行生命周期：建 run → 接收 Agent 步骤 → 出报告 → 回调。"""
from __future__ import annotations

import logging
import base64

from sqlalchemy import select

from aiweb import sites as SITES
from aiweb.db import session_scope
from aiweb.function_map_context import merge_function_map_context
from aiweb.models.item import (ITEM_CANCELLED, ITEM_FAILED, ITEM_QUEUED, ITEM_RUNNING, ITEM_SUCCESS, Item)
from aiweb.models.run import RUN_FAILED, RUN_RUNNING, RUN_SUCCESS, Run, RunStep
from aiweb.models.submission import SUB_DONE, Submission
from aiweb.report import build_item_report, build_summary_report
from aiweb.runtime_config import get_headless
from aiweb.settings import get_settings
from aiweb.storage import get_storage
from aiweb.webhook import fire_item_terminal, fire_submission_terminal
from aiweb.models.base import utcnow

logger = logging.getLogger("aiweb.worker")
_TERMINAL_ITEM = {ITEM_SUCCESS, ITEM_FAILED, ITEM_CANCELLED}


async def create_run_for_item(item_id: str, *, claimed_by: str) -> dict | None:
    settings = get_settings()
    storage = get_storage()
    headless = await get_headless()

    async with session_scope() as s:
        item = await s.get(Item, item_id)
        if item is None or item.state != ITEM_RUNNING:
            return None
        submission = await s.get(Submission, item.submission_id)
        if submission is None:
            return None
        function_map_context = merge_function_map_context(
            submission.function_map_context,
            item.function_map_context,
        )
        run = Run(
            item_id=item.id,
            state=RUN_RUNNING,
            claimed_by=settings.pod_id,
            heartbeat_at=utcnow(),
            function_map_context=function_map_context,
        )
        run.claimed_by = claimed_by
        s.add(run)
        await s.flush()
        # 站点映射与免登：命中站点 → 网址簿（注入 prompt）+ 登录态（注入浏览器）
        matched_sites = await SITES.resolve_sites(s, item.run_content)
        site_directory = SITES.build_directory_text(matched_sites)

        payload = {
            "runId": run.id,
            "itemId": item.id,
            "submissionId": submission.id,
            "caseId": item.case_id,
            "caseName": item.case_name,
            "platform": item.platform or "chrome",
            "runContent": item.run_content,
            "assets": [
                {"name": name, "url": storage.url_for(f"assets/{name}")}
                for name in list(item.assets or [])
            ],
            "functionMapContext": run.function_map_context,
            "siteDirectory": site_directory,
            "storageState": None,
            "headless": headless,
            "viewport": {"width": settings.viewport_size[0], "height": settings.viewport_size[1]},
        }

    # login_api 可能访问外部系统，放在 DB session 外执行。
    payload["storageState"] = await SITES.build_auth_storage_state(matched_sites)
    return payload


def _image_bytes(payload: dict, key: str) -> bytes | None:
    raw = payload.get(key)
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return base64.b64decode(raw)
        except Exception:
            return None
    b64 = payload.get(f"{key}_b64")
    if isinstance(b64, str) and b64:
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    return None


async def persist_run_step(run_id: str, payload: dict) -> None:
    storage = get_storage()
    step_no = int(payload.get("step_no") or 0)
    before_url = after_url = None
    sb = _image_bytes(payload, "screenshot_before")
    sa = _image_bytes(payload, "screenshot_after")

    async with session_scope() as s:
        run = await s.get(Run, run_id)
        if run is None:
            return
        item = await s.get(Item, run.item_id)
        if item is None:
            return
        if sb:
            _, before_url = storage.save_screenshot(item.submission_id, run_id, f"{step_no}_before.png", sb)
        if sa:
            _, after_url = storage.save_screenshot(item.submission_id, run_id, f"{step_no}_after.png", sa)

        existing = (await s.execute(
            select(RunStep).where(RunStep.run_id == run_id, RunStep.step_no == step_no)
        )).scalars().first()
        if existing is None:
            existing = RunStep(run_id=run_id, step_no=step_no)
            s.add(existing)
        existing.action = payload.get("action")
        existing.thought = payload.get("thought")
        existing.action_raw = payload.get("action_raw")
        existing.action_detail = payload.get("action_detail")
        existing.screenshot_before = before_url or existing.screenshot_before
        existing.screenshot_after = after_url or existing.screenshot_after
        existing.token_usage = payload.get("token_usage")
        existing.elapsed_ms = payload.get("elapsed_ms")


async def heartbeat_run(run_id: str) -> None:
    async with session_scope() as s:
        r = await s.get(Run, run_id)
        if r:
            r.heartbeat_at = utcnow()


async def should_cancel_item(item_id: str) -> bool:
    async with session_scope() as s:
        it = await s.get(Item, item_id)
        return bool(it and it.cancel_requested)


class _FakeResult:
    def __init__(self, status, steps, token_usage, fail_reason=None, finish_content=None, segments=1):
        self.status = status
        self.steps = steps
        self.token_usage = token_usage
        self.fail_reason = fail_reason
        self.finish_content = finish_content
        self.segments = segments


def _failed_result(steps, reason):
    return _FakeResult("failed", steps, {}, fail_reason=reason)


async def finalize_run(run_id: str, result, *, elapsed_ms: int | None = None) -> None:
    storage = get_storage()
    retrying = False

    async with session_scope() as s:
        run = await s.get(Run, run_id)
        if run is None:
            return
        item = await s.get(Item, run.item_id)
        if item is None:
            return
        submission = await s.get(Submission, item.submission_id)
        if submission is None:
            return
        callback_url = submission.callback_url
        run.state = RUN_SUCCESS if result.status == "success" else RUN_FAILED
        run.steps = result.steps
        run.token_usage = result.token_usage or {}
        run.elapsed_ms = elapsed_ms
        run.fail_reason = result.fail_reason
        run.finished_at = utcnow()

        if item.cancel_requested:
            item.state = ITEM_CANCELLED
            item.status_reason = "cancelled"
        elif result.status == "success":
            item.state = ITEM_SUCCESS
            item.status_reason = "run_success"
        elif result.status == "needs_human":
            item.state = ITEM_FAILED
            item.status_reason = "needs_human"
        else:  # failed
            if item.attempts <= item.retry_max:
                item.state = ITEM_QUEUED  # 重试
                item.status_reason = "retry"
                retrying = True
            else:
                item.state = ITEM_FAILED
                # status_reason 为 varchar(255)，失败原因可能很长（如 Playwright 报错），截断兜底
                item.status_reason = (result.fail_reason or "run_failed")[:255]

        # 生成本次 run 报告
        steps_rows = (await s.execute(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_no)
        )).scalars().all()
        steps_data = [{
            "step_no": st.step_no, "action": st.action, "thought": st.thought,
            "action_raw": st.action_raw, "elapsed_ms": st.elapsed_ms,
            "token_usage": st.token_usage or {},
            "screenshot_before": st.screenshot_before, "screenshot_after": st.screenshot_after,
        } for st in steps_rows]
        html = build_item_report(
            item, run, steps_data,
            finish_content=result.finish_content, segments=getattr(result, "segments", 1),
        )
        # 文件名带 platform：一条 case 多端 fan-out 时各端各自报告，避免同 caseId 互相覆盖
        _, report_url = storage.save_report(item.submission_id, f"{item.case_id}_{item.platform}.html", html)
        if not retrying:
            item.report_url = report_url

    if retrying:
        return  # 等待下一轮调度重试，不发终态

    # 4. item 终态：刷新批次计数，必要时收口
    await _refresh_submission(item.submission_id)
    await _maybe_fire_terminal(run_id, item.id, item.submission_id, callback_url)


async def _refresh_submission(submission_id: str) -> None:
    async with session_scope() as s:
        items = (await s.execute(select(Item).where(Item.submission_id == submission_id))).scalars().all()
        counts: dict[str, int] = {}
        for it in items:
            counts[it.state] = counts.get(it.state, 0) + 1
        submission = await s.get(Submission, submission_id)
        submission.counts = counts
        all_terminal = all(it.state in _TERMINAL_ITEM for it in items)
        if all_terminal and submission.state != SUB_DONE:
            submission.state = SUB_DONE
            html = build_summary_report(submission, items)
            _, url = get_storage().save_report(submission_id, "_summary.html", html)
            submission.summary_report_url = url


async def _maybe_fire_terminal(run_id, item_id, submission_id, callback_url) -> None:
    async with session_scope() as s:
        item = await s.get(Item, item_id)
        run = await s.get(Run, run_id)
        submission = await s.get(Submission, submission_id)
    await fire_item_terminal(callback_url, submission=submission, item=item, run=run, result=None)
    if submission.state == SUB_DONE:
        await fire_submission_terminal(callback_url, submission=submission)
