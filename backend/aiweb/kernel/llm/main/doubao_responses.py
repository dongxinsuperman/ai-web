"""主 VLM · 火山方舟 Responses API（豆包，带主动式缓存 + 服务端续历史）。

由 AI Web 原 vlm_client 重构而来，落成 BaseMainVLM 契约：
- 客户端维护 previous_response_id（服务端续历史 + 命中显式缓存）+ pending_hints。
- 输出文本 DSL（Thought / Action: click(point='<point>x y</point>') ...），
  在客户端就地解析成项目统一动作链 parsed_actions（dict，坐标 normalized）。
- prompt 过大时 should_reset_session 触发分段（阈值 settings.cache_reset_threshold）。
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Optional

import httpx

from aiweb.kernel import actions as A
from aiweb.kernel.llm.base import Decision, TokenCounter
from aiweb.settings import get_settings

_RETRIABLE = (httpx.TimeoutException, httpx.TransportError)
_DEFAULT_USER_PROMPT = "What's the next step that you will do to help with the task?"


class DoubaoResponsesClient:
    def __init__(self, system_prompt: str, counter: Optional[TokenCounter] = None) -> None:
        s = get_settings()
        self.api_url = (s.vlm_api_url or "").strip()
        self.api_key = (s.vlm_api_key or "").strip()
        self.model = (s.vlm_model or "").strip()
        if not (self.api_url and self.api_key and self.model):
            raise RuntimeError("豆包 VLM 配置缺失：AIWEB_VLM_API_URL / API_KEY / MODEL")
        self.timeout = 120.0
        self.counter = counter or TokenCounter()
        self.system_prompt = system_prompt
        self.previous_response_id: Optional[str] = None
        self.pending_hints: list[str] = []
        self.segment_count = 1
        self._reset_threshold = int(s.cache_reset_threshold or 0)

    @property
    def last_prompt_tokens(self) -> int:
        return self.counter.last_prompt_tokens

    def add_hint(self, text: str) -> None:
        if text:
            self.pending_hints.append(text)

    def should_reset_session(self) -> bool:
        if not self._reset_threshold or self._reset_threshold <= 0:
            return False
        if self.previous_response_id is None:
            return False
        return self.last_prompt_tokens >= self._reset_threshold

    def reset_session(self, resume_hint: Optional[str] = None) -> Optional[str]:
        old = self.previous_response_id
        self.previous_response_id = None
        self.segment_count += 1
        if resume_hint:
            self.pending_hints.append(resume_hint)
        self.counter.last_prompt_tokens = 0
        return old

    async def _post(self, payload: dict, headers: dict, timeout: float) -> dict:
        last_exc: Optional[BaseException] = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(self.api_url, json=payload, headers=headers)
            except _RETRIABLE as e:
                last_exc = e
                if attempt + 1 >= 2:
                    raise
                await asyncio.sleep(0.5)
                continue
            if resp.status_code == 200:
                return resp.json()
            if (resp.status_code == 429 or 500 <= resp.status_code < 600) and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            raise RuntimeError(f"豆包 Responses 失败 {resp.status_code}: {resp.text[:500]}")
        raise RuntimeError(f"豆包决策异常，重试仍失败：{last_exc}")

    async def decide(self, screenshot_bytes: bytes, *, mime: str = "image/png") -> Decision:
        b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        user_content: list[dict] = [{"type": "input_text", "text": h} for h in self.pending_hints]
        user_content.append({"type": "input_image", "image_url": data_url})
        user_content.append({"type": "input_text", "text": _DEFAULT_USER_PROMPT})

        pending_backup = list(self.pending_hints)
        self.pending_hints.clear()

        is_first = self.previous_response_id is None
        input_items: list[dict] = []
        if is_first:
            input_items.append({"role": "system", "content": self.system_prompt})
        input_items.append({"role": "user", "content": user_content})

        payload: dict[str, Any] = {
            "model": self.model, "temperature": 0, "input": input_items,
            "caching": {"type": "enabled"}, "store": True, "thinking": {"type": "disabled"},
        }
        if self.previous_response_id is not None:
            payload["previous_response_id"] = self.previous_response_id
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        turn_timeout = self.timeout * 2 if (is_first and self.segment_count > 1) else self.timeout
        t0 = time.monotonic()
        try:
            data = await self._post(payload, headers, turn_timeout)
        except Exception as e:
            if pending_backup:
                self.pending_hints[:0] = pending_backup
            raise RuntimeError(f"豆包决策异常: {e.__class__.__name__}: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        content = _extract_text(data)
        new_id = data.get("id")
        if isinstance(new_id, str) and new_id:
            self.previous_response_id = new_id
        usage = self.counter.record("VLM决策", self.model, data.get("usage"))

        thought = A.extract_thought(content)
        parsed_actions = [A.parse_action(s) for s in A.extract_actions(content)]
        return Decision(
            thought=thought, parsed_actions=parsed_actions, raw_content=content,
            elapsed_ms=elapsed_ms, usage=usage,
        )


def _extract_text(data: dict) -> str:
    output = data.get("output") or []
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "assistant":
                for c in item.get("content") or []:
                    if isinstance(c, dict) and c.get("type") in ("output_text", "text") and c.get("text"):
                        return c["text"].strip()
    ot = data.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot.strip()
    raise RuntimeError("豆包 Responses 响应缺少 assistant 文本：" + str(data)[:500])
