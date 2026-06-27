"""浏览器槽位：每个引擎几台。统一解析/读取，供调度、设备端点、校验复用。

模型：不再是"一个全局并发数"，而是 {引擎: 台数}（像 ai-phone：几个端有几台）。
- 总并发 = 各引擎台数之和；
- 某引擎台数=0 即不启用（提交该引擎会被拒，调度也不会接）。
存储：ConfigKV["browser_slots"] 存规范化 JSON（含 0）；缺省取 settings.browser_slots。
"""
from __future__ import annotations

import json

from aiweb.models.config import ConfigKV
from aiweb.settings import get_settings

# 规范引擎名（platform 别名 → 引擎）
CANON = {
    "chrome": "chrome", "chromium": "chrome",
    "firefox": "firefox",
    "webkit": "webkit", "safari": "webkit",
}
# 固定展示顺序与展示信息（label 给人看，brand 给设备端点）
ENGINES = ["chrome", "firefox", "webkit"]
META = {
    "chrome": ("Chrome", "Chromium"),
    "firefox": ("Firefox", "Firefox"),
    "webkit": ("Safari", "WebKit"),
}


def canon(platform: str | None) -> str:
    return CANON.get((platform or "chrome").strip().lower(), "chrome")


def parse_slots(text: str | None) -> dict[str, int]:
    """解析 'chrome:2,firefox:1,webkit:1' 或 JSON；返回 {引擎: 台数(>0)}（丢弃 0）。"""
    text = (text or "").strip()
    if not text:
        return {}
    pairs: list[tuple] = []
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                pairs = list(data.items())
        except Exception:
            pairs = []
    if not pairs:
        for part in text.split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                pairs.append((k, v))
    out: dict[str, int] = {}
    for k, v in pairs:
        eng = canon(str(k))
        try:
            n = int(v)
        except Exception:
            continue
        if n > 0:
            out[eng] = out.get(eng, 0) + n
    return out


def default_slots() -> dict[str, int]:
    return parse_slots(get_settings().browser_slots)


def slots_json(m: dict[str, int]) -> str:
    """按固定引擎顺序输出全量 JSON（未启用=0），便于前端渲染。"""
    return json.dumps({e: int(m.get(e, 0)) for e in ENGINES})


async def get_slots(session) -> dict[str, int]:
    """当前生效槽位（热配优先，缺省取 settings）。返回 {引擎: 台数(>0)}。

    注意：配置行一旦存在就尊重其结果——即使全为 0（解析后为空）也表示"全部停用"，
    不再回退默认；仅当没有配置行（或值为空串）时才取 settings 默认。
    """
    cfg = await session.get(ConfigKV, "browser_slots")
    if cfg and cfg.value:
        return parse_slots(cfg.value)
    return default_slots()
