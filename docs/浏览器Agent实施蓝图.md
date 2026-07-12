# Browser Agent 实施蓝图（历史资料）

> ⚠️ 本文是 Agent MVP 的实施过程记录。迁移已经完成，`main` 已是 Agent 主线，`codex/browser-agent-mvp` 已删除。当前使用 [快速开始](快速开始.md)、[Agent 主线架构](<架构说明（Agent主线）.md>) 和 [对外接口](<对外接口文档（集成方）.md>)。

> 原则：Agent 改造是内部执行架构升级，不改变对外 Submission / Webhook 契约。

## 1. 分支边界

开发分支：

```text
codex/browser-agent-mvp
```

主分支：

```text
main
```

分支策略：

- `main` 保持当前本机浏览器执行可用。
- Agent 所有代码、配置、文档先进入 `codex/browser-agent-mvp`。
- MVP 稳定后再合并回 `main`。
- 合并前必须验证 Agent 三服务模式可启动，对外提交接口仍可用。

## 2. 不变项

这些接口和语义不变：

```text
POST /api/submissions
GET /api/submissions
GET /api/submissions/{submissionId}
GET /api/submissions/{submissionId}/items/{caseId}?include_run=true&platform=chrome
POST /api/submissions/{submissionId}/cancel
POST /api/submissions/{submissionId}/cases/{caseId}/cancel
GET /api/devices/statuses
GET /api/devices/available
```

请求体不新增必填字段。

`platforms` 仍然是浏览器平台：

```json
["chrome", "firefox", "webkit"]
```

`deviceAliasPools` 仍然接受但忽略。

Webhook 不删除现有字段，不改变事件名：

```text
submission.item.terminal
submission.terminal
```

## 3. 目标执行流程

### 3.1 当前流程

```text
Dispatcher
  -> claim queued Item
  -> run_item(item_id)
  -> create Run
  -> Server 本机 launch browser
  -> WebVLMRunner
  -> persist RunStep
  -> finalize Run/Item/Submission
  -> report + webhook
```

### 3.2 Agent 流程

```text
Dispatcher
  -> read node browser capacity
  -> choose online Agent
  -> claim queued Item
  -> create Run(claimed_by=agent_id)
  -> send start_run over WS

Agent
  -> launch browser on Agent host
  -> WebVLMRunner
  -> send step_done
  -> send run_done

Server
  -> persist RunStep
  -> heartbeat Run
  -> finalize Run/Item/Submission
  -> report + webhook
```

## 4. 配置模型

### 4.1 配置格式

新分支只支持 Agent 节点容量配置：

```json
{
  "mac-01": {
    "chrome": 1,
    "firefox": 0,
    "webkit": 0
  },
  "win-01": {
    "chrome": 6,
    "firefox": 2
  }
}
```

Server 不再支持把 `{chrome:2}` 解释成本机执行容量。本机测试也要启动一个同名 Agent。

### 4.2 内部读取函数

建议在 `backend/aiweb/slots.py` 增加：

```python
def parse_node_slots(text: str | None) -> dict[str, dict[str, int]]
def flatten_slots(node_slots: dict[str, dict[str, int]]) -> dict[str, int]
async def get_node_slots(session) -> dict[str, dict[str, int]]
async def get_slots(session) -> dict[str, int]  # 保留，对外聚合使用
```

`get_slots()` 继续返回聚合结果：

```json
{"chrome": 8, "firefox": 3, "webkit": 1}
```

这样 `api/devices.py`、提交校验等对外视角仍可读取聚合后的浏览器容量。

## 5. 数据库调整

### 5.1 复用字段

`Run.claimed_by`：

```text
mac-01
win-01
```

`Run.heartbeat_at`：

```text
Server 本地 worker 或 Agent 都持续更新
```

### 5.2 建议迁移

`ConfigKV.value` 当前为 `String(255)`，节点配置 JSON 可能超过 255。

建议迁移：

```sql
ALTER TABLE t_aiweb_config ALTER COLUMN value TYPE TEXT;
```

`RunStep` 增加幂等约束：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_aiweb_run_step_run_step
ON t_aiweb_run_step (run_id, step_no);
```

Python 模型同步：

- `backend/aiweb/models/config.py`：`value` 改为 `Text`。
- `backend/aiweb/models/run.py`：`RunStep.__table_args__` 增加 unique index 或 constraint。

## 6. Server AgentHub

新增：

```text
backend/aiweb/agent_hub.py
```

职责：

- 管理在线 Agent WebSocket。
- 记录 `agent_id -> connection`。
- 记录 `run_id -> agent_id`。
- 提供 `send_start_run()`。
- 提供 `send_stop_run()`。
- 提供在线状态给 Web。

建议结构：

```python
class AgentInfo:
    agent_id: str
    name: str | None
    os: str | None
    connected_at: datetime
    last_seen_at: datetime
    running: set[str]

class AgentHub:
    async def register(agent_id, websocket, meta): ...
    async def unregister(agent_id): ...
    def online(agent_id) -> bool: ...
    async def send(agent_id, payload): ...
    def bind_run(run_id, agent_id): ...
    def unbind_run(run_id): ...
    def list_agents() -> list[dict]: ...
```

## 7. WebSocket 协议

### 7.1 连接

```http
GET /api/browser-agents/ws
Authorization: Bearer <AIWEB_API_TOKEN>  # 如配置
```

也可以第一版使用：

```http
GET /api/browser-agents/ws?token=...
```

方便 Agent CLI 连接。

### 7.2 Agent hello

Agent 不上报容量。

```json
{
  "type": "hello",
  "agentId": "win-01",
  "name": "Windows Office 01",
  "os": "Windows"
}
```

### 7.3 Server start_run

```json
{
  "type": "start_run",
  "runId": "run_123",
  "itemId": "item_123",
  "submissionId": "sub_123",
  "caseId": "cf-001",
  "platform": "chrome",
  "runContent": "打开系统并验证...",
  "assets": [
    {
      "name": "avatar.png",
      "url": "http://server/files/assets/avatar.png"
    }
  ],
  "functionMapContext": "...",
  "siteDirectory": "...",
  "storageState": {
    "cookies": [],
    "origins": []
  },
  "headless": false,
  "viewport": {
    "width": 1280,
    "height": 800
  }
}
```

### 7.4 Agent step_done

第一版截图可以用 base64，后续再改上传接口。

```json
{
  "type": "step_done",
  "runId": "run_123",
  "step": {
    "step_no": 1,
    "action": "click",
    "thought": "...",
    "action_raw": "...",
    "action_detail": {},
    "screenshot_before_b64": "...",
    "screenshot_after_b64": "...",
    "token_usage": {},
    "elapsed_ms": 1200
  }
}
```

### 7.5 Agent heartbeat

```json
{
  "type": "heartbeat",
  "runId": "run_123"
}
```

### 7.6 Agent run_done

```json
{
  "type": "run_done",
  "runId": "run_123",
  "status": "success",
  "steps": 8,
  "tokenUsage": {},
  "elapsedMs": 62000,
  "failReason": null,
  "finishContent": "...",
  "segments": 1
}
```

### 7.7 Server stop_run

```json
{
  "type": "stop_run",
  "runId": "run_123",
  "reason": "cancelled"
}
```

## 8. Worker 生命周期拆分

当前 `scheduler/worker.py` 的 `run_item()` 同时负责：

- 创建 Run。
- 构建站点上下文。
- 启动浏览器。
- 执行 runner。
- 持久化步骤。
- finalize。

需要拆出可复用函数：

```python
async def create_run_for_item(item_id: str, claimed_by: str) -> RunStartPayload
async def persist_run_step(run_id: str, payload: dict) -> None
async def heartbeat_run(run_id: str) -> None
async def should_cancel_item(item_id: str) -> bool
async def finalize_run(run_id: str, result: RunResultLike) -> None
```

本地执行继续使用这些函数：

```python
payload = await create_run_for_item(item_id, claimed_by=agent_id)
await agent_hub.send_start_run(agent_id, payload)
```

## 9. Dispatcher 改造

### 9.1 当前逻辑

当前按平台计数：

```text
chrome cap=2
running chrome=1
claim one chrome
run_item(item_id)
```

### 9.2 目标逻辑

按节点和平台计数：

```text
mac-01 chrome cap=1
win-01 chrome cap=6

统计 running:
  claimed_by=mac-01, platform=chrome
  claimed_by=win-01, platform=chrome

若 win-01 在线且未满:
  claim chrome item
  create run claimed_by=win-01
  send start_run
```

### 9.3 没有 local 特殊节点

新分支没有 Server 本机执行兜底：

- 不配置 Agent 容量时，不派发。
- Agent 不在线时，不派发给该节点。
- 本机测试也要启动 `mac-01` 这类 Agent。

## 10. Agent CLI

新增：

```text
backend/aiweb/agent/__init__.py
backend/aiweb/agent/main.py
backend/aiweb/agent/client.py
backend/aiweb/agent/assets.py
```

启动命令：

```bash
python -m aiweb.agent \
  --server http://127.0.0.1:8009 \
  --agent-id win-01 \
  --token xxx
```

Agent 职责：

- 连接 Server WS。
- 发送 hello。
- 接收 start_run。
- 下载 assets 到临时目录。
- 启动本机 Playwright 浏览器。
- 调用 `WebVLMRunner`。
- step 回传 Server。
- run_done 回传 Server。
- 收到 stop_run 时设置 cancel flag。

Agent 不职责：

- 不上报容量。
- 不管理队列。
- 不生成最终 HTML 报告。
- 不发调用方 webhook。
- 不决定调度。

## 11. assets 处理

Server start_run 中带：

```json
{"name": "avatar.png", "url": "http://server/files/assets/avatar.png"}
```

Agent 下载到临时目录：

```text
/tmp/aiweb-agent-assets/{runId}/avatar.png
```

Agent `resolve_asset(name)` 返回本地路径。

执行结束后清理该 run 的临时目录。

## 12. 站点免登处理

Server 侧在 `create_run_for_item()` 中完成：

- `resolve_sites()`
- `build_directory_text()`
- `build_auth_storage_state()`

然后把以下数据发给 Agent：

- `siteDirectory`
- `storageState`

这样 Agent 不需要访问 Server 数据库。

## 13. 前端改造

配置页从当前单层：

```text
Chrome: 2
Firefox: 1
Safari: 1
```

升级为节点表：

```text
节点       Chrome   Firefox   WebKit
mac-01     1        0         0
win-01     6        2         0
```

操作：

- 新增节点。
- 删除节点。
- 修改每个节点各浏览器容量。
- 展示在线状态。

注意：

- 外部文档不改。
- `devices` 对外仍聚合成浏览器槽。
- Web 内部可以新增 Agent 状态页。

## 14. 验收路径

### P0：三服务启动

- Server 可启动。
- Web 可启动。
- Agent 可连接 Server。
- 不启 Agent 时，Server 不执行浏览器任务。

### P1：Agent 连接

- Agent CLI 能连上 Server。
- Server 能看到 `win-01 online`。
- Agent 断开后状态变 offline。

### P2：远端派发

- 配置 `win-01.chrome=1`。
- 提交 chrome case。
- Server 创建 Run，`claimed_by=win-01`。
- Agent 执行。
- Server 收到 step。
- Server 生成报告和回调。

### P3：取消和回收

- running item 取消后，Server 发 `stop_run`。
- Agent 停止执行。
- item 进入 `cancelled`。
- Agent 执行中断后，heartbeat 超时能回收。

### P4：并发

- 配置 `win-01.chrome=2`。
- 同时提交 3 条 chrome。
- 最多 2 条 running。
- 完成一条后继续派下一条。

### P5：对外契约确认

- `POST /api/submissions` 响应结构不变。
- `GET /api/submissions/{id}` 字段不删。
- webhook 字段不删。
- `deviceAliasPools` 仍可传且不影响派发。
- `/api/devices/available` 不要求调用方理解 Agent。

## 15. 回滚策略

如果 Agent 分支不稳定：

- 不合回 main。
- main 继续保持当前本地执行。

如果合回后需要临时关闭 Agent：

```json
{
  "browser_slots": {
    "mac-01": {"chrome": 1, "firefox": 0, "webkit": 0}
  }
}
```

> 历史说明：本文编写时曾建议通过切回旧 `main` 回滚。当前仓库已经只有 Agent 主线；不再支持把 Server 本机浏览器模式作为运行选项。

## 16. 实际业务解释

对调用方来说，前后都是同一句话：

```text
帮我跑这个 Web case，平台 chrome，完成后回调。
```

AI Web 内部以前是：

```text
Server 自己开浏览器跑。
```

迁移后的运行模式变成：

```text
Server 派给 win-01，win-01 开浏览器跑。
```

调用方不需要新增字段，也不需要知道 `win-01`。这就是本次改造最重要的产品边界。
