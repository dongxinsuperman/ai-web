"""Playwright 浏览器生命周期：单浏览器实例，每个 run 独立 context（隔离）。

含基础反检测（anti-bot）：
- 启动参数关闭 AutomationControlled，去掉 navigator.webdriver 标记。
- context 覆盖 UA（抹掉 HeadlessChrome）、Accept-Language。
- 注入 stealth 脚本，修补 webdriver / languages / plugins / chrome 等常见指纹。
注意：headless 比有头更易被风控（如百度滑块）。本地可设 AIWEB_HEADLESS=false 跑有头，
检测概率显著降低（与原 Selenium + Xvfb 有头方案一致）。
"""
from __future__ import annotations

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from aiweb.settings import get_settings

# 默认 UA（去掉 Headless 标记）。可被 AIWEB_USER_AGENT 覆盖。
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
const _query = window.navigator.permissions && window.navigator.permissions.query;
if (_query) {
  window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _query(p);
}
"""

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
]

# 浏览器类型（platform）→ Playwright 引擎
_ENGINE_MAP = {
    "chrome": "chromium", "chromium": "chromium",
    "firefox": "firefox",
    "safari": "webkit", "webkit": "webkit",
}


class BrowserManager:
    """每个任务独立起一个浏览器实例：headless 由全局配置动态决定，可热切。

    （测试场景下并发不高，per-run 启动开销可接受，换来 headless 即时可切 + 更强隔离。）
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()

    async def stop(self) -> None:
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def launch(self, headless: bool, browser: str = "chrome") -> Browser:
        if self._pw is None:
            raise RuntimeError("BrowserManager 未启动")
        engine = _ENGINE_MAP.get((browser or "chrome").lower(), "chromium")
        launcher = getattr(self._pw, engine)
        # 反检测启动参数是 Chromium 专属；firefox/webkit 用各自默认启动
        if engine == "chromium":
            return await launcher.launch(
                headless=headless, args=_LAUNCH_ARGS, ignore_default_args=["--enable-automation"]
            )
        return await launcher.launch(headless=headless)

    async def new_context(self, browser: Browser, storage_state: dict | None = None) -> BrowserContext:
        s = get_settings()
        w, h = s.viewport_size
        # UA：chromium 用内置去-Headless 的 Chrome UA；firefox/webkit 用各自原生 UA
        # （给 Firefox/WebKit 套 Chrome UA 反而是矛盾指纹），除非显式配置 AIWEB_USER_AGENT
        try:
            engine = browser.browser_type.name
        except Exception:
            engine = "chromium"
        ua = s.user_agent or (_DEFAULT_UA if engine == "chromium" else "")
        kwargs: dict = dict(
            viewport={"width": w, "height": h},
            accept_downloads=True,
            locale="zh-CN",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        if ua:
            kwargs["user_agent"] = ua
        tmp_path: str | None = None
        if storage_state:
            import json
            import os
            import tempfile

            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(storage_state, f)
            kwargs["storage_state"] = tmp_path
        context = await browser.new_context(**kwargs)
        if tmp_path:
            import os

            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        await context.add_init_script(_STEALTH_JS)
        return context


# 进程内单例
browser_manager = BrowserManager()
