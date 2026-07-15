"""Browser Agent CLI.

Agent 不管理容量，不接收外部任务；它只连接 AI Web Server，执行 Server 派发的 run。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
from urllib.parse import urlencode

import httpx
import websockets

from aiweb.kernel.browser import browser_manager

logger = logging.getLogger("aiweb.agent")

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


def _as_bool(value, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSEY:
        return False
    return default


def _ws_url(server: str, token: str | None) -> str:
    base = server.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    qs = f"?{urlencode({'token': token})}" if token else ""
    return f"{base}/api/browser-agents/ws{qs}"


async def _download_assets(run_id: str, assets: list[dict], token: str | None) -> tuple[str, dict[str, str]]:
    root = tempfile.mkdtemp(prefix=f"aiweb-agent-{run_id}-")
    out: dict[str, str] = {}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        for asset in assets or []:
            name = asset.get("name")
            url = asset.get("url")
            if not name or not url:
                continue
            path = os.path.join(root, os.path.basename(name))
            resp = await client.get(url)
            resp.raise_for_status()
            with open(path, "wb") as f:
                f.write(resp.content)
            out[name] = path
    return root, out


def _encode_step(step: dict) -> dict:
    out = dict(step)
    for key in ("screenshot_before", "screenshot_after"):
        raw = out.pop(key, None)
        if isinstance(raw, bytes):
            out[f"{key}_b64"] = base64.b64encode(raw).decode("ascii")
    return out


class BrowserAgent:
    def __init__(self, *, server: str, agent_id: str, token: str | None, name: str | None) -> None:
        self.server = server
        self.agent_id = agent_id
        self.token = token
        self.name = name
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.send_lock = asyncio.Lock()

    async def run_forever(self) -> None:
        await browser_manager.start()
        try:
            while True:
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Agent 连接断开，5 秒后重试: %s", exc)
                    await asyncio.sleep(5)
        finally:
            await browser_manager.stop()

    async def _send(self, ws, payload: dict) -> None:
        async with self.send_lock:
            await ws.send(json.dumps(payload, ensure_ascii=False))

    async def _connect_once(self) -> None:
        url = _ws_url(self.server, self.token)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            await self._send(ws, {
                "type": "hello",
                "agentId": self.agent_id,
                "name": self.name,
                "os": os.name,
            })
            ack = json.loads(await ws.recv())
            if ack.get("type") != "hello_ack":
                raise RuntimeError(f"Server hello_ack 异常: {ack}")
            logger.info("Browser Agent 已连接 server=%s agent=%s", self.server, self.agent_id)

            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")
                if msg_type == "start_run":
                    run_id = msg["runId"]
                    if run_id in self.tasks:
                        continue
                    self.cancel_events[run_id] = asyncio.Event()
                    task = asyncio.create_task(self._run_one(ws, msg), name=f"agent-run-{run_id}")
                    self.tasks[run_id] = task
                    task.add_done_callback(lambda _t, rid=run_id: self.tasks.pop(rid, None))
                elif msg_type == "stop_run":
                    ev = self.cancel_events.get(msg.get("runId"))
                    if ev:
                        ev.set()
                elif msg_type == "auth_check":
                    request_id = msg.get("requestId")
                    if not request_id:
                        continue
                    asyncio.create_task(self._auth_check(ws, msg), name=f"agent-auth-check-{request_id}")

    async def _run_one(self, ws, payload: dict) -> None:
        run_id = payload["runId"]
        asset_root = None
        try:
            asset_root, asset_map = await _download_assets(run_id, payload.get("assets") or [], self.token)

            def resolve_asset(name: str) -> str:
                path = asset_map.get(name)
                if not path:
                    raise FileNotFoundError(f"素材不存在: {name}")
                return path

            async def send_heartbeat() -> None:
                await self._send(ws, {"type": "heartbeat", "runId": run_id})

            original_cancel = self.cancel_events[run_id]

            async def should_cancel() -> bool:
                return original_cancel.is_set()

            step_counter = {"n": 0}

            async def send_step(step: dict) -> None:
                step_counter["n"] = int(step.get("step_no") or step_counter["n"])
                await self._send(ws, {
                    "type": "step_done",
                    "runId": run_id,
                    "step": _encode_step(step),
                })

            local_payload = dict(payload)
            result, elapsed_ms = await self._execute(
                local_payload, resolve_asset, send_step, send_heartbeat, should_cancel, step_counter
            )
            await self._send(ws, {
                "type": "run_done",
                "runId": run_id,
                "status": result.status,
                "steps": result.steps,
                "tokenUsage": result.token_usage or {},
                "elapsedMs": elapsed_ms,
                "failReason": result.fail_reason,
                "finishContent": result.finish_content,
                "segments": getattr(result, "segments", 1),
            })
        except Exception as exc:
            logger.exception("Agent 执行异常 run=%s", run_id)
            await self._send(ws, {
                "type": "run_done",
                "runId": run_id,
                "status": "failed",
                "steps": 0,
                "tokenUsage": {},
                "elapsedMs": 0,
                "failReason": f"agent 异常: {exc}",
                "segments": 1,
            })
        finally:
            self.cancel_events.pop(run_id, None)
            if asset_root:
                shutil.rmtree(asset_root, ignore_errors=True)

    async def _auth_check(self, ws, payload: dict) -> None:
        request_id = payload["requestId"]
        browser = None
        context = None
        try:
            url = payload.get("url")
            if not url:
                raise ValueError("url required")
            headless = _as_bool(payload.get("headless"), default=True)
            platform = payload.get("platform") or "chrome"
            logger.info("Agent 打开登录验证浏览器 request=%s platform=%s headless=%s", request_id, platform, headless)
            browser = await browser_manager.launch(headless, platform)
            context = await browser_manager.new_context(browser, storage_state=payload.get("storageState"))
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=int(payload.get("networkIdleTimeoutMs") or 4000))
            except Exception:
                pass
            await self._send(ws, {
                "type": "auth_check_done",
                "requestId": request_id,
                "opened": True,
                "finalUrl": page.url,
                "title": await page.title(),
            })
        except Exception as exc:
            await self._send(ws, {
                "type": "auth_check_done",
                "requestId": request_id,
                "opened": False,
                "error": str(exc),
                "finalUrl": None,
                "title": None,
            })
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _execute(self, payload, resolve_asset, on_step, on_heartbeat, should_cancel, step_counter):
        """执行远端 run。复用 worker 的浏览器执行结构，但替换三个 hook。"""
        import time

        from aiweb.kernel.runner import WebVLMRunner

        t0 = time.time()
        browser = None
        context = None
        try:
            headless = _as_bool(payload.get("headless"), default=True)
            platform = payload.get("platform") or "chrome"
            logger.info("Agent 启动任务浏览器 run=%s platform=%s headless=%s", payload.get("runId"), platform, headless)
            browser = await browser_manager.launch(headless, platform)
            context = await browser_manager.new_context(browser, storage_state=payload.get("storageState"))
            page = await context.new_page()
            runner = WebVLMRunner(context, page, resolve_asset)
            result = await runner.run(
                payload.get("runContent") or "",
                has_assets=bool(payload.get("assets")),
                assets=payload.get("assets") or [],
                function_map_context=payload.get("functionMapContext"),
                site_directory=payload.get("siteDirectory"),
                on_step=on_step,
                on_heartbeat=on_heartbeat,
                should_cancel=should_cancel,
            )
        except Exception as exc:
            from aiweb.scheduler.worker import _failed_result

            result = _failed_result(step_counter["n"], f"agent worker 异常: {exc}")
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
        return result, int((time.time() - t0) * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Web Browser Agent")
    parser.add_argument("--server", required=True, help="AI Web Server base URL, e.g. http://127.0.0.1:8009")
    parser.add_argument("--agent-id", required=True, help="Agent id controlled by Server config, e.g. win-01")
    parser.add_argument("--token", default=os.getenv("AIWEB_API_TOKEN") or "")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    agent = BrowserAgent(server=args.server, agent_id=args.agent_id, token=args.token or None, name=args.name)
    asyncio.run(agent.run_forever())
