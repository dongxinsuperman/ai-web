"""异步数据库引擎与会话。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aiweb.settings import get_settings

_settings = get_settings()

engine = create_async_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """提供一个自动提交 / 回滚的会话上下文。"""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """V1 用 create_all 建表（生产可换 alembic）。"""
    from aiweb.models.base import Base
    from aiweb import models  # noqa: F401  确保所有模型被导入注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
