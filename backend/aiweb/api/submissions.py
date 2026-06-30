"""投递 / 查询 / 取消。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from aiweb.agent_hub import agent_hub
from aiweb.db import session_scope
from aiweb.models.item import (ITEM_CANCELLED, ITEM_QUEUED, ITEM_RUNNING, Item)
from aiweb.models.run import RUN_RUNNING, Run, RunStep
from aiweb.models.submission import Submission
from aiweb.scheduler.service import SubmissionRejected, parse_and_validate
from aiweb.slots import canon

router = APIRouter(tags=["submissions"])


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


@router.post("/submissions", dependencies=[_guard()])
async def create_submission(payload: dict = Body(...)):
    async with session_scope() as s:
        try:
            submission = await parse_and_validate(s, payload)
        except SubmissionRejected as e:
            raise HTTPException(
                status_code=400,
                detail={"rejectReason": e.reason, "rejectDetail": e.detail, "index": e.index},
            )
        await s.flush()
        items = (await s.execute(
            select(Item).where(Item.submission_id == submission.id).order_by(Item.created_at)
        )).scalars().all()
        return {
            "submissionId": submission.id,
            "submissionName": submission.name,
            "acceptedAt": submission.created_at.isoformat(),
            "items": [
                {"itemId": it.id, "caseId": it.case_id, "platform": it.platform, "state": it.state}
                for it in items
            ],
        }


@router.get("/submissions", dependencies=[_guard()])
async def list_submissions(limit: int = Query(default=50, le=200)):
    async with session_scope() as s:
        rows = (await s.execute(
            select(Submission).order_by(Submission.created_at.desc()).limit(limit)
        )).scalars().all()
        return [{
            "id": sub.id, "name": sub.name, "state": sub.state, "counts": sub.counts or {},
            "summaryReportUrl": sub.summary_report_url, "createdAt": sub.created_at.isoformat(),
        } for sub in rows]


@router.get("/submissions/{submission_id}", dependencies=[_guard()])
async def get_submission(submission_id: str):
    async with session_scope() as s:
        submission = await s.get(Submission, submission_id)
        if not submission:
            raise HTTPException(status_code=404, detail="submission not found")
        items = (await s.execute(
            select(Item).where(Item.submission_id == submission_id).order_by(Item.created_at)
        )).scalars().all()
        return {
            "id": submission.id,
            "name": submission.name,
            "state": submission.state,
            "counts": submission.counts or {},
            "summaryReportUrl": submission.summary_report_url,
            "items": [{
                "caseId": it.case_id, "caseName": it.case_name, "platform": it.platform, "state": it.state,
                "statusReason": it.status_reason, "attempts": it.attempts, "reportUrl": it.report_url,
            } for it in items],
        }


@router.get("/submissions/{submission_id}/items/{case_id}", dependencies=[_guard()])
async def get_item(
    submission_id: str, case_id: str,
    include_run: bool = Query(default=False),
    platform: str | None = Query(default=None),
):
    async with session_scope() as s:
        # 一条 case 可能多端，按 platform 精确定位；不传则取第一条
        conds = [Item.submission_id == submission_id, Item.case_id == case_id]
        if platform:
            conds.append(Item.platform == canon(platform))
        item = (await s.execute(select(Item).where(*conds).order_by(Item.created_at))).scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="item not found")
        data = {
            "caseId": item.case_id, "caseName": item.case_name, "platform": item.platform,
            "runContent": item.run_content,
            "state": item.state, "statusReason": item.status_reason, "attempts": item.attempts,
            "assets": item.assets or [], "reportUrl": item.report_url,
        }
        if include_run:
            run = (await s.execute(
                select(Run).where(Run.item_id == item.id).order_by(Run.started_at.desc())
            )).scalars().first()
            if run:
                steps = (await s.execute(
                    select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_no)
                )).scalars().all()
                data["run"] = {
                    "id": run.id, "state": run.state, "steps": run.steps,
                    "tokenUsage": run.token_usage or {}, "elapsedMs": run.elapsed_ms,
                    "failReason": run.fail_reason,
                    "stepList": [{
                        "stepNo": st.step_no, "action": st.action, "thought": st.thought,
                        "actionRaw": st.action_raw, "screenshotBefore": st.screenshot_before,
                        "screenshotAfter": st.screenshot_after, "elapsedMs": st.elapsed_ms,
                    } for st in steps],
                }
        return data


@router.post("/submissions/{submission_id}/cancel", dependencies=[_guard()])
async def cancel_submission(submission_id: str):
    stop_runs: list[str] = []
    async with session_scope() as s:
        items = (await s.execute(
            select(Item).where(Item.submission_id == submission_id)
        )).scalars().all()
        if not items:
            raise HTTPException(status_code=404, detail="submission not found")
        n = 0
        for it in items:
            if it.state == ITEM_QUEUED:
                it.state = ITEM_CANCELLED
                it.status_reason = "cancelled"
                n += 1
            elif it.state == ITEM_RUNNING:
                it.cancel_requested = True
                run = (await s.execute(
                    select(Run).where(Run.item_id == it.id, Run.state == RUN_RUNNING).order_by(Run.started_at.desc())
                )).scalars().first()
                if run:
                    stop_runs.append(run.id)
                n += 1
    for run_id in stop_runs:
        try:
            await agent_hub.send_stop_run(run_id)
        except Exception:
            pass
    return {"cancelled": n}


@router.post("/submissions/{submission_id}/cases/{case_id}/cancel", dependencies=[_guard()])
async def cancel_item(submission_id: str, case_id: str, platform: str | None = Query(default=None)):
    stop_runs: list[str] = []
    async with session_scope() as s:
        # 不传 platform = 取消该 case 的全部端；传了只取消指定端
        conds = [Item.submission_id == submission_id, Item.case_id == case_id]
        if platform:
            conds.append(Item.platform == canon(platform))
        items = (await s.execute(select(Item).where(*conds))).scalars().all()
        if not items:
            raise HTTPException(status_code=404, detail="item not found")
        results = []
        for item in items:
            if item.state == ITEM_QUEUED:
                item.state = ITEM_CANCELLED
                item.status_reason = "cancelled"
            elif item.state == ITEM_RUNNING:
                item.cancel_requested = True
                run = (await s.execute(
                    select(Run).where(Run.item_id == item.id, Run.state == RUN_RUNNING).order_by(Run.started_at.desc())
                )).scalars().first()
                if run:
                    stop_runs.append(run.id)
            results.append({"platform": item.platform, "state": item.state,
                            "cancelRequested": item.cancel_requested})
    for run_id in stop_runs:
        try:
            await agent_hub.send_stop_run(run_id)
        except Exception:
            pass
    return {"cases": results}
