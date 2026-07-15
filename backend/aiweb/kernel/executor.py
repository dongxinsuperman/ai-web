"""Playwright 执行层：把统一动作对象落地为浏览器操作。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from playwright.async_api import BrowserContext, Page

from aiweb.settings import get_settings

_MODIFIERS = {"ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt", "meta": "Meta", "cmd": "Meta", "command": "Meta"}


class ActionExecutor:
    def __init__(self, context: BrowserContext, page: Page, resolve_asset: Callable[[str], str]) -> None:
        self.context = context
        self.page = page
        self.resolve_asset = resolve_asset
        self.vw, self.vh = get_settings().viewport_size
        # 最近一次喂给模型的截图尺寸（CU 绝对像素反缩放用）；默认等于视口。
        self.frame_w, self.frame_h = self.vw, self.vh
        # Page 对象不是可传给模型的稳定标识；运行期间为每页分配 tab_id。
        self._tab_seq = 0
        self._tab_ids: dict[int, str] = {}
        self._tab_pages: dict[int, Page] = {}
        self._opened_tab_ids: list[str] = []
        self._closed_tab_ids: list[str] = []
        for existing in self.context.pages:
            self._register_tab(existing, opened=False)
        # 事件用于记录“何时出现过新页”；每轮状态刷新还会兜底扫描 context.pages，
        # 因此不依赖点击后的单次短时间轮询。
        try:
            self.context.on("page", self._on_page_created)
        except Exception:
            pass

    def set_frame(self, w: int, h: int) -> None:
        if w > 0 and h > 0:
            self.frame_w, self.frame_h = w, h

    def _abs(self, point: list[int], coord_space: str = "normalized") -> tuple[float, float]:
        if coord_space == "absolute":
            # 绝对像素：相对模型所见截图，按 视口/截图 比例缩放回视口像素
            x = point[0] * (self.vw / self.frame_w)
            y = point[1] * (self.vh / self.frame_h)
        else:
            # 归一化 0-1000
            x = point[0] / 1000.0 * self.vw
            y = point[1] / 1000.0 * self.vh
        x = max(0.0, min(x, self.vw - 1))
        y = max(0.0, min(y, self.vh - 1))
        return x, y

    @staticmethod
    def _tab_key(page: Page) -> int:
        return id(page)

    def _register_tab(self, page: Page, *, opened: bool) -> str:
        key = self._tab_key(page)
        tab_id = self._tab_ids.get(key)
        if tab_id and self._tab_pages.get(key) is page:
            return tab_id
        self._tab_seq += 1
        tab_id = f"tab_{self._tab_seq}"
        self._tab_ids[key] = tab_id
        self._tab_pages[key] = page
        if opened:
            self._opened_tab_ids.append(tab_id)
        try:
            page.on("close", lambda *_args, p=page: self._on_page_closed(p))
        except Exception:
            pass
        return tab_id

    def _on_page_created(self, page: Page) -> None:
        self._register_tab(page, opened=True)

    def _on_page_closed(self, page: Page) -> None:
        key = self._tab_key(page)
        tab_id = self._tab_ids.get(key)
        if tab_id and tab_id not in self._closed_tab_ids:
            self._closed_tab_ids.append(tab_id)
        self._tab_pages.pop(key, None)

    def _refresh_tabs(self) -> list[Page]:
        pages = list(self.context.pages)
        active = {self._tab_key(p) for p in pages}
        for candidate in pages:
            self._register_tab(candidate, opened=True)
        for key, candidate in list(self._tab_pages.items()):
            if key not in active:
                self._on_page_closed(candidate)
        if pages and self._tab_key(self.page) not in active:
            # 当前页被网站或显式 close_tab 关闭后，必须选择仍可截图的一页。
            self.page = pages[0]
        return pages

    async def browser_state(self, *, consume_events: bool = False) -> dict:
        """返回当前页、标签清单及本轮以来的新增/关闭事件。"""
        pages = self._refresh_tabs()
        tabs = []
        for candidate in pages:
            try:
                title = await candidate.title()
            except Exception:
                title = ""
            tabs.append({
                "tab_id": self._register_tab(candidate, opened=True),
                "title": title,
                "url": getattr(candidate, "url", "") or "",
                "is_current": candidate is self.page,
            })
        current_tab_id = self._tab_ids.get(self._tab_key(self.page))
        active_ids = {tab["tab_id"] for tab in tabs}
        state = {
            "current_tab_id": current_tab_id,
            "tabs": tabs,
            "opened_tab_ids": [tab_id for tab_id in self._opened_tab_ids if tab_id in active_ids],
            "closed_tab_ids": list(self._closed_tab_ids),
        }
        if consume_events:
            self._opened_tab_ids.clear()
            self._closed_tab_ids.clear()
        return state

    def _page_for_tab_id(self, tab_id: str) -> Page | None:
        self._refresh_tabs()
        for key, known_id in self._tab_ids.items():
            if known_id == tab_id:
                return self._tab_pages.get(key)
        return None

    @staticmethod
    def _failed(action: str, error: str, **detail) -> dict:
        return {"action": action, "success": False, "error": error, **detail}

    @staticmethod
    def _to_pw_hotkey(key: str) -> str:
        parts = [p for p in key.replace("+", " ").split() if p]
        out = []
        for p in parts:
            low = p.lower()
            if low in _MODIFIERS:
                out.append(_MODIFIERS[low])
            elif len(p) == 1:
                out.append(p.upper())
            else:
                out.append(p.capitalize())
        return "+".join(out)

    async def execute(self, parsed: dict) -> dict:
        """执行一个动作；恢复性失败以 success=False 返回，未知动作抛 UnknownAction。"""
        action = parsed.get("action", "unknown")
        cs = parsed.get("coord_space", "normalized")
        page = self.page
        result_detail: dict = {}

        if action == "click":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            await page.mouse.click(x, y)

        elif action == "left_double":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            await page.mouse.dblclick(x, y)

        elif action == "right_single":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            await page.mouse.click(x, y, button="right")

        elif action == "hover":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            await page.mouse.move(x, y)

        elif action == "drag":
            sx, sy = self._abs(parsed.get("start_point", [500, 500]), cs)
            ex, ey = self._abs(parsed.get("end_point", [500, 500]), cs)
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await page.mouse.move(ex, ey, steps=12)
            await page.mouse.up()

        elif action == "scroll":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            direction = parsed.get("direction", "down")
            await page.mouse.move(x, y)
            unit = int(min(self.vw, self.vh) * 0.6)
            times = max(1, min(10, int(parsed.get("scroll_amount", 1) or 1)))
            for _ in range(times):
                dx, dy = 0, 0
                if direction == "down":
                    dy = unit
                elif direction == "up":
                    dy = -unit
                elif direction == "right":
                    dx = unit
                elif direction == "left":
                    dx = -unit
                await page.mouse.wheel(dx, dy)
                await asyncio.sleep(0.1)

        elif action == "type":
            content = parsed.get("content", "")
            submit = content.endswith("\n")
            if submit:
                content = content[:-1]
            await page.keyboard.type(content)
            if submit:
                await page.keyboard.press("Enter")

        elif action == "select_all_and_type":
            content = parsed.get("content", "")
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.type(content)

        elif action == "hotkey":
            # pw_key = CU 客户端已解析好的 Playwright 键串（如 'Control+C'/'ArrowDown'）；
            # 否则按豆包文本 key（'ctrl c'）转换。
            pw_key = parsed.get("pw_key")
            if pw_key:
                await page.keyboard.press(pw_key)
            else:
                key = parsed.get("key", "")
                if key:
                    await page.keyboard.press(self._to_pw_hotkey(key))

        elif action == "open_url":
            url = parsed.get("url", "")
            if not url:
                return self._failed(action, "缺少 url 参数")
            await page.goto(url, wait_until="domcontentloaded")

        elif action == "refresh":
            await page.reload(wait_until="domcontentloaded")

        elif action == "new_tab":
            self.page = await self.context.new_page()
            self._register_tab(self.page, opened=True)
            await self.page.bring_to_front()

        elif action == "switch_tab":
            tab_id = parsed.get("tab_id")
            if tab_id:
                target = self._page_for_tab_id(tab_id)
                if target is None:
                    return self._failed(action, f"标签 {tab_id} 不存在或已关闭", tab_id=tab_id)
            elif "index" in parsed:
                # 兼容既有软协议；新提示词和环境状态只使用 tab_id。
                idx = parsed["index"]
                pages = self._refresh_tabs()
                if not (0 <= idx < len(pages)):
                    return self._failed(action, f"标签序号 {idx} 不存在", index=idx)
                target = pages[idx]
                tab_id = self._register_tab(target, opened=True)
            else:
                return self._failed(action, "缺少 tab_id 参数")
            try:
                await target.bring_to_front()
            except Exception as exc:
                return self._failed(action, f"无法切换到标签 {tab_id}: {exc}", tab_id=tab_id)
            self.page = target

        elif action == "close_tab":
            if len(self.context.pages) > 1:
                await page.close()
                self.page = self.context.pages[-1]
                await self.page.bring_to_front()
            else:
                return self._failed(action, "当前仅剩一个标签，不能关闭")

        elif action == "upload_file":
            name = parsed.get("name", "")
            if not name:
                return self._failed(action, "缺少素材文件名")
            try:
                local = self.resolve_asset(name)
            except Exception as exc:
                return self._failed(action, f"素材 {name} 不可用: {exc}", asset_name=name)
            inp = await self.page.query_selector("input[type=file]")
            if inp is None:
                return self._failed(action, f"页面未找到可用上传控件，无法上传 {name}（请先触发上传入口）", asset_name=name)
            try:
                await inp.set_input_files(local)
            except Exception as exc:
                return self._failed(action, f"上传 {name} 失败: {exc}", asset_name=name)
            result_detail["asset_name"] = name

        elif action == "wait":
            await asyncio.sleep(5)

        else:
            raise UnknownAction(action)

        await asyncio.sleep(0.4)
        return {"action": action, "success": True, **result_detail}


class UnknownAction(Exception):
    def __init__(self, action: str) -> None:
        super().__init__(f"未知动作: {action}")
        self.action = action
