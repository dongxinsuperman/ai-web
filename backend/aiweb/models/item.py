"""执行单元表（= 一条自然语言任务）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aiweb.models.base import Base, short_id, utcnow

# item 状态
ITEM_QUEUED = "queued"
ITEM_RUNNING = "running"
ITEM_SUCCESS = "success"
ITEM_FAILED = "failed"
ITEM_CANCELLED = "cancelled"


class Item(Base):
    __tablename__ = "t_aiweb_item"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=short_id)
    submission_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("t_aiweb_submission.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str | None] = mapped_column(String(255))
    case_name: Mapped[str | None] = mapped_column(String(255))
    run_content: Mapped[str] = mapped_column(Text)
    function_map_context: Mapped[str | None] = mapped_column(Text)
    assets: Mapped[list] = mapped_column(JSON, default=list)
    platform: Mapped[str] = mapped_column(String(32), default="chrome")  # 浏览器类型
    state: Mapped[str] = mapped_column(String(16), default=ITEM_QUEUED)
    status_reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_max: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    report_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("idx_item_queue", "state", "created_at"),)
