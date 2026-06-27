"""辅助系统 · OpenAI（Chat Completions 一次性调用）。"""
from __future__ import annotations

import base64
from typing import Any, Optional

import httpx

from aiweb.kernel.llm.base import TokenCounter
from aiweb.settings import get_settings

_DEFAULT_JUDGE_SYSTEM = "You are a strict, conservative result-verification adjudicator; rely only on screenshot evidence."


class OpenAIAssistant:
    def __init__(self, counter: Optional[TokenCounter] = None) -> None:
        s = get_settings()
        self.api_url = s.assistant_api_url.strip()
        self.api_key = s.assistant_api_key_resolved.strip()
        self.model = s.assistant_model_resolved.strip()
        if not (self.api_url.startswith("http") and self.api_key and self.model):
            raise RuntimeError("辅助模型配置缺失：请配置 AIWEB_ASSISTANT_*（与主模型不同家时必须显式填 BASE_URL/API_KEY/MODEL）")
        self.counter = counter or TokenCounter()

    async def chat(self, system: str, user_content: list[dict], *, thinking: bool = False,
                   timeout: float = 60.0) -> str:
        content: list[dict] = []
        for b in user_content:
            if b.get("type") == "text":
                content.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "image":
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{b.get('mime', 'image/png')};base64,{b['b64']}"}})
        payload: dict[str, Any] = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        }
        if thinking:
            payload["reasoning_effort"] = "medium"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self.api_url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI 辅助调用失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        self.counter.record("辅助", self.model, data.get("usage"))
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            c = msg.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                return "\n".join(x.get("text", "") for x in c if isinstance(x, dict)).strip()
        raise RuntimeError("OpenAI 辅助响应缺少 message.content")

    async def verify_finished(self, *, prompt: str, prev_before_bytes: Optional[bytes],
                              final_bytes: bytes, thinking: bool = True,
                              system: Optional[str] = None) -> str:
        blocks: list[dict] = [{"type": "text", "text": prompt}]
        if prev_before_bytes:
            blocks.append({"type": "image", "b64": base64.b64encode(prev_before_bytes).decode("ascii")})
        blocks.append({"type": "image", "b64": base64.b64encode(final_bytes).decode("ascii")})
        return await self.chat(system or _DEFAULT_JUDGE_SYSTEM, blocks, thinking=thinking, timeout=90)
