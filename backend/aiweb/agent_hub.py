"""Browser Agent 连接管理。

Agent 是内部执行节点：Server 控制容量和派发，Agent 只接收 start_run 并回传步骤/终态。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket

from aiweb.models.base import utcnow


@dataclass
class AgentInfo:
    agent_id: str
    websocket: WebSocket
    name: str | None = None
    os: str | None = None
    connected_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)
    running: set[str] = field(default_factory=set)


class AgentHub:
    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}
        self._run_to_agent: dict[str, str] = {}
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def register(self, agent_id: str, websocket: WebSocket, meta: dict[str, Any]) -> None:
        async with self._lock:
            old = self._agents.get(agent_id)
            if old is not None and old.websocket is not websocket:
                try:
                    await old.websocket.close(code=4000, reason="agent reconnected")
                except Exception:
                    pass
            self._agents[agent_id] = AgentInfo(
                agent_id=agent_id,
                websocket=websocket,
                name=meta.get("name"),
                os=meta.get("os"),
            )

    async def unregister(self, agent_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            cur = self._agents.get(agent_id)
            if cur is None or cur.websocket is not websocket:
                return
            for run_id in list(cur.running):
                self._run_to_agent.pop(run_id, None)
            self._agents.pop(agent_id, None)

    async def send(self, agent_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            info = self._agents.get(agent_id)
            if info is None:
                raise RuntimeError(f"Agent 不在线: {agent_id}")
            ws = info.websocket
        await ws.send_json(payload)

    async def send_start_run(self, agent_id: str, payload: dict[str, Any]) -> None:
        run_id = payload["runId"]
        await self.send(agent_id, {"type": "start_run", **payload})
        async with self._lock:
            info = self._agents.get(agent_id)
            if info is not None:
                info.running.add(run_id)
                info.last_seen_at = utcnow()
                self._run_to_agent[run_id] = agent_id

    async def send_stop_run(self, run_id: str, reason: str = "cancelled") -> bool:
        async with self._lock:
            agent_id = self._run_to_agent.get(run_id)
        if not agent_id:
            return False
        await self.send(agent_id, {"type": "stop_run", "runId": run_id, "reason": reason})
        return True

    async def request_auth_check(self, payload: dict[str, Any], *, timeout: float = 60) -> dict[str, Any]:
        agent_id = await self._choose_agent()
        if not agent_id:
            raise RuntimeError("没有在线 Agent，无法验证登录态")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        async with self._lock:
            self._pending_requests[request_id] = fut
        try:
            await self.send(agent_id, {"type": "auth_check", "requestId": request_id, **payload})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            async with self._lock:
                self._pending_requests.pop(request_id, None)

    async def complete_request(self, request_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            fut = self._pending_requests.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    async def touch(self, agent_id: str, run_id: str | None = None) -> None:
        async with self._lock:
            info = self._agents.get(agent_id)
            if info is None:
                return
            info.last_seen_at = utcnow()
            if run_id:
                info.running.add(run_id)
                self._run_to_agent[run_id] = agent_id

    async def finish_run(self, run_id: str) -> None:
        async with self._lock:
            agent_id = self._run_to_agent.pop(run_id, None)
            if not agent_id:
                return
            info = self._agents.get(agent_id)
            if info is not None:
                info.running.discard(run_id)
                info.last_seen_at = utcnow()

    def online(self, agent_id: str) -> bool:
        return agent_id in self._agents

    async def _choose_agent(self) -> str | None:
        async with self._lock:
            if not self._agents:
                return None
            return min(self._agents.values(), key=lambda info: len(info.running)).agent_id

    def list_agents(self) -> list[dict[str, Any]]:
        out = []
        for info in self._agents.values():
            out.append({
                "agentId": info.agent_id,
                "name": info.name,
                "os": info.os,
                "connectedAt": info.connected_at.isoformat(),
                "lastSeenAt": info.last_seen_at.isoformat(),
                "running": sorted(info.running),
            })
        return sorted(out, key=lambda x: x["agentId"])


agent_hub = AgentHub()
