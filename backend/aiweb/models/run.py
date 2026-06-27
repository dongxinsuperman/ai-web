"""执行记录与步骤表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aiweb.models.base import Base, short_id, utcnow

# run 状态
RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_FAILED = "failed"


class Run(Base):
    __tablename__ = "t_aiweb_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=short_id)
    item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("t_aiweb_item.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), default=RUN_RUNNING)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[dict] = mapped_column(JSON, default=dict)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("idx_run_heartbeat", "state", "heartbeat_at"),)


class RunStep(Base):
    __tablename__ = "t_aiweb_run_step"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("t_aiweb_run.id", ondelete="CASCADE")
    )
    step_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str | None] = mapped_column(String(32))
    thought: Mapped[str | None] = mapped_column(Text)
    action_raw: Mapped[str | None] = mapped_column(Text)  # 模型原始 Action 文本（用于探针/校准）
    action_detail: Mapped[dict | None] = mapped_column(JSON)  # 解析后的统一动作对象
    screenshot_before: Mapped[str | None] = mapped_column(String(1024))
    screenshot_after: Mapped[str | None] = mapped_column(String(1024))
    token_usage: Mapped[dict | None] = mapped_column(JSON)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("idx_step_run", "run_id", "step_no"),)
