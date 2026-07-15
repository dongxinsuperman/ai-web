from __future__ import annotations

import unittest
from unittest.mock import patch

from aiweb.kernel.actions import parse_action
from aiweb.kernel.executor import ActionExecutor
from aiweb.kernel.llm.base import Decision, TokenCounter
from aiweb.kernel.prompt import build_system_prompt
from aiweb.kernel.runner import WebVLMRunner


class FakeMouse:
    def __init__(self, on_click=None) -> None:
        self.on_click = on_click

    async def click(self, *_args, **_kwargs) -> None:
        if self.on_click:
            self.on_click()


class FakeKeyboard:
    async def type(self, *_args, **_kwargs) -> None:
        return None

    async def press(self, *_args, **_kwargs) -> None:
        return None


class FakePage:
    def __init__(self, title: str, url: str) -> None:
        self._title = title
        self.url = url
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.fronted = False
        self.file_input = None
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def title(self) -> str:
        return self._title

    async def bring_to_front(self) -> None:
        self.fronted = True

    async def close(self) -> None:
        for callback in self._handlers.get("close", []):
            callback()

    async def screenshot(self) -> bytes:
        return b"fake-screenshot"

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    async def evaluate(self, *_args, **_kwargs) -> None:
        return None

    async def query_selector(self, _selector: str):
        return self.file_input


class FakeFileInput:
    def __init__(self) -> None:
        self.files = None

    async def set_input_files(self, path: str) -> None:
        self.files = path


class FakeContext:
    def __init__(self, pages) -> None:
        self.pages = list(pages)
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    def add_page(self, page) -> None:
        self.pages.append(page)
        for callback in self._handlers.get("page", []):
            callback(page)

    async def new_page(self):
        page = FakePage("新标签", "about:blank")
        self.add_page(page)
        return page


class BrowserEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_tab_is_reported_without_auto_switch(self) -> None:
        page_a = FakePage("A", "https://example.test/a")
        page_b = FakePage("B", "https://example.test/b")
        context = FakeContext([page_a])
        page_a.mouse.on_click = lambda: context.add_page(page_b)
        executor = ActionExecutor(context, page_a, lambda name: f"/tmp/{name}")

        result = await executor.execute({"action": "click", "point": [500, 500]})
        state = await executor.browser_state(consume_events=True)

        self.assertTrue(result["success"])
        self.assertIs(executor.page, page_a)
        self.assertEqual(state["current_tab_id"], "tab_1")
        self.assertEqual(state["opened_tab_ids"], ["tab_2"])
        self.assertEqual([tab["title"] for tab in state["tabs"]], ["A", "B"])

        switched = await executor.execute(parse_action("switch_tab(tab_id='tab_2')"))
        self.assertTrue(switched["success"])
        self.assertIs(executor.page, page_b)
        self.assertTrue(page_b.fronted)

    async def test_invalid_tab_and_missing_upload_control_are_recoverable(self) -> None:
        page_a = FakePage("A", "https://example.test/a")
        executor = ActionExecutor(FakeContext([page_a]), page_a, lambda name: f"/tmp/{name}")

        tab_result = await executor.execute(parse_action("switch_tab(tab_id='tab_missing')"))
        upload_result = await executor.execute(parse_action("upload_file(name='source.pdf')"))

        self.assertFalse(tab_result["success"])
        self.assertIn("不存在", tab_result["error"])
        self.assertFalse(upload_result["success"])
        self.assertIn("上传控件", upload_result["error"])

    async def test_upload_success_reports_selected_asset(self) -> None:
        page_a = FakePage("A", "https://example.test/a")
        page_a.file_input = FakeFileInput()
        executor = ActionExecutor(FakeContext([page_a]), page_a, lambda name: f"/tmp/{name}")

        result = await executor.execute(parse_action("upload_file(name='source.pdf')"))

        self.assertTrue(result["success"])
        self.assertEqual(result["asset_name"], "source.pdf")
        self.assertEqual(page_a.file_input.files, "/tmp/source.pdf")


class PromptAssetInventoryTests(unittest.TestCase):
    def test_prompt_contains_asset_inventory_and_tab_id_syntax(self) -> None:
        prompt = build_system_prompt(
            "上传素材",
            has_assets=True,
            assets=[{"name": "source.pdf", "mime": "application/pdf", "size": 42}],
        )

        self.assertIn("source.pdf", prompt)
        self.assertIn("application/pdf", prompt)
        self.assertIn("switch_tab(tab_id='tab_2')", prompt)


class FakeVLM:
    def __init__(self) -> None:
        self.counter = TokenCounter()
        self.hints: list[str] = []
        self.observations: list[list[str]] = []
        self.calls = 0

    def add_hint(self, text: str) -> None:
        self.hints.append(text)

    def should_reset_session(self) -> bool:
        return False

    async def decide(self, _screenshot: bytes, *, mime: str = "image/png") -> Decision:
        self.observations.append(list(self.hints))
        self.hints.clear()
        self.calls += 1
        if self.calls == 1:
            return Decision(thought="点击入口", parsed_actions=[{"action": "click", "point": [500, 500]}])
        return Decision(thought="已看到新标签", parsed_actions=[{"action": "finished", "content": "done"}])


class RunnerBrowserStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_background_tab_is_given_to_vlm_before_next_action(self) -> None:
        page_a = FakePage("A", "https://example.test/a")
        page_b = FakePage("B", "https://example.test/b")
        context = FakeContext([page_a])
        page_a.mouse.on_click = lambda: context.add_page(page_b)
        vlm = FakeVLM()
        runner = WebVLMRunner(context, page_a, lambda name: f"/tmp/{name}")
        steps: list[dict] = []

        async def on_step(step: dict) -> None:
            steps.append(step)

        with patch("aiweb.kernel.runner.create_main_vlm", return_value=vlm):
            result = await runner.run("完成测试", False, on_step=on_step)

        self.assertEqual(result.status, "success")
        self.assertIs(runner.executor.page, page_a)
        self.assertTrue(any("本轮新增标签：tab_2" in hint for hint in vlm.observations[1]))
        self.assertTrue(any("当前标签：tab_1" in hint for hint in vlm.observations[1]))
        self.assertEqual(steps[1]["action_detail"]["browser_state_after"]["opened_tab_ids"], ["tab_2"])


if __name__ == "__main__":
    unittest.main()
