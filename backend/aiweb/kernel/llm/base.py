"""多协议 LLM 适配层 · 通用契约（移植自 ai-phone shared/llm/base.py，适配 Web）。

设计原则（与 ai-phone 一致）：
1. 高冗余、低耦合：每家协议一个独立文件，互不 import；改一家不动其它家。
2. runner 单点接入：只通过 create_main_vlm / create_assistant 工厂拿实例。
3. 统一 Decision：各家把决策结果填成项目统一的"动作链"（list[dict]），
   runner 上层零分支消费。
4. Protocol 而非 ABC：鸭子类型，每家实现签名一致的方法即可。

与 ai-phone 的差异：AI Web 是浏览器自动化，动作用 dict 表示（沿用
kernel/actions.py 既有约定），坐标空间用 dict 里的 ``coord_space`` 字段标注：
- ``"normalized"``（默认，豆包系）：0-1000 归一化坐标。
- ``"absolute"``（Claude / GPT computer-use）：相对模型所见截图的绝对像素。
runner 执行前按 coord_space 反缩放到视口像素。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


# ---------------------------------------------------------------------------
# Token 统计（精简版，兼容 Chat / Responses / Anthropic 三套 usage 字段）
# ---------------------------------------------------------------------------
@dataclass
class TokenCounter:
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cached_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    call_count: int = 0
    last_prompt_tokens: int = 0

    def record(self, scene: str, model: str, usage: Optional[dict]) -> dict:
        """累计并返回本次归一化 usage（供 step 级展示）。"""
        if not usage:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}
        pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_tokens") or usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_write_tokens") or usage.get("cache_creation_input_tokens") or 0)
        cached = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
        if cache_read <= 0 and cached > 0:
            cache_read = cached
        if cached <= 0 and cache_read > 0:
            cached = cache_read
        if str(usage.get("cache_accounting") or "") == "read_write":
            tt = pt + cache_read + cache_write + ct
        else:
            tt = int(usage.get("total_tokens") or (pt + ct))

        self.total_prompt_tokens += pt
        self.total_completion_tokens += ct
        self.total_tokens += tt
        self.total_cached_tokens += cached
        self.total_cache_read_tokens += cache_read
        self.total_cache_write_tokens += cache_write
        self.call_count += 1
        self.last_prompt_tokens = pt
        return {
            "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt,
            "cached_tokens": cached, "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }

    def summary(self) -> dict:
        return {
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.total_cached_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_write_tokens": self.total_cache_write_tokens,
            "calls": self.call_count,
        }


# ---------------------------------------------------------------------------
# 决策结果
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    """单轮决策结果。

    - ``thought``：模型思考文本（中文/英文）。
    - ``parsed_actions``：已解析的动作链（list[dict]）。每项形如
      ``{"action":"click","point":[x,y],"coord_space":"absolute","raw":...}``。
      runner 优先消费它，无需再做文本解析。
    - ``usage``：本轮归一化 token 用量（来自 TokenCounter.record 的返回）。
    """

    thought: str
    parsed_actions: list[dict]
    raw_content: str = ""
    elapsed_ms: int = 0
    usage: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 协议契约
# ---------------------------------------------------------------------------
class BaseMainVLM(Protocol):
    """主 VLM 契约：runner 只关心"喂截图 → 拿 Decision"。"""

    counter: TokenCounter
    system_prompt: str

    @property
    def last_prompt_tokens(self) -> int: ...

    def add_hint(self, text: str) -> None: ...

    def should_reset_session(self) -> bool: ...

    def reset_session(self, resume_hint: Optional[str] = None) -> Optional[str]: ...

    async def decide(self, screenshot_bytes: bytes, *, mime: str = "image/png") -> Decision: ...


class BaseAssistant(Protocol):
    """辅助系统契约：一次性裁决（二次断言 / 通用文本/多模态问答）。"""

    counter: TokenCounter

    async def chat(self, system: str, user_content: list[dict], *, thinking: bool = False,
                   timeout: float = 60.0) -> str:
        """通用一次性调用。user_content 用项目内中性结构（见 assistants 适配层）。"""
        ...

    async def verify_finished(self, *, prompt: str, prev_before_bytes: Optional[bytes],
                              final_bytes: bytes, thinking: bool = True) -> str:
        """二次断言终局裁决：双图（前/后）+ 文本提示 → 原始文本（PASS/FAIL/SKIP 由调用方解析）。"""
        ...


# 中性多模态内容块约定（assistants 适配层各自翻译成本家协议）：
#   {"type": "text", "text": "..."}
#   {"type": "image", "b64": "<base64>", "mime": "image/png"}
def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(b64: str, mime: str = "image/png") -> dict:
    return {"type": "image", "b64": b64, "mime": mime}
