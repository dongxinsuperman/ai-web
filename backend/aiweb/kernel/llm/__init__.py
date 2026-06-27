"""多协议 LLM 适配层 · 工厂入口。

runner / 辅助系统只通过 create_main_vlm / create_assistant 拿实例，
不直接 import 各家实现（按需 import：没装某家依赖也不影响其它家）。
"""
from __future__ import annotations

from typing import Optional

from aiweb.kernel.llm.base import BaseAssistant, BaseMainVLM, Decision, TokenCounter
from aiweb.settings import get_settings

__all__ = [
    "BaseAssistant", "BaseMainVLM", "Decision", "TokenCounter",
    "create_main_vlm", "create_assistant",
    "SUPPORTED_VLM_PROVIDERS", "SUPPORTED_ASSISTANT_PROVIDERS",
]

SUPPORTED_VLM_PROVIDERS = ("doubao", "claude", "openai")
SUPPORTED_ASSISTANT_PROVIDERS = ("doubao", "claude", "openai")


def create_main_vlm(system_prompt: str, *, counter: Optional[TokenCounter] = None) -> BaseMainVLM:
    provider = (get_settings().vlm_provider or "doubao").strip().lower()
    if provider == "doubao":
        from aiweb.kernel.llm.main.doubao_responses import DoubaoResponsesClient
        return DoubaoResponsesClient(system_prompt, counter=counter)
    if provider in ("claude", "anthropic"):
        from aiweb.kernel.llm.main.claude_cu import ClaudeComputerUseClient
        return ClaudeComputerUseClient(system_prompt, counter=counter)
    if provider in ("openai", "gpt"):
        from aiweb.kernel.llm.main.gpt_cu import GPTComputerUseClient
        return GPTComputerUseClient(system_prompt, counter=counter)
    raise RuntimeError(f"未知 vlm_provider={provider!r}，支持：{SUPPORTED_VLM_PROVIDERS}")


def create_assistant(*, counter: Optional[TokenCounter] = None) -> BaseAssistant:
    provider = get_settings().assistant_provider_resolved
    if provider == "doubao":
        from aiweb.kernel.llm.assistants.doubao import DoubaoAssistant
        return DoubaoAssistant(counter=counter)
    if provider in ("claude", "anthropic"):
        from aiweb.kernel.llm.assistants.claude import ClaudeAssistant
        return ClaudeAssistant(counter=counter)
    if provider in ("openai", "gpt"):
        from aiweb.kernel.llm.assistants.openai import OpenAIAssistant
        return OpenAIAssistant(counter=counter)
    raise RuntimeError(f"未知 assistant_provider={provider!r}，支持：{SUPPORTED_ASSISTANT_PROVIDERS}")
