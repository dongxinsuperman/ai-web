"""投递准入校验 + 落库。"""
from __future__ import annotations

from sqlalchemy import select

from aiweb.models.asset import Asset
from aiweb.models.item import Item
from aiweb.models.submission import Submission
from aiweb.slots import canon, get_slots


class SubmissionRejected(Exception):
    def __init__(self, reason: str, detail: str = "", index: int | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.index = index


async def parse_and_validate(session, payload: dict) -> Submission:
    if not isinstance(payload, dict):
        raise SubmissionRejected("invalid_body", "请求体必须是对象")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise SubmissionRejected("invalid_body", "items 必须是非空数组")

    retry_max = int(payload.get("retryMax", 0) or 0)
    # functionMapContext：只读执行参考，封顶 8000 字（cacheMode / deviceAliasPools 收下即忽略）
    fmc = payload.get("functionMapContext")
    if isinstance(fmc, str) and fmc.strip():
        fmc = fmc.strip()[:8000]
    else:
        fmc = None
    submission = Submission(
        name=payload.get("submissionName"),
        callback_url=payload.get("callbackUrl"),
        retry_max=retry_max,
        function_map_context=fmc,
        counts={},
    )
    session.add(submission)
    await session.flush()  # 拿到 submission.id

    seen: set[tuple[str, str]] = set()  # (caseId, platform) 唯一
    # 预取素材名集合，用于校验 assets 引用
    asset_names = {a.name for a in (await session.execute(select(Asset.name))).scalars()}
    # 当前启用的浏览器引擎（台数>0），用于校验 platform
    enabled_engines = set((await get_slots(session)).keys())

    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise SubmissionRejected("invalid_body", f"items[{idx}] 必须是对象", idx)
        case_id = raw.get("caseId")
        run_content = raw.get("runContent")
        if not case_id:
            raise SubmissionRejected("missing_field", f"items[{idx}].caseId", idx)
        if not run_content:
            raise SubmissionRejected("missing_field", f"items[{idx}].runContent", idx)

        assets = raw.get("assets") or []
        if not isinstance(assets, list):
            raise SubmissionRejected("invalid_body", f"items[{idx}].assets 必须是数组", idx)
        for name in assets:
            if name not in asset_names:
                raise SubmissionRejected("unknown_asset", f"素材不存在: {name}", idx)

        # platforms = 勾选的多个浏览器引擎；一条 case 在每个端各跑一次（fan-out，对齐 ai-phone）
        raw_platforms = raw.get("platforms") or []
        if not (isinstance(raw_platforms, list) and raw_platforms):
            raw_platforms = ["chrome"]
        # 规范化 + 去重保序
        engines: list[str] = []
        for p in raw_platforms:
            eng = canon(p)
            if eng not in engines:
                engines.append(eng)
        for eng in engines:
            if eng not in enabled_engines:
                raise SubmissionRejected(
                    "unsupported_platform",
                    f"items[{idx}] 浏览器引擎未启用: {eng}（可用: {sorted(enabled_engines)}）",
                    idx,
                )
            key = (str(case_id), eng)
            if key in seen:
                raise SubmissionRejected("duplicate_case_id", f"caseId+引擎 重复: {case_id}/{eng}", idx)
            seen.add(key)
            session.add(
                Item(
                    submission_id=submission.id,
                    case_id=str(case_id),
                    case_name=raw.get("caseName"),
                    run_content=str(run_content),
                    assets=assets,
                    platform=eng,
                    retry_max=retry_max,
                )
            )

    await session.flush()
    return submission
