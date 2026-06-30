"""Browser Agent 内部接入 API。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from aiweb.agent_hub import agent_hub
from aiweb.scheduler.worker import finalize_run, heartbeat_run, persist_run_step
from aiweb.settings import get_settings

logger = logging.getLogger("aiweb.browser_agents")
router = APIRouter(prefix="/browser-agents", tags=["browser-agents"])


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


class _RemoteResult:
    def __init__(self, msg: dict[str, Any]) -> None:
        self.status = msg.get("status") or "failed"
        self.steps = int(msg.get("steps") or 0)
        self.token_usage = msg.get("tokenUsage") or msg.get("token_usage") or {}
        self.fail_reason = msg.get("failReason") or msg.get("fail_reason")
        self.finish_content = msg.get("finishContent") or msg.get("finish_content")
        self.segments = int(msg.get("segments") or 1)


@router.get("", dependencies=[_guard()])
async def list_browser_agents():
    return {"agents": agent_hub.list_agents()}


def _ws_authorized(websocket: WebSocket, token: str | None) -> bool:
    expected = get_settings().api_token
    if not expected:
        return True
    auth = websocket.headers.get("authorization")
    return token == expected or auth == f"Bearer {expected}"


@router.websocket("/ws")
async def browser_agent_ws(websocket: WebSocket, token: str | None = Query(default=None)):
    if not _ws_authorized(websocket, token):
        await websocket.close(code=4401, reason="unauthorized")
        return
    await websocket.accept()
    agent_id: str | None = None
    try:
        hello = await websocket.receive_json()
        if hello.get("type") != "hello" or not hello.get("agentId"):
            await websocket.close(code=4400, reason="hello required")
            return
        agent_id = str(hello["agentId"]).strip()
        await agent_hub.register(agent_id, websocket, hello)
        logger.info("Browser Agent connected id=%s", agent_id)
        await websocket.send_json({"type": "hello_ack", "agentId": agent_id})

        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            run_id = msg.get("runId")
            await agent_hub.touch(agent_id, run_id)

            if msg_type == "heartbeat" and run_id:
                await heartbeat_run(run_id)
            elif msg_type == "step_done" and run_id:
                await persist_run_step(run_id, msg.get("step") or {})
            elif msg_type == "run_done" and run_id:
                await finalize_run(run_id, _RemoteResult(msg), elapsed_ms=msg.get("elapsedMs"))
                await agent_hub.finish_run(run_id)
            elif msg_type == "auth_check_done" and msg.get("requestId"):
                await agent_hub.complete_request(str(msg["requestId"]), msg)
            elif msg_type == "log":
                logger.info("Agent %s: %s", agent_id, msg.get("message"))
            else:
                logger.warning("未知 Agent 消息 agent=%s msg=%s", agent_id, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Browser Agent WS 异常 agent=%s", agent_id)
    finally:
        if agent_id:
            await agent_hub.unregister(agent_id, websocket)
            logger.info("Browser Agent disconnected id=%s", agent_id)
