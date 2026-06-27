"""单任务执行：建 run → 起浏览器 context → 跑内核 → 落步骤 → 出报告 → 回调。"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select

from aiweb import sites as SITES
from aiweb.db import session_scope
from aiweb.kernel.browser import browser_manager
from aiweb.kernel.runner import WebVLMRunner
from aiweb.models.config import ConfigKV
from aiweb.models.item import (ITEM_CANCELLED, ITEM_FAILED, ITEM_QUEUED, ITEM_RUNNING, ITEM_SUCCESS, Item)
from aiweb.models.run import RUN_FAILED, RUN_RUNNING, RUN_SUCCESS, Run, RunStep
from aiweb.models.submission import SUB_DONE, Submission
from aiweb.report import build_item_report, build_summary_report
from aiweb.settings import get_settings
from aiweb.storage import get_storage
from aiweb.webhook import fire_item_terminal, fire_submission_terminal
from aiweb.models.base import utcnow

logger = logging.getLogger("aiweb.worker")
_TERMINAL_ITEM = {ITEM_SUCCESS, ITEM_FAILED, ITEM_CANCELLED}
_TRUTHY = {"1", "true", "yes", "on"}


async def _get_headless() -> bool:
    """读全局配置 headless（可热切）；未配置则回落到 settings 默认。"""
    async with session_scope() as s:
        cfg = await s.get(ConfigKV, "headless")
        if cfg is not None:
            return str(cfg.value).lower() in _TRUTHY
    return get_settings().headless


async def run_item(item_id: str) -> None:
    settings = get_settings()
    storage = get_storage()

    # 1. 载入 item / submission，创建 run
    async with session_scope() as s:
        item = await s.get(Item, item_id)
        if item is None or item.state != ITEM_RUNNING:
            return
        submission = await s.get(Submission, item.submission_id)
        run = Run(item_id=item.id, state=RUN_RUNNING, claimed_by=settings.pod_id, heartbeat_at=utcnow())
        s.add(run)
        await s.flush()
        run_id = run.id
        submission_id = submission.id
        callback_url = submission.callback_url
        function_map_context = submission.function_map_context
        run_content = item.run_content
        assets = list(item.assets or [])
        platform = item.platform or "chrome"
        case_retry_max = item.retry_max
        attempts = item.attempts
        # 站点映射与免登：命中站点 → 网址簿（注入 prompt）+ 登录态（注入浏览器）
        matched_sites = await SITES.resolve_sites(s, run_content)
        site_directory = SITES.build_directory_text(matched_sites)

    # 2. 起浏览器并执行内核（不持 DB 会话）
    t0 = time.time()
    step_counter = {"n": 0}

    async def persist_step(payload: dict) -> None:
        before_url = after_url = None
        sb = payload.get("screenshot_before")
        sa = payload.get("screenshot_after")
        if sb:
            _, before_url = storage.save_screenshot(submission_id, run_id, f"{payload['step_no']}_before.png", sb)
        if sa:
            _, after_url = storage.save_screenshot(submission_id, run_id, f"{payload['step_no']}_after.png", sa)
        step_counter["n"] = payload["step_no"]
        async with session_scope() as s:
            s.add(RunStep(
                run_id=run_id,
                step_no=payload["step_no"],
                action=payload.get("action"),
                thought=payload.get("thought"),
                action_raw=payload.get("action_raw"),
                action_detail=payload.get("action_detail"),
                screenshot_before=before_url,
                screenshot_after=after_url,
                token_usage=payload.get("token_usage"),
                elapsed_ms=payload.get("elapsed_ms"),
            ))

    async def heartbeat() -> None:
        async with session_scope() as s:
            r = await s.get(Run, run_id)
            if r:
                r.heartbeat_at = utcnow()

    async def should_cancel() -> bool:
        async with session_scope() as s:
            it = await s.get(Item, item_id)
            return bool(it and it.cancel_requested)

    headless = await _get_headless()
    # 构建登录态（含 login_api 动态现取；best-effort，失败不阻断）
    site_storage_state = await SITES.build_auth_storage_state(matched_sites)
    browser = None
    context = None
    try:
        browser = await browser_manager.launch(headless, platform)
        context = await browser_manager.new_context(browser, storage_state=site_storage_state)
        page = await context.new_page()
        runner = WebVLMRunner(context, page, storage.resolve_asset)
        result = await runner.run(
            run_content,
            has_assets=bool(assets),
            function_map_context=function_map_context,
            site_directory=site_directory,
            on_step=persist_step,
            on_heartbeat=heartbeat,
            should_cancel=should_cancel,
        )
    except Exception as e:  # 内核外层兜底
        logger.exception("worker 执行异常 item=%s", item_id)
        result = _failed_result(step_counter["n"], f"worker 异常: {e}")
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    elapsed_ms = int((time.time() - t0) * 1000)

    # 3. 落终态 + 报告 + 回调
    await _finalize(
        run_id=run_id, item_id=item_id, submission_id=submission_id, callback_url=callback_url,
        result=result, elapsed_ms=elapsed_ms, case_retry_max=case_retry_max, attempts=attempts,
    )


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


async def _finalize(*, run_id, item_id, submission_id, callback_url, result, elapsed_ms, case_retry_max, attempts):
    storage = get_storage()
    retrying = False

    async with session_scope() as s:
        run = await s.get(Run, run_id)
        item = await s.get(Item, item_id)
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
            if attempts <= case_retry_max:
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
        _, report_url = storage.save_report(submission_id, f"{item.case_id}_{item.platform}.html", html)
        if not retrying:
            item.report_url = report_url

    if retrying:
        return  # 等待下一轮调度重试，不发终态

    # 4. item 终态：刷新批次计数，必要时收口
    await _refresh_submission(submission_id)
    await _maybe_fire_terminal(run_id, item_id, submission_id, callback_url)


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
