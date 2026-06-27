"""存储抽象：V1 本地卷实现。

目录布局（storage_dir 下）：
  assets/<name>            素材文件（原名）
  reports/<sub>/<file>     报告 HTML
  reports/<sub>/shots/...  执行截图

对外 URL 统一走 /files/... 静态路由（见 api/files.py），便于报告 / Webhook 引用。
"""
from __future__ import annotations

import os
from functools import lru_cache

from aiweb.settings import get_settings


class LocalStorage:
    def __init__(self, root: str, public_base_url: str) -> None:
        self.root = os.path.abspath(root)
        self.public_base_url = public_base_url.rstrip("/")
        for sub in ("assets", "reports"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)

    # ---- 通用 ----
    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def url_for(self, rel: str) -> str:
        """相对路径 → 可访问 URL。"""
        return f"{self.public_base_url}/files/{rel.lstrip('/')}"

    def save_bytes(self, rel: str, data: bytes) -> str:
        path = self._abs(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return rel

    def save_text(self, rel: str, text: str) -> str:
        return self.save_bytes(rel, text.encode("utf-8"))

    # ---- 素材 ----
    def save_asset(self, name: str, data: bytes) -> str:
        rel = f"assets/{name}"
        self.save_bytes(rel, data)
        return rel

    def resolve_asset(self, name: str) -> str:
        """素材名 → 本地绝对路径（供 Playwright set_input_files）。"""
        path = self._abs(f"assets/{name}")
        if not os.path.exists(path):
            raise FileNotFoundError(f"素材不存在: {name}")
        return path

    def delete_asset(self, name: str) -> None:
        path = self._abs(f"assets/{name}")
        if os.path.exists(path):
            os.remove(path)

    # ---- 截图 / 报告 ----
    def save_screenshot(self, submission_id: str, run_id: str, filename: str, data: bytes) -> tuple[str, str]:
        rel = f"reports/{submission_id}/shots/{run_id}/{filename}"
        self.save_bytes(rel, data)
        return rel, self.url_for(rel)

    def save_report(self, submission_id: str, filename: str, html: str) -> tuple[str, str]:
        rel = f"reports/{submission_id}/{filename}"
        self.save_text(rel, html)
        return rel, self.url_for(rel)


@lru_cache
def get_storage() -> LocalStorage:
    s = get_settings()
    return LocalStorage(s.storage_dir, s.public_base_url)
