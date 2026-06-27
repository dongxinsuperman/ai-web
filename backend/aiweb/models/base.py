"""SQLAlchemy 声明基类与工具。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def short_id() -> str:
    """短 uuid（12 位 hex）。"""
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
