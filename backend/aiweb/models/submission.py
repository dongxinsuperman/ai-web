"""批次表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aiweb.models.base import Base, short_id, utcnow

# 批次状态
SUB_ACCEPTED = "accepted"
SUB_DONE = "done"
SUB_CANCELLED = "cancelled"
SUB_EXPIRED = "expired"


class Submission(Base):
    __tablename__ = "t_aiweb_submission"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=short_id)
    name: Mapped[str | None] = mapped_column(String(255))
    callback_url: Mapped[str | None] = mapped_column(String(1024))
    retry_max: Mapped[int] = mapped_column(Integer, default=0)
    function_map_context: Mapped[str | None] = mapped_column(Text)  # 只读执行参考，注入 prompt
    state: Mapped[str] = mapped_column(String(16), default=SUB_ACCEPTED, index=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    summary_report_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
