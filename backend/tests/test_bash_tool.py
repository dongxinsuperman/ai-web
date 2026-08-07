from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiweb.kernel import executor as executor_module
from aiweb.kernel.actions import extract_actions, parse_action
from aiweb.kernel.executor import ActionExecutor
from aiweb.kernel.llm.base import Decision, TokenCounter
from aiweb.kernel.llm.main._cu_common import extract_platform_actions
from aiweb.kernel.prompt import build_system_prompt, build_system_prompt_cu
from aiweb.kernel.runner import WebVLMRunner


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.test/"
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def title(self) -> str:
        return "Example"

    async def screenshot(self) -> bytes:
        return b"fake-screenshot"

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    async def evaluate(self, *_args, **_kwargs) -> None:
        return None


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)


class FakeProcess:
    def __init__(self, *, returncode: int | None, stdout: bytes = b"", stderr: bytes = b"", pid: int = 12345) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.pid = pid
        self.killed = False

    async def communicate(self):
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


class BashActionParsingTests(unittest.TestCase):
    def test_bash_command_supports_quotes_parentheses_and_newline_escape(self) -> None:
        parsed = parse_action(
            "bash(command='python -c \"print((1 + 2))\" && printf \\'done\\'\\nprintf ok')"
        )

        self.assertEqual(parsed["action"], "bash")
        self.assertEqual(
            parsed["command"],
            'python -c "print((1 + 2))" && printf \'done\'\nprintf ok',
        )

    def test_invalid_action_never_becomes_finished(self) -> None:
        malformed = parse_action("not a valid action(")
        missing_action = parse_action(extract_actions("Thought: 还需要继续")[0])

        self.assertEqual(malformed["action"], "unknown")
        self.assertEqual(missing_action["action"], "unknown")
        self.assertIn("缺少 Action 行", missing_action["error"])

    def test_action_parser_keeps_existing_prefix_tolerance(self) -> None:
        parsed = parse_action("I will click(point='<point>1 2</point>')")

        self.assertEqual(parsed["action"], "click")
        self.assertEqual(parsed["point"], [1, 2])

    def test_unescaped_inner_quotes_are_rejected_instead_of_truncated(self) -> None:
        parsed = parse_action("bash(command='psql -c 'select id from users limit 1'')")

        self.assertEqual(parsed["action"], "bash")
        self.assertNotIn("command", parsed)
        self.assertIn("结束引号后仍有内容", parsed["error"])

    def test_double_wrapped_command_accepts_inner_single_quotes(self) -> None:
        parsed = parse_action('bash(command="psql -c \'select id from users limit 1\'")')

        self.assertEqual(parsed["command"], "psql -c 'select id from users limit 1'")

    def test_cu_platform_protocol_accepts_bash(self) -> None:
        actions = extract_platform_actions(
            "PLATFORM_ACTION: bash(command='printf \"123\"')"
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "bash")
        self.assertEqual(actions[0]["command"], 'printf "123"')

    def test_prompts_publish_bash_and_single_action_rule(self) -> None:
        doubao = build_system_prompt("查询数据后填写网页")
        cu = build_system_prompt_cu("查询数据后填写网页")

        self.assertIn("bash(command='命令')", doubao)
        self.assertIn("bash 必须独占一轮", doubao)
        self.assertIn("PLATFORM_ACTION: bash(command='...')", cu)
        self.assertIn("must be the only action", cu)
        self.assertIn("命令含单引号时", doubao)
        self.assertIn("contains single quotes", cu)


class BashExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        page = FakePage()
        self.executor = ActionExecutor(FakeContext(page), page, lambda name: name)

    async def test_executes_with_explicit_bash_and_returns_process_result(self) -> None:
        process = FakeProcess(returncode=7, stdout=b"user_id=12345\n", stderr=b"query warning\n")
        create = AsyncMock(return_value=process)

        with (
            patch("aiweb.kernel.executor.asyncio.create_subprocess_exec", create),
            patch("aiweb.kernel.executor.asyncio.sleep", new=AsyncMock()),
        ):
            result = await self.executor.execute({"action": "bash", "command": "query-user"})

        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["stdout"], "user_id=12345\n")
        self.assertEqual(result["stderr"], "query warning\n")
        args, kwargs = create.await_args
        self.assertEqual(args, ("bash", "-c", "query-user"))
        self.assertIs(kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], asyncio.subprocess.PIPE)
        self.assertIs(kwargs["stderr"], asyncio.subprocess.PIPE)
        if os.name == "posix":
            self.assertTrue(kwargs["start_new_session"])
        elif os.name == "nt":
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NEW_PROCESS_GROUP)

    @unittest.skipUnless(shutil.which("bash"), "当前环境未安装 Bash")
    async def test_real_bash_command_returns_stdout(self) -> None:
        with patch("aiweb.kernel.executor.asyncio.sleep", new=AsyncMock()):
            result = await self.executor.execute({"action": "bash", "command": "printf '12345'"})

        self.assertTrue(result["success"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "12345")
        self.assertEqual(result["stderr"], "")

    async def test_missing_bash_returns_explicit_error(self) -> None:
        with patch(
            "aiweb.kernel.executor.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("bash not found")),
        ):
            result = await self.executor.execute({"action": "bash", "command": "printf ok"})

        self.assertFalse(result["success"])
        self.assertIn("无法启动 Bash", result["error"])

    async def test_malformed_command_is_not_executed(self) -> None:
        parsed = parse_action("bash(command='echo 'ok'')")
        create = AsyncMock()

        with patch("aiweb.kernel.executor.asyncio.create_subprocess_exec", create):
            result = await self.executor.execute(parsed)

        self.assertFalse(result["success"])
        self.assertIn("结束引号后仍有内容", result["error"])
        create.assert_not_awaited()

    async def test_timeout_kills_process_and_returns_explicit_error(self) -> None:
        process = FakeProcess(returncode=-9)
        terminate = AsyncMock(return_value=None)

        async def timeout(awaitable, *, timeout):
            awaitable.close()
            raise TimeoutError

        with (
            patch(
                "aiweb.kernel.executor.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ),
            patch("aiweb.kernel.executor.asyncio.wait_for", side_effect=timeout),
            patch("aiweb.kernel.executor._terminate_process_tree", terminate),
        ):
            result = await self.executor.execute({"action": "bash", "command": "long-job"})

        self.assertFalse(result["success"])
        terminate.assert_awaited_once_with(process)
        self.assertIn("执行超过", result["error"])
        self.assertIn("输出管道仍未", result["error"])

    @unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "需要 POSIX Bash")
    async def test_timeout_terminates_real_bash_process_group(self) -> None:
        started = time.monotonic()
        with (
            patch("aiweb.kernel.executor._BASH_TIMEOUT_SEC", 0.05),
            patch("aiweb.kernel.executor._BASH_TERMINATE_GRACE_SEC", 0.5),
        ):
            result = await self.executor.execute({"action": "bash", "command": "sleep 3 & wait"})
        elapsed = time.monotonic() - started

        self.assertFalse(result["success"])
        self.assertIn("执行超过", result["error"])
        self.assertNotIn("输出管道仍未", result["error"])
        self.assertLess(elapsed, 1.5)

    async def test_windows_process_tree_uses_taskkill(self) -> None:
        process = FakeProcess(returncode=None, pid=2468)
        killer = FakeProcess(returncode=0)
        create = AsyncMock(return_value=killer)

        with patch("aiweb.kernel.executor.asyncio.create_subprocess_exec", create):
            error = await executor_module._terminate_process_tree(process, platform="nt")

        self.assertIsNone(error)
        create.assert_awaited_once_with(
            "taskkill", "/PID", "2468", "/T", "/F",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    def test_windows_bash_starts_in_new_process_group(self) -> None:
        with patch.object(executor_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            options = executor_module._bash_process_options(platform="nt")

        self.assertEqual(options, {"creationflags": 512})


class FakeVLM:
    def __init__(self, first_actions: list[dict]) -> None:
        self.counter = TokenCounter()
        self.first_actions = first_actions
        self.observations: list[list[str]] = []
        self.pending_hints: list[str] = []
        self.calls = 0

    def add_hint(self, text: str) -> None:
        self.pending_hints.append(text)

    def should_reset_session(self) -> bool:
        return False

    async def decide(self, _screenshot: bytes, *, mime: str = "image/png") -> Decision:
        self.observations.append(list(self.pending_hints))
        self.pending_hints.clear()
        self.calls += 1
        if self.calls == 1:
            return Decision(thought="先查询数据", parsed_actions=self.first_actions)
        return Decision(thought="已经取得结果", parsed_actions=[{"action": "finished", "content": "done"}])


class BashRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _runner(self) -> WebVLMRunner:
        page = FakePage()
        return WebVLMRunner(FakeContext(page), page, lambda name: name)

    async def test_bash_result_is_returned_to_model(self) -> None:
        vlm = FakeVLM([{"action": "bash", "command": "query-user"}])
        runner = self._runner()
        runner.executor.execute = AsyncMock(return_value={
            "action": "bash",
            "success": True,
            "exit_code": 0,
            "stdout": "user_id=12345",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "elapsed_ms": 20,
        })
        steps: list[dict] = []

        async def on_step(step: dict) -> None:
            steps.append(step)

        with patch("aiweb.kernel.runner.create_main_vlm", return_value=vlm):
            result = await runner.run("查询数据后填写网页", False, on_step=on_step)

        self.assertEqual(result.status, "success")
        self.assertTrue(any("user_id=12345" in hint for hint in vlm.observations[1]))
        self.assertEqual(steps[1]["action_detail"]["results"][0]["exit_code"], 0)

    def test_bash_failure_hint_describes_execution_failure(self) -> None:
        hint = WebVLMRunner._bash_result_hint({
            "action": "bash",
            "success": False,
            "error": "Bash 执行超过 60 秒",
        })

        self.assertIn("Bash 执行失败", hint)
        self.assertNotIn("Bash 未执行", hint)

    async def test_bash_mixed_with_other_actions_executes_nothing(self) -> None:
        vlm = FakeVLM([
            {"action": "click", "point": [500, 500]},
            {"action": "bash", "command": "query-user"},
        ])
        runner = self._runner()
        runner.executor.execute = AsyncMock()
        steps: list[dict] = []

        async def on_step(step: dict) -> None:
            steps.append(step)

        with patch("aiweb.kernel.runner.create_main_vlm", return_value=vlm):
            result = await runner.run("查询数据后填写网页", False, on_step=on_step)

        self.assertEqual(result.status, "success")
        runner.executor.execute.assert_not_awaited()
        self.assertTrue(any("bash 必须独占一轮" in hint for hint in vlm.observations[1]))
        self.assertEqual(steps[1]["action"], "click")
        self.assertEqual(
            steps[1]["action_detail"]["skipped_reason"],
            "bash 必须独占一轮，不能与其他动作同时执行",
        )


if __name__ == "__main__":
    unittest.main()
