"""浏览器槽位：每个引擎几台。统一解析/读取，供调度、设备端点、校验复用。

模型：不再是"一个全局并发数"，而是 {引擎: 台数}（像 ai-phone：几个端有几台）。
- 总并发 = 各引擎台数之和；
- 某引擎台数=0 即不启用（提交该引擎会被拒，调度也不会接）。
存储：ConfigKV["browser_slots"] 存节点容量 JSON。

Agent 分支只支持节点形态：{"mac-01": {"chrome": 1}, "win-01": {"chrome": 6}}。
Server 不再把浏览器任务落到本机 worker；本机测试也要启动一个 Agent。
对外接口继续使用聚合后的 {引擎: 台数}。
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
    """供对外设备/准入等旧调用点读取聚合容量；非节点配置返回空。"""
    return flatten_slots(parse_node_slots(text))


def _normalize_engine_counts(data: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in data.items():
        eng = canon(str(k))
        try:
            n = int(v)
        except Exception:
            continue
        if n > 0:
            out[eng] = out.get(eng, 0) + n
    return out


def parse_node_slots(text: str | None) -> dict[str, dict[str, int]]:
    """解析节点容量配置；只接受 {node:{engine:count}}。"""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, dict[str, int]] = {}
    for node, raw_slots in data.items():
        node_id = str(node or "").strip()
        if not node_id or not isinstance(raw_slots, dict):
            continue
        slots = _normalize_engine_counts(raw_slots)
        if slots:
            out[node_id] = slots
    return out


def default_slots() -> dict[str, int]:
    return flatten_slots(default_node_slots())


def default_node_slots() -> dict[str, dict[str, int]]:
    return parse_node_slots(get_settings().browser_slots)


def flatten_slots(nodes: dict[str, dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for slots in nodes.values():
        for eng, n in slots.items():
            if n > 0:
                out[eng] = out.get(eng, 0) + int(n)
    return out


def slots_json(m: dict[str, int]) -> str:
    """按固定引擎顺序输出全量 JSON（未启用=0），便于前端渲染。"""
    return json.dumps({e: int(m.get(e, 0)) for e in ENGINES})


def node_slots_json(m: dict[str, dict[str, int]]) -> str:
    """规范化节点容量 JSON；每个节点下按固定引擎顺序输出。"""
    out: dict[str, dict[str, int]] = {}
    for node in sorted(m.keys()):
        out[node] = {e: int((m.get(node) or {}).get(e, 0)) for e in ENGINES}
    return json.dumps(out)


def normalize_slots_value(value) -> str:
    """配置入口统一规范化；只支持节点对象 {node:{engine:count}}。"""
    if isinstance(value, dict):
        nodes: dict[str, dict[str, int]] = {}
        for node, raw_slots in value.items():
            node_id = str(node or "").strip()
            if node_id and isinstance(raw_slots, dict):
                slots = _normalize_engine_counts(raw_slots)
                if slots:
                    nodes[node_id] = slots
        return node_slots_json(nodes)
    text = str(value)
    return node_slots_json(parse_node_slots(text))


async def get_node_slots(session) -> dict[str, dict[str, int]]:
    """当前生效 Agent 节点容量。无配置行时返回空。"""
    cfg = await session.get(ConfigKV, "browser_slots")
    if cfg and cfg.value:
        return parse_node_slots(cfg.value)
    return default_node_slots()


async def get_slots(session) -> dict[str, int]:
    """当前生效槽位（热配优先，缺省取 settings）。返回 {引擎: 台数(>0)}。

    注意：配置行一旦存在就尊重其结果——即使全为 0（解析后为空）也表示"全部停用"，
    不再回退默认；仅当没有配置行（或值为空串）时才取 settings 默认。
    """
    return flatten_slots(await get_node_slots(session))
