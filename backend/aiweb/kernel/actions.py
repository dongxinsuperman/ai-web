"""动作解析与归一化。

设计原则：承接而非固定。parser 通用解析 `fn_name(params)`，不预设动作名；
ACTION_ALIASES 把模型各种写法归一到 canonical 动作；未知动作交由 runner 优雅兜底。
参考字节 UI-TARS-desktop 的 actionTypeMap 做法。
"""
from __future__ import annotations

import json
import re

# canonical 动作名 → 同义写法集合。归一化时反查。
_ALIAS_GROUPS: dict[str, list[str]] = {
    "click": ["click", "left_click", "left_single", "leftclick", "leftsingle", "tap"],
    "left_double": ["left_double", "double_click", "doubleclick", "leftdouble", "double_tap"],
    "right_single": ["right_single", "right_click", "rightclick", "rightsingle"],
    "hover": ["hover", "move", "move_to", "mouse_move", "moveto", "mousemove"],
    "drag": ["drag", "left_click_drag", "leftclickdrag", "swipe"],
    "scroll": ["scroll", "wheel"],
    "type": ["type", "input", "text"],
    "select_all_and_type": ["select_all_and_type", "clear_and_type", "replace_text"],
    "hotkey": ["hotkey", "key", "key_press", "keypress", "press", "shortcut"],
    "wait": ["wait", "sleep"],
    "open_url": ["open_url", "goto", "navigate", "open"],
    "refresh": ["refresh", "reload"],
    "new_tab": ["new_tab", "newtab", "open_tab"],
    "switch_tab": ["switch_tab", "switchtab", "select_tab"],
    "close_tab": ["close_tab", "closetab"],
    "upload_file": ["upload_file", "upload", "set_input_files", "uploadfile"],
    "finished": ["finished", "done", "complete", "finish"],
    "assert_fail": ["assert_fail", "fail", "assertion_failed", "assert_failed"],
    "call_user": ["call_user", "calluser", "ask_user", "need_human"],
}

# 同义写法 → canonical
ACTION_ALIASES: dict[str, str] = {
    alias.lower(): canonical for canonical, aliases in _ALIAS_GROUPS.items() for alias in aliases
}

# 终态动作
TERMINAL_ACTIONS = {"finished", "assert_fail", "call_user"}
# 带坐标点的动作
POINT_ACTIONS = {"click", "left_double", "right_single", "hover", "scroll"}


def normalize_action(name: str) -> str:
    """归一化动作名；未知则原样小写返回（交由 runner 兜底）。"""
    return ACTION_ALIASES.get(name.strip().lower(), name.strip().lower())


def extract_thought(content: str) -> str:
    m = re.search(r"Thought:\s*(.+?)(?=\nAction:|$)", content, re.DOTALL)
    return m.group(1).strip() if m else ""


def extract_action(content: str) -> str:
    m = re.search(r"Action:\s*(.+)", content, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 没有 Action 段：兜底为 finished，避免崩溃
    return f"finished(content='无法解析决策输出: {content[:120]}')"


def extract_actions(content: str) -> list[str]:
    """抽取所有 `Action:` 行（按出现顺序），支持同一 Thought 下的链式动作。"""
    if not content:
        return [extract_action(content)]
    matches = [m.strip() for m in re.findall(r"^\s*Action:\s*(.+?)\s*$", content, re.MULTILINE) if m.strip()]
    if matches:
        return matches
    return [extract_action(content)]


def _extract_point(s: str) -> list[int] | None:
    m = re.search(r"<point>\s*(\d+)\s+(\d+)\s*</point>", s)
    if m:
        return [int(m.group(1)), int(m.group(2))]
    # 兼容 (x,y) / [x,y] / x1 y1 等写法
    m = re.search(r"[\(\[]\s*(\d+)\s*[,\s]\s*(\d+)\s*[\)\]]", s)
    if m:
        return [int(m.group(1)), int(m.group(2))]
    return None


def parse_action(action_str: str) -> dict:
    """把模型输出的 Action 文本解析为统一动作对象。

    返回示例：{"action": "click", "point": [500, 800], "raw": "click(point=...)"}
    """
    raw = action_str.strip()
    fn_match = re.search(r"(\w+)\s*\((.*)\)\s*$", raw, re.DOTALL)
    if not fn_match:
        # 非函数式：尝试裸动作名（如 "wait" / "finished"）
        bare = re.match(r"^(\w+)\s*$", raw)
        if bare:
            return {"action": normalize_action(bare.group(1)), "raw": raw}
        return {"action": "finished", "content": f"无法解析 Action: {raw[:120]}", "raw": raw}

    action = normalize_action(fn_match.group(1))
    params = fn_match.group(2).strip()
    result: dict = {"action": action, "raw": raw}

    if not params:
        return result

    if action in POINT_ACTIONS:
        pt = _extract_point(params)
        if pt:
            result["point"] = pt

    if action == "scroll":
        dm = re.search(r"direction\s*=\s*'([^']*)'", params) or re.search(r"direction\s*=\s*\"([^\"]*)\"", params)
        if dm:
            result["direction"] = dm.group(1)

    if action == "drag":
        points = re.findall(r"<point>\s*(\d+)\s+(\d+)\s*</point>", params)
        if len(points) >= 2:
            result["start_point"] = [int(points[0][0]), int(points[0][1])]
            result["end_point"] = [int(points[1][0]), int(points[1][1])]

    if action in ("type", "select_all_and_type", "finished", "assert_fail", "call_user"):
        cm = re.search(r"content\s*=\s*'(.*)'", params, re.DOTALL) or re.search(
            r"content\s*=\s*\"(.*)\"", params, re.DOTALL
        )
        if cm:
            result["content"] = cm.group(1).replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")

    if action == "hotkey":
        km = re.search(r"key\s*=\s*'([^']*)'", params) or re.search(r"key\s*=\s*\"([^\"]*)\"", params)
        if km:
            result["key"] = km.group(1)

    if action == "open_url":
        um = re.search(r"url\s*=\s*'([^']*)'", params) or re.search(r"url\s*=\s*\"([^\"]*)\"", params)
        if um:
            result["url"] = um.group(1)

    if action == "switch_tab":
        tm = re.search(r"tab_id\s*=\s*'([^']*)'", params) or re.search(
            r'tab_id\s*=\s*"([^\"]*)"', params
        )
        if tm and tm.group(1).strip():
            result["tab_id"] = tm.group(1).strip()
        im = re.search(r"index\s*=\s*'?(\d+)'?", params)
        if im:
            result["index"] = int(im.group(1))

    if action == "upload_file":
        nm = re.search(r"name\s*=\s*'([^']*)'", params) or re.search(r"name\s*=\s*\"([^\"]*)\"", params)
        if nm:
            result["name"] = nm.group(1)

    return result


def safe_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
