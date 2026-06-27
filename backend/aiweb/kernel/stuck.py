"""朴素本地卡死检测（V1）。"""
from __future__ import annotations

CLICK_STUCK_THRESHOLD = 4
SCROLL_STUCK_THRESHOLD = 3


class StuckDetector:
    def __init__(self) -> None:
        self.recent_clicks: list[list[int]] = []
        self.scroll_streak = {"direction": None, "count": 0}

    def check_click(self, parsed: dict) -> str | None:
        if parsed.get("action") != "click" or not parsed.get("point"):
            return None
        point = parsed["point"]
        self.recent_clicks.append(point)
        if len(self.recent_clicks) > CLICK_STUCK_THRESHOLD:
            self.recent_clicks.pop(0)
        if len(self.recent_clicks) >= CLICK_STUCK_THRESHOLD:
            first = self.recent_clicks[0]
            if all(abs(p[0] - first[0]) < 30 and abs(p[1] - first[1]) < 30 for p in self.recent_clicks):
                self.recent_clicks.clear()
                return (
                    f"注意：你已连续 {CLICK_STUCK_THRESHOLD} 次点击几乎相同的位置但似乎无效。"
                    "请换方式：滚动查找目标 / 点击元素其他区域 / 检查是否有弹窗遮挡。"
                )
        return None

    def check_scroll(self, parsed: dict) -> str | None:
        if parsed.get("action") != "scroll":
            self.scroll_streak = {"direction": None, "count": 0}
            return None
        direction = parsed.get("direction", "down")
        if direction == self.scroll_streak["direction"]:
            self.scroll_streak["count"] += 1
        else:
            self.scroll_streak = {"direction": direction, "count": 1}
        if self.scroll_streak["count"] >= SCROLL_STUCK_THRESHOLD:
            self.scroll_streak["count"] = 0
            return (
                f"注意：你已连续 {SCROLL_STUCK_THRESHOLD} 次向 {direction} 滚动。"
                "若目标不在该方向，请尝试反方向滚动或换操作。"
            )
        return None
