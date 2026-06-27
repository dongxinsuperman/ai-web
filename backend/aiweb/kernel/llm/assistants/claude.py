"""辅助系统 · Claude（Anthropic Messages API 一次性调用）。"""
from __future__ import annotations

import base64
from typing import Any, Optional

import httpx

from aiweb.kernel.llm.base import TokenCounter
from aiweb.settings import get_settings

_DEFAULT_JUDGE_SYSTEM = "You are a strict, conservative result-verification adjudicator; rely only on screenshot evidence."


class ClaudeAssistant:
    def __init__(self, counter: Optional[TokenCounter] = None) -> None:
        s = get_settings()
        self.api_url = s.assistant_api_url.strip()
        self.api_key = s.assistant_api_key_resolved.strip()
        self.model = s.assistant_model_resolved.strip()
        if not (self.api_url.startswith("http") and self.api_key and self.model):
            raise RuntimeError("辅助模型配置缺失：请配置 AIWEB_ASSISTANT_*（与主模型不同家时必须显式填 BASE_URL/API_KEY/MODEL）")
        self._thinking_budget = max(0, int(s.vlm_main_thinking_budget or 0))
        self.counter = counter or TokenCounter()

    async def chat(self, system: str, user_content: list[dict], *, thinking: bool = False,
                   timeout: float = 60.0) -> str:
        content: list[dict] = []
        for b in user_content:
            if b.get("type") == "text":
                content.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "image":
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": b.get("mime", "image/png"), "data": b["b64"]}})
        payload: dict[str, Any] = {
            "model": self.model, "max_tokens": 8192, "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        if thinking and self._thinking_budget > 0:
            payload["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget}
            payload["temperature"] = 1
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                   "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self.api_url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Claude 辅助调用失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usage") or {}
        self.counter.record("辅助", self.model, {
            "cache_accounting": "read_write",
            "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        })
        parts = [b.get("text", "") for b in (data.get("content") or [])
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()

    async def verify_finished(self, *, prompt: str, prev_before_bytes: Optional[bytes],
                              final_bytes: bytes, thinking: bool = True,
                              system: Optional[str] = None) -> str:
        blocks: list[dict] = [{"type": "text", "text": prompt}]
        if prev_before_bytes:
            blocks.append({"type": "image", "b64": base64.b64encode(prev_before_bytes).decode("ascii")})
        blocks.append({"type": "image", "b64": base64.b64encode(final_bytes).decode("ascii")})
        return await self.chat(system or _DEFAULT_JUDGE_SYSTEM, blocks, thinking=thinking, timeout=120)
