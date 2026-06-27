"""站点映射与免登配置表。

- keywords：分隔符分割的关键字（任一命中 runContent 即视为涉及该站点）。
- url：站点地址（命中后注入"网址簿"供模型 open_url）。
- auth_type：none / storage_state / cookies（C: login_api 后续扩展）。
- auth_payload：按 auth_type 不同（storage_state 的 JSON / {cookies:[...]}）；敏感，加密存储（V1 明文，TODO 加密）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aiweb.models.base import Base, short_id, utcnow

AUTH_NONE = "none"
AUTH_STORAGE_STATE = "storage_state"
AUTH_COOKIES = "cookies"
AUTH_LOGIN_API = "login_api"


class Site(Base):
    __tablename__ = "t_aiweb_site"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=short_id)
    name: Mapped[str] = mapped_column(String(255))
    keywords: Mapped[str] = mapped_column(Text)  # 分隔符分割
    url: Mapped[str] = mapped_column(String(1024))
    auth_type: Mapped[str] = mapped_column(String(32), default=AUTH_NONE)
    auth_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
