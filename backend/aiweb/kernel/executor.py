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

    async def _sync_to_newest_tab(self, before: int) -> bool:
        """点击可能开新标签：若 pages 增加则切到最新页。返回是否切换。"""
        await asyncio.sleep(0.5)
        if len(self.context.pages) > before:
            self.page = self.context.pages[-1]
            try:
                await self.page.bring_to_front()
            except Exception:
                pass
            return True
        return False

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
        """执行一个动作；返回 {action, new_tab: bool}。未知动作抛 UnknownAction。"""
        action = parsed.get("action", "unknown")
        cs = parsed.get("coord_space", "normalized")
        new_tab = False
        page = self.page

        if action == "click":
            x, y = self._abs(parsed.get("point", [500, 500]), cs)
            before = len(self.context.pages)
            await page.mouse.click(x, y)
            new_tab = await self._sync_to_newest_tab(before)

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
            if url:
                await page.goto(url, wait_until="domcontentloaded")

        elif action == "refresh":
            await page.reload(wait_until="domcontentloaded")

        elif action == "new_tab":
            self.page = await self.context.new_page()
            new_tab = True

        elif action == "switch_tab":
            idx = parsed.get("index", 0)
            if 0 <= idx < len(self.context.pages):
                self.page = self.context.pages[idx]
                await self.page.bring_to_front()

        elif action == "close_tab":
            if len(self.context.pages) > 1:
                await page.close()
                self.page = self.context.pages[-1]
                await self.page.bring_to_front()

        elif action == "upload_file":
            name = parsed.get("name", "")
            local = self.resolve_asset(name)
            inp = await self.page.query_selector("input[type=file]")
            if inp is None:
                raise RuntimeError(f"页面未找到 input[type=file]，无法上传 {name}（请先触发上传控件）")
            await inp.set_input_files(local)

        elif action == "wait":
            await asyncio.sleep(5)

        else:
            raise UnknownAction(action)

        await asyncio.sleep(0.4)
        return {"action": action, "new_tab": new_tab}


class UnknownAction(Exception):
    def __init__(self, action: str) -> None:
        super().__init__(f"未知动作: {action}")
        self.action = action
