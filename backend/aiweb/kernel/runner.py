"""VLM 决策循环（provider 无关）。

通过 create_main_vlm / create_assistant 拿到主模型与辅助模型；runner 只关心
"喂截图 → 拿 Decision（动作链）→ 执行 → 终态/断言"。各家协议差异（豆包文本
DSL / Claude·GPT computer-use）由 LLM 适配层吸收；坐标空间由动作 dict 的
coord_space 决定，执行层按帧尺寸反缩放。
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page

from aiweb.kernel import actions as A
from aiweb.kernel import assertion as ASSERT
from aiweb.kernel.executor import ActionExecutor, UnknownAction
from aiweb.kernel.llm import create_assistant, create_main_vlm
from aiweb.kernel.llm.main._cu_common import decode_image_size
from aiweb.kernel.prompt import build_system_prompt_for_backend
from aiweb.kernel.stuck import StuckDetector
from aiweb.settings import get_settings

OnStep = Callable[[dict], Awaitable[None]]
Hook = Callable[[], Awaitable[None]]
ShouldCancel = Callable[[], Awaitable[bool]]

_STABLE_TIMEOUT_MS = 8000
_STABLE_WINDOW_MS = 500
_STABLE_JS = """
async ({ timeout, stable }) => {
  await new Promise((r) =>
    document.readyState === 'complete'
      ? r()
      : window.addEventListener('load', () => r(), { once: true })
  );
  await new Promise((resolve) => {
    let t = setTimeout(done, stable);
    const max = setTimeout(done, timeout);
    const target = document.body || document.documentElement;
    const obs = new MutationObserver(() => {
      clearTimeout(t);
      t = setTimeout(done, stable);
    });
    if (target) obs.observe(target, { childList: true, subtree: true });
    function done() { clearTimeout(t); clearTimeout(max); obs.disconnect(); resolve(); }
  });
}
"""


@dataclass
class RunResult:
    status: str  # success / failed / needs_human
    steps: int
    token_usage: dict
    fail_reason: str | None = None
    finish_content: str | None = None
    segments: int = 1


class WebVLMRunner:
    def __init__(self, context: BrowserContext, page: Page, resolve_asset) -> None:
        self.settings = get_settings()
        self.executor = ActionExecutor(context, page, resolve_asset)
        self.stuck = StuckDetector()

    async def run(
        self,
        goal: str,
        has_assets: bool,
        *,
        function_map_context: str | None = None,
        site_directory: str | None = None,
        on_step: OnStep,
        on_heartbeat: Hook | None = None,
        should_cancel: ShouldCancel | None = None,
    ) -> RunResult:
        if not goal or not goal.strip():
            return RunResult(status="failed", steps=0, token_usage={}, fail_reason="run_content 为空")

        provider = (self.settings.vlm_provider or "doubao").strip().lower()
        is_cu = provider in ("claude", "anthropic", "openai", "gpt")
        system_prompt = build_system_prompt_for_backend(
            provider, goal, has_assets=has_assets,
            function_map_context=function_map_context, site_directory=site_directory,
        )
        vlm = create_main_vlm(system_prompt)
        # 辅助模型惰性创建：只在结构化通道二次断言时才需要（见下方 finished 分支）。

        # 结构化通道判定（是否启用二次断言）
        structured, hits, expected = ASSERT.detect_structured(goal)
        channel_desc = (
            f"结构化通道（命中标签：{', '.join(hits)}）→ finished 时启用二次断言"
            if structured
            else f"自由对话通道（命中标签：{', '.join(hits) or '无'}）→ 不做二次断言，采纳模型 finished"
        )
        await on_step({
            "step_no": 0, "action": "通道判定", "thought": channel_desc,
            "action_raw": None, "action_detail": {"structured": structured, "hits": hits, "provider": provider},
            "screenshot_before": None, "screenshot_after": None, "token_usage": None, "elapsed_ms": None,
        })

        max_steps = self.settings.max_steps
        consecutive_shot_fail = 0
        prev_shot: bytes | None = None
        history_lines: list[str] = []
        segments = 1

        for step_no in range(1, max_steps + 1):
            if should_cancel is not None and await should_cancel():
                return RunResult(status="failed", steps=step_no - 1,
                                 token_usage=self._usage(vlm), fail_reason="cancelled")
            if on_heartbeat is not None:
                await on_heartbeat()

            # 分段重置（豆包按 token 阈值；CU 系恒不触发）
            if vlm.should_reset_session():
                vlm.reset_session(
                    f"【会话续接】此前已完成 {step_no - 1} 步（历史已归档）。请根据当前截图分析剩余进度并继续；"
                    "若已满足完成条件，直接宣告完成。"
                )
                segments += 1

            await self._wait_stable()
            shot = await self._screenshot()
            if shot is None:
                consecutive_shot_fail += 1
                if consecutive_shot_fail >= 3:
                    return RunResult(status="failed", steps=step_no - 1,
                                     token_usage=self._usage(vlm), fail_reason="连续截图失败，浏览器异常")
                continue
            consecutive_shot_fail = 0
            # 仅 CU（claude/gpt）用绝对像素，需要按截图尺寸反缩放；豆包归一化坐标用不到，跳过解码。
            if is_cu:
                fw, fh = decode_image_size(shot)
                self.executor.set_frame(fw, fh)

            t0 = time.time()
            try:
                decision = await vlm.decide(shot, mime="image/png")
            except Exception as e:
                return RunResult(status="failed", steps=step_no - 1,
                                 token_usage=self._usage(vlm), fail_reason=f"决策失败: {e}")
            elapsed = int((time.time() - t0) * 1000)

            chain = decision.parsed_actions or [{"action": "unknown"}]
            thought = decision.thought
            label = chain[0].get("action", "unknown")
            history_lines.append(f"{step_no}. {label}: {(thought or '')[:80]}")

            # 执行动作链：非终态顺序执行，遇终态停下
            terminal = None
            after_shot = None
            new_tab_any = False
            exec_failed: str | None = None
            for pa in chain:
                act = pa.get("action", "unknown")
                if act in A.TERMINAL_ACTIONS:
                    terminal = pa
                    break
                try:
                    res = await self.executor.execute(pa)
                    if res.get("new_tab"):
                        new_tab_any = True
                except UnknownAction:
                    vlm.add_hint(
                        f"动作 '{act}' 不被支持。屏幕交互用 click/type/scroll/drag/hotkey；"
                        "导航/标签/上传用 PLATFORM_ACTION:（如 open_url/new_tab/switch_tab/upload_file）。"
                    )
                except Exception as e:
                    exec_failed = f"执行失败 [{act}]: {e}"
                    break

            if exec_failed:
                await on_step(self._step(step_no, label, thought, chain, shot, None, decision.usage, elapsed))
                return RunResult(status="failed", steps=step_no, token_usage=self._usage(vlm),
                                 fail_reason=exec_failed, segments=segments)

            if new_tab_any:
                vlm.add_hint(f"步骤 {step_no} 操作后切换到了新标签页，当前截图为新标签内容，请判断是否达成目标。")
            if terminal is None:
                after_shot = await self._screenshot()

            await on_step(self._step(step_no, label, thought, chain, shot, after_shot, decision.usage, elapsed))

            if terminal is not None:
                act = terminal.get("action")
                if act == "finished":
                    if structured and expected:
                        try:
                            assistant = create_assistant(counter=vlm.counter)
                            verdict, reason = await ASSERT.run_final_assertion(
                                assistant, expected, thought, final_bytes=shot,
                                before_bytes=prev_shot, history="\n".join(history_lines),
                            )
                        except Exception as e:
                            verdict, reason = "skip", f"辅助模型未就绪，跳过二次断言：{e}"
                        await on_step({
                            "step_no": step_no + 1, "action": "二次断言",
                            "thought": f"{verdict.upper()}：{reason}",
                            "action_raw": None, "action_detail": {"verdict": verdict, "reason": reason},
                            "screenshot_before": prev_shot, "screenshot_after": shot,
                            "token_usage": None, "elapsed_ms": None,
                        })
                        if verdict == "fail":
                            return RunResult(status="failed", steps=step_no, token_usage=self._usage(vlm),
                                             fail_reason=f"二次断言不通过：{reason}", segments=segments)
                    return RunResult(status="success", steps=step_no, token_usage=self._usage(vlm),
                                     finish_content=terminal.get("content"), segments=segments)
                if act == "assert_fail":
                    return RunResult(status="failed", steps=step_no, token_usage=self._usage(vlm),
                                     fail_reason=terminal.get("content", "断言不通过"), segments=segments)
                if act == "call_user":
                    return RunResult(status="needs_human", steps=step_no, token_usage=self._usage(vlm),
                                     fail_reason=terminal.get("content", "需要人工介入"), segments=segments)

            # 卡死检测（对链内点击/滚动动作）
            for pa in chain:
                for hint in (self.stuck.check_click(pa), self.stuck.check_scroll(pa)):
                    if hint:
                        vlm.add_hint(hint)

            prev_shot = shot

        return RunResult(status="failed", steps=max_steps, token_usage=self._usage(vlm),
                         fail_reason=f"达到安全上限 {max_steps} 步", segments=segments)

    # ---- 工具 ----
    @staticmethod
    def _usage(vlm) -> dict:
        try:
            return vlm.counter.summary()
        except Exception:
            return {}

    async def _wait_stable(self) -> None:
        page = self.executor.page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=_STABLE_TIMEOUT_MS)
        except Exception:
            pass
        try:
            await page.evaluate(_STABLE_JS, {"timeout": _STABLE_TIMEOUT_MS, "stable": _STABLE_WINDOW_MS})
        except Exception:
            pass

    async def _screenshot(self) -> bytes | None:
        try:
            return await self.executor.page.screenshot()
        except Exception:
            return None

    @staticmethod
    def _step(step_no, action, thought, chain, before, after, usage, elapsed) -> dict:
        primary = chain[0] if chain else {}
        return {
            "step_no": step_no,
            "action": action,
            "thought": thought,
            "action_raw": primary.get("raw"),
            "action_detail": {"chain": chain},
            "screenshot_before": before,
            "screenshot_after": after,
            "token_usage": usage,
            "elapsed_ms": elapsed,
        }
