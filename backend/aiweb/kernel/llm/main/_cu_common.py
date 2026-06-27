"""Claude / GPT computer-use 共用工具：截图尺寸、键名映射、文本协议解析。

与 ai-phone 不同：AI Web 是浏览器，按键映射到 Playwright 键名；PLATFORM_ACTION
承载浏览器导航（open_url / new_tab / switch_tab / close_tab / refresh /
upload_file），内联调用直接复用 kernel/actions.parse_action 解析。
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Optional

from aiweb.kernel import actions as A
from aiweb.settings import get_settings

# 终态文本协议（行首，容错全角冒号）
FINISHED_RE = re.compile(r"^\s*FINISHED\s*[:：]\s*(.*)$", re.IGNORECASE | re.MULTILINE)
ASSERT_FAIL_RE = re.compile(r"^\s*ASSERT_FAIL\s*[:：]\s*(.*)$", re.IGNORECASE | re.MULTILINE)
CALL_USER_RE = re.compile(r"^\s*CALL_USER\s*[:：]\s*(.*)$", re.IGNORECASE | re.MULTILINE)
# 浏览器导航文本协议：PLATFORM_ACTION: open_url(url='...') 等
PLATFORM_ACTION_RE = re.compile(r"^\s*PLATFORM_ACTION\s*[:：]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
# 允许通过 PLATFORM_ACTION 触发的浏览器级动作（computer tool 做不到）
WEB_PLATFORM_WHITELIST = frozenset(
    {"open_url", "refresh", "new_tab", "switch_tab", "close_tab", "upload_file"}
)

# X11/xdotool 风格键名 → Playwright 键名
_X11_TO_PW = {
    "return": "Enter", "enter": "Enter", "tab": "Tab", "backspace": "Backspace",
    "delete": "Delete", "space": "Space", "up": "ArrowUp", "down": "ArrowDown",
    "left": "ArrowLeft", "right": "ArrowRight", "page_up": "PageUp", "page_down": "PageDown",
    "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End",
    "escape": "Escape", "esc": "Escape",
}
_MODIFIERS = {
    "ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt",
    "meta": "Meta", "cmd": "Meta", "command": "Meta", "super": "Meta",
}


def decode_image_size(image_bytes: bytes) -> tuple[int, int]:
    """从截图 bytes 解码 (w,h)；失败回退到视口尺寸。"""
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            return int(img.width), int(img.height)
    except Exception:
        pass
    try:
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(image_bytes[16:20], "big"), int.from_bytes(image_bytes[20:24], "big")
        if image_bytes[:3] == b"\xff\xd8\xff":
            i = 2
            while i < len(image_bytes) - 8:
                if image_bytes[i] == 0xFF and image_bytes[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3):
                    h = (image_bytes[i + 5] << 8) | image_bytes[i + 6]
                    w = (image_bytes[i + 7] << 8) | image_bytes[i + 8]
                    return int(w), int(h)
                i += 1
    except Exception:
        pass
    return get_settings().viewport_size


def key_to_pw(name: str) -> Optional[str]:
    """单个键名 → Playwright 键名；不认识返回 None。"""
    if not name:
        return None
    low = name.strip().lower()
    if low in _X11_TO_PW:
        return _X11_TO_PW[low]
    if low in _MODIFIERS:
        return _MODIFIERS[low]
    if len(low) == 1:
        return low.upper()
    return None


def keys_to_pw(keys: list[str]) -> Optional[str]:
    """组合键列表 → Playwright 组合串（如 ['ctrl','c'] → 'Control+C'）。"""
    if not keys:
        return None
    parts = []
    for k in keys:
        pw = key_to_pw(str(k))
        if pw is None:
            return None
        parts.append(pw)
    return "+".join(parts)


def extract_platform_actions(full_text: str) -> list[dict]:
    """从文本里抽取 PLATFORM_ACTION 行 → 浏览器动作 dict（白名单校验）。"""
    out: list[dict] = []
    for m in PLATFORM_ACTION_RE.finditer(full_text):
        inner = m.group(1).strip()
        parsed = A.parse_action(inner)
        if parsed.get("action") in WEB_PLATFORM_WHITELIST:
            parsed["raw"] = f"PLATFORM_ACTION: {inner}"
            out.append(parsed)
    return out


def extract_finish_action(full_text: str) -> Optional[dict]:
    """扫 ASSERT_FAIL > CALL_USER > FINISHED 关键字，返回终态动作 dict。"""
    m = ASSERT_FAIL_RE.search(full_text)
    if m:
        reason = m.group(1).strip() or "assert_fail（无原因）"
        return {"action": "assert_fail", "content": reason, "raw": f"assert_fail(content='{reason}')",
                "coord_space": "absolute"}
    m = CALL_USER_RE.search(full_text)
    if m:
        reason = m.group(1).strip() or "需要人工介入"
        return {"action": "call_user", "content": reason, "raw": f"call_user(content='{reason}')",
                "coord_space": "absolute"}
    m = FINISHED_RE.search(full_text)
    if m:
        reason = m.group(1).strip() or "finished"
        return {"action": "finished", "content": reason, "raw": f"finished(content='{reason}')",
                "coord_space": "absolute"}
    return None


def strip_protocol_lines(full_text: str) -> str:
    """从 thought 文本里剥掉所有协议关键字行。"""
    t = PLATFORM_ACTION_RE.sub("", full_text)
    t = ASSERT_FAIL_RE.sub("", t)
    t = CALL_USER_RE.sub("", t)
    t = FINISHED_RE.sub("", t)
    return t.strip()
