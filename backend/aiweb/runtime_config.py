"""运行期热配置读取。"""
from __future__ import annotations

from aiweb.db import session_scope
from aiweb.models.config import ConfigKV
from aiweb.settings import get_settings

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


def parse_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSEY:
        return False
    return default


def format_bool(value, *, default: bool = False) -> str:
    return "true" if parse_bool(value, default=default) else "false"


async def get_headless() -> bool:
    """读取全局 headless 热配置；未配置时回落到环境默认值。"""
    async with session_scope() as s:
        cfg = await s.get(ConfigKV, "headless")
        if cfg is not None:
            return parse_bool(cfg.value, default=get_settings().headless)
    return get_settings().headless
