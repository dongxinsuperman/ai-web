"""自包含 HTML 报告生成。"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "j2"]),
)

_STATE_LABEL = {"success": "成功", "failed": "失败", "needs_human": "需人工", "running": "执行中"}


def build_item_report(item, run, steps, *, finish_content: str | None, segments: int) -> str:
    """渲染单条执行报告 HTML。steps 为 dict 列表（含截图 URL）。"""
    tok = run.token_usage or {}
    template = _env.get_template("report.html.j2")
    return template.render(
        item=item,
        run=run,
        steps=steps,
        tok={"total_tokens": tok.get("total_tokens", 0), "cached_tokens": tok.get("cached_tokens", 0)},
        state_label=_STATE_LABEL.get(run.state, run.state),
        finish_content=finish_content,
        segments=segments,
        now=_now(),
    )


def build_summary_report(submission, items) -> str:
    template = _env.get_template("summary.html.j2")
    return template.render(
        submission=submission, items=items, counts=submission.counts or {}, now=_now()
    )
