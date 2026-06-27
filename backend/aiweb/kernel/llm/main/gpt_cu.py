"""主 VLM · OpenAI computer-use-preview（原生 Responses API，移植自 ai-phone，适配 Web）。

要点：
- 端点 /v1/responses；服务端续历史（previous_response_id）。
- computer_use_preview 工具，environment="browser"；坐标绝对像素（coord_space=absolute）。
- 每轮回传上一次 computer_call 的 computer_call_output（携最新截图）做 ack。
- FINISHED/ASSERT_FAIL/CALL_USER + PLATFORM_ACTION 走文本协议；reasoning.effort 控推理。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Optional

import httpx

from aiweb.kernel.llm.base import Decision, TokenCounter
from aiweb.kernel.llm.main import _cu_common as CU
from aiweb.settings import get_settings

_RETRIABLE = (httpx.TimeoutException, httpx.TransportError)


class GPTComputerUseClient:
    def __init__(self, system_prompt: str, counter: Optional[TokenCounter] = None) -> None:
        s = get_settings()
        self.api_url = (s.vlm_api_url or "").strip()
        self.api_key = (s.vlm_api_key or "").strip()
        self.model = (s.vlm_model or "").strip()
        if not (self.api_url and self.api_key and self.model):
            raise RuntimeError("GPT 主 VLM 配置缺失：AIWEB_VLM_API_URL / API_KEY / MODEL")
        self.timeout = 180.0
        self.counter = counter or TokenCounter()
        self.system_prompt = system_prompt
        self.previous_response_id: Optional[str] = None
        self.pending_hints: list[str] = []
        self._last_call_id: Optional[str] = None
        self._last_safety_checks: list[dict] = []
        self.segment_count = 1
        effort = (s.vlm_main_reasoning_effort or "medium").strip().lower()
        self._reasoning_effort = effort if effort in ("low", "medium", "high") else "medium"

    @property
    def last_prompt_tokens(self) -> int:
        return self.counter.last_prompt_tokens

    def add_hint(self, text: str) -> None:
        if text:
            self.pending_hints.append(text)

    def should_reset_session(self) -> bool:
        return False

    def reset_session(self, resume_hint: Optional[str] = None) -> Optional[str]:
        old = self.previous_response_id
        self.previous_response_id = None
        self._last_call_id = None
        self._last_safety_checks = []
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
            raise RuntimeError(f"OpenAI Responses 失败 {resp.status_code}: {resp.text[:500]}")
        raise RuntimeError(f"GPT 决策异常，重试仍失败：{last_exc}")

    async def decide(self, screenshot_bytes: bytes, *, mime: str = "image/png") -> Decision:
        screen_w, screen_h = CU.decode_image_size(screenshot_bytes)
        data_url = f"data:{mime};base64,{base64.b64encode(screenshot_bytes).decode('ascii')}"
        input_items: list[dict] = []
        if self._last_call_id is not None:
            ack: dict[str, Any] = {"type": "computer_call_output", "call_id": self._last_call_id,
                                   "output": {"type": "input_image", "image_url": data_url}}
            if self._last_safety_checks:
                ack["acknowledged_safety_checks"] = self._last_safety_checks
            input_items.append(ack)
        user_content: list[dict] = [{"type": "input_text", "text": h} for h in self.pending_hints]
        user_content.append({"type": "input_image", "image_url": data_url})
        if not self.pending_hints:
            user_content.append({"type": "input_text", "text": "What's the next action?"})
        input_items.append({"role": "user", "content": user_content})

        pending_backup = list(self.pending_hints)
        self.pending_hints.clear()

        computer_tool = {"type": "computer_use_preview", "display_width": screen_w,
                         "display_height": screen_h, "environment": "browser"}
        payload: dict[str, Any] = {
            "model": self.model, "tools": [computer_tool], "input": input_items,
            "truncation": "auto", "reasoning": {"effort": self._reasoning_effort},
        }
        is_first = self.previous_response_id is None
        if is_first:
            payload["instructions"] = self.system_prompt
        else:
            payload["previous_response_id"] = self.previous_response_id
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        turn_timeout = self.timeout * 2 if (is_first and self.segment_count > 1) else self.timeout

        t0 = time.monotonic()
        try:
            data = await self._post(payload, headers, turn_timeout)
        except Exception as e:
            if pending_backup:
                self.pending_hints[:0] = pending_backup
            raise RuntimeError(f"GPT 决策异常: {e.__class__.__name__}: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        thought, computer_calls, platform_actions, finish_action = _parse_response(data)
        if computer_calls:
            self._last_call_id = computer_calls[-1].get("call_id")
            self._last_safety_checks = computer_calls[-1].get("pending_safety_checks") or []
        else:
            self._last_call_id = None
            self._last_safety_checks = []

        parsed_actions: list[dict] = list(platform_actions)
        for cc in computer_calls:
            pa = _computer_call_to_action(cc)
            if pa is not None:
                parsed_actions.append(pa)
        if finish_action is not None:
            parsed_actions.append(finish_action)
        if not parsed_actions:
            parsed_actions = [{"action": "unknown", "raw": "(empty response)", "coord_space": "absolute"}]

        usage_raw = data.get("usage") or {}
        normalized = {"input_tokens": usage_raw.get("input_tokens", 0),
                      "output_tokens": usage_raw.get("output_tokens", 0),
                      "total_tokens": usage_raw.get("total_tokens") or 0}
        details = usage_raw.get("input_tokens_details")
        if isinstance(details, dict):
            normalized["input_tokens_details"] = details
        usage = self.counter.record("VLM决策", self.model, normalized)

        new_id = data.get("id")
        if isinstance(new_id, str) and new_id:
            self.previous_response_id = new_id

        return Decision(thought=thought or "", parsed_actions=parsed_actions,
                        raw_content=json.dumps(data.get("output") or [], ensure_ascii=False),
                        elapsed_ms=elapsed_ms, usage=usage)


def _parse_response(data: dict) -> tuple[str, list[dict], list[dict], Optional[dict]]:
    items = data.get("output") or []
    if not isinstance(items, list):
        return "", [], [], None
    reasoning_parts, text_parts, computer_calls = [], [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        it = item.get("type")
        if it == "reasoning":
            for s in item.get("summary") or []:
                if isinstance(s, dict) and isinstance(s.get("text"), str):
                    reasoning_parts.append(s["text"].strip())
        elif it == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    t = c.get("text") or ""
                    if isinstance(t, str) and t.strip():
                        text_parts.append(t.strip())
        elif it == "computer_call":
            computer_calls.append(item)
    full_text = "\n".join(text_parts)
    platform_actions = CU.extract_platform_actions(full_text)
    finish_action = CU.extract_finish_action(full_text)
    cleaned = CU.strip_protocol_lines(full_text)
    thought = "\n".join(p for p in (reasoning_parts + [cleaned]) if p)
    return thought, computer_calls, platform_actions, finish_action


def _computer_call_to_action(cc: dict) -> Optional[dict]:
    obj = cc.get("action") or {}
    if not isinstance(obj, dict):
        return None
    atype = (obj.get("type") or "").strip()
    raw = f"computer.{atype}({json.dumps(obj, ensure_ascii=False)})"

    def _xy(o: dict) -> Optional[list]:
        try:
            return [int(o.get("x")), int(o.get("y"))]
        except (TypeError, ValueError):
            return None

    if atype == "click":
        pt = _xy(obj)
        if pt is None:
            return None
        btn = (obj.get("button") or "left").lower()
        act = "right_single" if btn == "right" else "click"
        return {"action": act, "point": pt, "coord_space": "absolute", "raw": raw}
    if atype == "double_click":
        pt = _xy(obj)
        return None if pt is None else {"action": "left_double", "point": pt, "coord_space": "absolute", "raw": raw}
    if atype == "scroll":
        pt = _xy(obj) or [500, 500]
        try:
            sx, sy = int(obj.get("scroll_x") or 0), int(obj.get("scroll_y") or 0)
        except (TypeError, ValueError):
            sx = sy = 0
        if abs(sy) >= abs(sx):
            direction, mag = ("down" if sy > 0 else "up"), abs(sy)
        else:
            direction, mag = ("right" if sx > 0 else "left"), abs(sx)
        amount = max(1, min(10, int(round(mag / 100)))) if mag else 1
        return {"action": "scroll", "point": pt, "direction": direction,
                "scroll_amount": amount, "coord_space": "absolute", "raw": raw}
    if atype == "type":
        return {"action": "type", "content": str(obj.get("text") or ""), "raw": raw}
    if atype == "keypress":
        keys = obj.get("keys") or []
        if not isinstance(keys, list) or not keys:
            return None
        pw = CU.keys_to_pw([str(k) for k in keys])
        return None if pw is None else {"action": "hotkey", "pw_key": pw, "raw": raw}
    if atype in ("wait", "screenshot"):
        return {"action": "wait", "raw": raw}
    if atype == "drag":
        path = obj.get("path") or []
        if not isinstance(path, list) or len(path) < 2:
            return None
        sp, ep = _xy(path[0]) if isinstance(path[0], dict) else None, _xy(path[-1]) if isinstance(path[-1], dict) else None
        if sp is None or ep is None:
            return None
        return {"action": "drag", "start_point": sp, "end_point": ep, "coord_space": "absolute", "raw": raw}
    return None
