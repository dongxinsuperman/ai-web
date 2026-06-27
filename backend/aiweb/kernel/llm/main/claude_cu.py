"""主 VLM · Anthropic Claude Computer Use（原生 Messages API，移植自 ai-phone，适配 Web）。

要点（与 ai-phone 一致）：
- 原生端点 + ``anthropic-beta: computer-use-2025-01-24``；客户端维护 messages 滑窗。
- 每轮按截图实际尺寸动态声明 computer 工具；坐标是绝对像素（coord_space=absolute）。
- 多 tool_use 块 → 动作链；FINISHED/ASSERT_FAIL/CALL_USER + PLATFORM_ACTION 走文本协议。
- thinking（budget）/ prompt caching 可选。
Web 适配：动作映射到浏览器动作 dict；PLATFORM_ACTION 承载浏览器导航。
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from typing import Any, Optional

import httpx

from aiweb.kernel.llm.base import Decision, TokenCounter
from aiweb.kernel.llm.main import _cu_common as CU
from aiweb.settings import get_settings

_RETRIABLE = (httpx.TimeoutException, httpx.TransportError)
COMPUTER_TOOL_TYPE = "computer_20250124"
COMPUTER_USE_BETA = "computer-use-2025-01-24"


class ClaudeComputerUseClient:
    def __init__(self, system_prompt: str, counter: Optional[TokenCounter] = None) -> None:
        s = get_settings()
        self.api_url = (s.vlm_api_url or "").strip()
        self.api_key = (s.vlm_api_key or "").strip()
        self.model = (s.vlm_model or "").strip()
        if not (self.api_url and self.api_key and self.model):
            raise RuntimeError("Claude 主 VLM 配置缺失：AIWEB_VLM_API_URL / API_KEY / MODEL")
        self.timeout = 120.0
        self.counter = counter or TokenCounter()
        self.system_prompt = system_prompt
        self.messages: list[dict] = []
        self.pending_hints: list[str] = []
        self._history_window_steps = max(1, int(s.vlm_history_window_steps or 12))
        self._thinking_budget = max(0, int(s.vlm_main_thinking_budget or 0))
        self._prompt_caching = bool(s.vlm_main_prompt_caching_enabled)
        self.segment_count = 1

    @property
    def last_prompt_tokens(self) -> int:
        return self.counter.last_prompt_tokens

    def add_hint(self, text: str) -> None:
        if text:
            self.pending_hints.append(text)

    def should_reset_session(self) -> bool:
        return False  # Anthropic 无服务端会话；客户端滑窗即分段

    def reset_session(self, resume_hint: Optional[str] = None) -> Optional[str]:
        old = len(self.messages)
        self.messages.clear()
        if resume_hint:
            self.pending_hints.append(resume_hint)
        return f"cleared-{old}-msgs" if old else None

    async def _post(self, payload: dict, headers: dict) -> dict:
        last_exc: Optional[BaseException] = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
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
            raise RuntimeError(f"Claude Messages 失败 {resp.status_code}: {resp.text[:500]}")
        raise RuntimeError(f"Claude 决策异常，重试仍失败：{last_exc}")

    async def decide(self, screenshot_bytes: bytes, *, mime: str = "image/png") -> Decision:
        screen_w, screen_h = CU.decode_image_size(screenshot_bytes)
        image_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime,
                       "data": base64.b64encode(screenshot_bytes).decode("ascii")},
        }
        prev_ids = self._extract_prev_tool_use_ids()
        user_blocks: list[dict] = []
        for idx, tu_id in enumerate(prev_ids):
            if idx == 0:
                user_blocks.append({"type": "tool_result", "tool_use_id": tu_id, "content": [image_block]})
            else:
                user_blocks.append({"type": "tool_result", "tool_use_id": tu_id,
                                    "content": [{"type": "text", "text": "ok"}]})
        for hint in self.pending_hints:
            user_blocks.append({"type": "text", "text": hint})
        if not prev_ids:
            user_blocks.append(image_block)

        pending_backup = list(self.pending_hints)
        self.pending_hints.clear()

        new_user = {"role": "user", "content": user_blocks}
        request_messages = self._trimmed_messages() + [new_user]

        computer_tool: dict[str, Any] = {
            "type": COMPUTER_TOOL_TYPE, "name": "computer",
            "display_width_px": screen_w, "display_height_px": screen_h, "display_number": 1,
        }
        if self._prompt_caching:
            system_field: Any = [{"type": "text", "text": self.system_prompt,
                                  "cache_control": {"type": "ephemeral"}}]
            computer_tool["cache_control"] = {"type": "ephemeral"}
            request_messages = _mark_cache_breakpoint(request_messages)
        else:
            system_field = self.system_prompt

        payload: dict[str, Any] = {
            "model": self.model, "max_tokens": 8192, "system": system_field,
            "messages": request_messages, "tools": [computer_tool], "tool_choice": {"type": "auto"},
        }
        if self._thinking_budget > 0:
            payload["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget}
            payload["temperature"] = 1
        headers = {
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            "anthropic-beta": COMPUTER_USE_BETA, "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            data = await self._post(payload, headers)
        except Exception as e:
            if pending_backup:
                self.pending_hints[:0] = pending_backup
            raise RuntimeError(f"Claude 决策异常: {e.__class__.__name__}: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        thought, tool_uses, platform_actions, finish_action = _parse_response(data)
        parsed_actions: list[dict] = list(platform_actions)
        for tu in tool_uses:
            pa = _tool_use_to_action(tu, screen_w, screen_h)
            if pa is not None:
                parsed_actions.append(pa)
        if finish_action is not None:
            parsed_actions.append(finish_action)
        if not parsed_actions:
            parsed_actions = [{"action": "unknown", "raw": "(empty response)", "coord_space": "absolute"}]

        usage_raw = data.get("usage") or {}
        normalized = {
            "cache_accounting": "read_write",
            "input_tokens": usage_raw.get("input_tokens", 0),
            "output_tokens": usage_raw.get("output_tokens", 0),
        }
        if usage_raw.get("cache_read_input_tokens") is not None:
            normalized["cache_read_tokens"] = int(usage_raw["cache_read_input_tokens"])
            normalized["input_tokens_details"] = {"cached_tokens": int(usage_raw["cache_read_input_tokens"])}
        if usage_raw.get("cache_creation_input_tokens") is not None:
            normalized["cache_write_tokens"] = int(usage_raw["cache_creation_input_tokens"])
        usage = self.counter.record("VLM决策", self.model, normalized)

        self.messages.append(new_user)
        self.messages.append({"role": "assistant", "content": data.get("content") or []})

        return Decision(thought=thought or "", parsed_actions=parsed_actions,
                        raw_content=json.dumps(data.get("content") or [], ensure_ascii=False),
                        elapsed_ms=elapsed_ms, usage=usage)

    def _extract_prev_tool_use_ids(self) -> list[str]:
        if not self.messages:
            return []
        last = self.messages[-1]
        if not isinstance(last, dict) or last.get("role") != "assistant":
            return []
        return [b["id"] for b in (last.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("id"), str)]

    def _trimmed_messages(self) -> list[dict]:
        if not self.messages:
            return []
        max_keep = self._history_window_steps * 2
        if len(self.messages) <= max_keep:
            return list(self.messages)
        head_user, head_asst = self.messages[0], self.messages[1]
        tail_count = max_keep - 2
        tail_start = len(self.messages) - tail_count
        if tail_start % 2 != 0:
            tail_start -= 1
        tail = list(self.messages[tail_start:])
        sanitized_asst = copy.deepcopy(head_asst)
        if isinstance(sanitized_asst.get("content"), list):
            sanitized_asst["content"] = [b for b in sanitized_asst["content"]
                                         if not (isinstance(b, dict) and b.get("type") == "tool_use")]
            if not sanitized_asst["content"]:
                sanitized_asst["content"] = [{"type": "text", "text": "(action executed)"}]
        sanitized_tail_user = copy.deepcopy(tail[0])
        if isinstance(sanitized_tail_user.get("content"), list):
            sanitized_tail_user["content"] = [b for b in sanitized_tail_user["content"]
                                              if not (isinstance(b, dict) and b.get("type") == "tool_result")]
            if not sanitized_tail_user["content"]:
                sanitized_tail_user["content"] = [{"type": "text", "text": "(continuing)"}]
        tail[0] = sanitized_tail_user
        return [head_user, sanitized_asst] + tail


def _mark_cache_breakpoint(messages: list[dict]) -> list[dict]:
    if len(messages) < 2:
        return messages
    out = list(messages)
    target = copy.deepcopy(out[len(out) - 2])
    content = target.get("content")
    if not isinstance(content, list) or not content:
        return messages
    if isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}
        out[len(out) - 2] = target
    return out


def _parse_response(data: dict) -> tuple[str, list[dict], list[dict], Optional[dict]]:
    blocks = data.get("content") or []
    if not isinstance(blocks, list):
        return "", [], [], None
    thinking_parts, text_parts, tool_uses = [], [], []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "thinking":
            t = b.get("thinking") or b.get("text") or ""
            if isinstance(t, str) and t.strip():
                thinking_parts.append(t.strip())
        elif bt == "text":
            t = b.get("text") or ""
            if isinstance(t, str) and t.strip():
                text_parts.append(t.strip())
        elif bt == "tool_use":
            tool_uses.append(b)
    full_text = "\n".join(text_parts)
    platform_actions = CU.extract_platform_actions(full_text)
    finish_action = CU.extract_finish_action(full_text)
    cleaned = CU.strip_protocol_lines(full_text)
    thought = "\n".join(p for p in (thinking_parts + [cleaned]) if p)
    return thought, tool_uses, platform_actions, finish_action


def _tool_use_to_action(tool_use: dict, screen_w: int, screen_h: int) -> Optional[dict]:
    if tool_use.get("name") != "computer":
        return None
    args = tool_use.get("input") or {}
    if not isinstance(args, dict):
        return None
    action = (args.get("action") or "").strip()
    coord = args.get("coordinate")
    point = None
    if isinstance(coord, list) and len(coord) >= 2:
        try:
            point = [int(coord[0]), int(coord[1])]
        except (TypeError, ValueError):
            point = None
    raw = f"computer.{action}({json.dumps(args, ensure_ascii=False)})"

    if action == "left_click":
        return None if point is None else {"action": "click", "point": point, "coord_space": "absolute", "raw": raw}
    if action == "right_click":
        return None if point is None else {"action": "right_single", "point": point, "coord_space": "absolute", "raw": raw}
    if action == "double_click":
        return None if point is None else {"action": "left_double", "point": point, "coord_space": "absolute", "raw": raw}
    if action == "left_click_drag":
        sc, ec = args.get("start_coordinate"), coord
        if isinstance(sc, list) and len(sc) >= 2 and isinstance(ec, list) and len(ec) >= 2:
            try:
                return {"action": "drag", "start_point": [int(sc[0]), int(sc[1])],
                        "end_point": [int(ec[0]), int(ec[1])], "coord_space": "absolute", "raw": raw}
            except (TypeError, ValueError):
                return None
        return None
    if action == "type":
        return {"action": "type", "content": str(args.get("text") or ""), "raw": raw}
    if action == "scroll":
        direction = (args.get("scroll_direction") or "down").lower()
        if direction not in ("up", "down", "left", "right"):
            direction = "down"
        try:
            amount = max(1, min(10, int(args.get("scroll_amount") or 1)))
        except (TypeError, ValueError):
            amount = 1
        return {"action": "scroll", "point": point or [screen_w // 2, screen_h // 2],
                "direction": direction, "scroll_amount": amount, "coord_space": "absolute", "raw": raw}
    if action == "key":
        pw = CU.key_to_pw(str(args.get("text") or ""))
        return None if pw is None else {"action": "hotkey", "pw_key": pw, "raw": raw}
    if action in ("wait", "screenshot"):
        return {"action": "wait", "raw": raw}
    return None
