"""素材文件表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from aiweb.models.base import Base, short_id, utcnow


class Asset(Base):
    __tablename__ = "t_aiweb_asset"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=short_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int | None] = mapped_column(BigInteger)
    mime: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
