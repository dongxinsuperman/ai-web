"""FastAPI 入口：生命周期内启动浏览器 / 调度器 / 回收器，挂载静态文件与 API。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aiweb.api import api_router
from aiweb.db import init_db
from aiweb.kernel.browser import browser_manager
from aiweb.scheduler.dispatcher import dispatcher
from aiweb.scheduler.reaper import reaper
from aiweb.settings import get_settings
from aiweb.storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("aiweb")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("AI Web 启动中 pod=%s", settings.pod_id)
    await init_db()
    get_storage()  # 确保存储目录就绪
    await browser_manager.start()
    await reaper.start()
    await dispatcher.start()
    logger.info("AI Web 就绪 :%s", settings.port)
    try:
        yield
    finally:
        await dispatcher.stop()
        await reaper.stop()
        await browser_manager.stop()
        logger.info("AI Web 已停止")


app = FastAPI(title="AI Web", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# 静态文件：报告 / 截图 / 素材
_storage_dir = os.path.abspath(get_settings().storage_dir)
os.makedirs(_storage_dir, exist_ok=True)
app.mount("/files", StaticFiles(directory=_storage_dir), name="files")


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("aiweb.main:app", host=s.host, port=s.port, workers=1)


if __name__ == "__main__":
    main()
