# Browser Agent 轻量改造方案（历史资料）

> ⚠️ 本文记录从旧 Server 本机浏览器模式迁移到 Agent 模式的过程。迁移已经完成，`codex/browser-agent-mvp` 已不再存在；当前部署请看 [快速开始](快速开始.md) 和 [Agent 主线架构](<架构说明（Agent主线）.md>)。

> 本文记录 AI Web 从“Server 本机起浏览器执行”升级为“Server 分发任务，浏览器 Agent 执行”的轻量方案。
>
> 关键原则：对外提交契约尽量稳定；Agent、节点、Windows 主机、浏览器数量都是 AI Web 内部实现细节。

## 1. 结论

这个改造是可行的，而且比 ai-phone Agent 轻很多。

AI Web 当前已经具备：

- 批次提交、队列、取消、查询、报告、Webhook。
- 多浏览器平台 fan-out：`chrome` / `firefox` / `webkit`。
- Playwright 浏览器执行内核。
- 每个任务独立 Run、Step、截图和 HTML 报告。

所以 Agent 改造不需要重做业务平台，也不需要复刻 ai-phone 的真机、镜像、设备发现、串口、WDA、ADB、HDC、唤醒、安装 App 等重能力。

目标只是把这段链路：

```text
Server 领取任务 -> Server 本机启动浏览器 -> Server 执行 WebVLMRunner -> Server 落报告/回调
```

改成：

```text
Server 领取任务 -> Server 选择在线 Agent -> Agent 启动浏览器 -> Agent 执行 WebVLMRunner -> Server 落报告/回调
```

业务上，调用方仍然只看到：

```text
POST /api/submissions
GET /api/submissions/{id}
Webhook terminal event
报告 URL
```

## 2. 分支策略

建议新开分支实现：

```text
main
  保持当前双端/本机浏览器执行可用

codex/browser-agent-mvp
  开发浏览器 Agent 模式
  允许内部协议和配置模型快速迭代
  稳定后再合回 main
```

这样 `main` 始终是可运行、可部署、可回滚的版本，Agent 分支即使中途调整，也不会污染现有主链路。

## 3. 对外契约不变

Agent 改造不改 `docs/对外接口文档（集成方）.md` 的核心契约。

保持不变：

- `POST /api/submissions`
- `GET /api/submissions/{submissionId}`
- `GET /api/submissions/{submissionId}/items/{caseId}?platform=chrome`
- `POST /api/submissions/{submissionId}/cancel`
- `POST /api/submissions/{submissionId}/cases/{caseId}/cancel`
- `callbackUrl` 回调事件
- `platforms: ["chrome", "firefox", "webkit"]`
- `deviceAliasPools` 继续接受但忽略

尤其不要让调用方感知：

- `win-01`
- Agent ID
- 节点容量
- 浏览器真实运行在哪台机器
- Agent WebSocket 协议

`/api/devices/*` 仍然保持“浏览器槽”视角，用于展示可用性，不作为调用方必须理解的派发入口。

## 4. 业务场景解释

调用方的视角应该始终是：

```text
我要跑一个 Web case
平台选择 chrome
AI Web 跑完告诉我结果
我打开报告看步骤和截图
```

它不应该关心：

```text
是 Server 自己开的 Chrome
还是 win-01 Agent 开的 Chrome
是第 1 个浏览器槽
还是第 6 个浏览器槽
```

也就是说，Agent 改造只是 AI Web 内部执行位置变化，不是对外产品契约变化。

## 5. 目标部署形态

稳定后形态是三个服务角色：

```text
上游平台 / CI / 人工
        |
        v
AI Web Server
  - 接收提交
  - 管理队列
  - 按配置容量派发
  - 接收 Agent 步骤和结果
  - 生成报告
  - Webhook 回调
        |
        v
Browser Agent: win-01
  - 连接 Server
  - 接收 start_run
  - 本机启动 Chrome / Edge / Firefox
  - 执行 WebVLMRunner
  - 上报 step_done / run_done
        |
        v
AI Web Web
  - 展示队列/报告
  - 调整浏览器容量
  - 查看 Agent 在线状态
```

## 6. Server 控制容量，不由 Agent 上报容量

这是与 ai-phone 的关键区别。

目标配置由 Server/Web 控制，例如：

```text
win-01
  Chrome: 6
  Edge: 4
  Firefox: 2

mac-01
  Chrome: 1
```

Agent 不决定“我有几台浏览器”。Agent 只是执行机构：

```text
Agent hello: 我是 win-01，我在线
Server config: win-01 可以承接 6 个 Chrome、4 个 Edge、2 个 Firefox
Server dispatcher: 当前 win-01/chrome 空闲，就派一个 chrome item
Agent: 收到 start_run，执行并回传
```

这样好处是：

- 运维和 Web 控制容量。
- Agent 不需要设备发现。
- 不会把 Agent 做重。
- 调度策略集中在 Server，方便和现有队列模型融合。

## 7. 与 ai-phone 的根本区别

ai-phone Agent 重，是因为它面对的是“真实手机/虚拟机设备池”：

- 设备发现。
- 设备 serial。
- readiness。
- ADB / WDA / HDC。
- 镜像流。
- 设备锁。
- 唤醒、解锁、安装、卸载。
- Agent 上报设备。
- Server 按真实设备派发。

AI Web Browser Agent 不做这些。

AI Web 只需要：

- Agent 在线连接。
- Server 按配置容量派发。
- Agent 本机启动 Playwright 浏览器。
- Agent 回传步骤、截图、终态。

所以它不是“浏览器真机设备池”，而是“浏览器执行进程外移”。

## 8. 当前本机测试与 Agent 执行的关系

这个新分支不再保留 Server 本机执行任务的模式。

即使 Server、Web、Agent 都在同一台电脑，也按三服务模式跑：

```text
Server 接收任务和分发
Web 展示队列和配置容量
Agent 启动 Playwright 浏览器执行
```

本机测试时，Agent ID 可以叫 `mac-01`：

```text
mac-01 Agent 连接本机 Server
Server 配置 mac-01.chrome=1
Chrome 任务派给 mac-01 Agent
```

它仍然不是“真实用户手工浏览器”，还是 Playwright 控制的浏览器。只是如果 Agent 在 Windows、有桌面、有真实 Chrome/Edge、有头模式，环境会比 Linux 容器里的无头 Chromium 更接近普通用户。

## 9. 改造范围

后端：

- `slots.py`：只支持 Agent 节点容量配置。
- `api/config.py`：Web 可配置节点容量。
- `api/devices.py`：对外仍返回浏览器槽聚合视角。
- 新增 `api/agents.py` 或 `api/browser_agent.py`：Agent WS 接入和状态查看。
- `scheduler/dispatcher.py`：从“按平台容量派发”升级为“按节点+平台容量派发”。
- `scheduler/worker.py`：拆出生命周期函数，支持 Agent 回传后复用落库/报告/回调。
- 新增 `agent/` 包：Browser Agent CLI。

前端：

- 配置页从单层浏览器数量，升级为节点表格。
- 队列/设备页可展示 Agent 在线状态。
- 对外调用页面不需要变化。

数据库：

- 可复用 `Run.claimed_by` 记录执行节点，例如 `mac-01` / `win-01`。
- `Run.heartbeat_at` 继续用于回收。
- `RunStep` 建议加 `(run_id, step_no)` 唯一约束，支持 Agent 重连/重发幂等。
- `ConfigKV.value` 当前是 `String(255)`，节点容量 JSON 变大后建议改为 `Text`。

## 10. 执行流程

### 10.1 提交

```text
调用方 -> POST /api/submissions
Server -> 落 Submission / Item
Item.platform = chrome/firefox/webkit
```

不变。

### 10.2 调度

```text
Dispatcher 读取容量配置
Dispatcher 统计 running 数量
Dispatcher 选择可用节点
Dispatcher claim queued item
Dispatcher 创建 Run
```

目标节点必须是在线 Agent，例如 `mac-01` 或 `win-01`。Server 通过 Agent WS 发送 `start_run`。

### 10.3 Agent 执行

```text
Agent 收到 start_run
Agent 下载/准备 assets
Agent 启动本地浏览器
Agent 创建 context/page
Agent 调 WebVLMRunner
Agent 每一步发送 step_done
Agent 结束发送 run_done
```

### 10.4 Server 收口

```text
Server 收到 step_done -> 保存截图/RunStep
Server 收到 run_done -> 更新 Run/Item
Server 生成 HTML 报告
Server 刷新 Submission counts
Server Webhook 回调调用方
```

## 11. 代价评估

整体复杂度：中等。

主要成本不是浏览器执行本身，而是可靠分发：

- Agent WS 连接管理。
- `start_run` 发送失败后的回滚。
- Agent 执行中断后的 heartbeat 回收。
- step 上报幂等。
- cancel 时 Server 通知 Agent 停止。
- assets 从 Server 到 Agent 的传输。
- 报告和截图仍统一落 Server 存储。

不需要做的重能力：

- 不做设备发现。
- 不做真实 serial 语义。
- 不做镜像服务。
- 不做 VM 生命周期。
- 不做 App 安装。
- 不做多协议手机驱动。

## 12. 风险和约束

### 12.1 Agent 离线

Agent 掉线时：

- 未派发任务不受影响。
- 已 running 任务依赖 heartbeat TTL 回收。
- 回收后按 retry 策略重排或失败。

### 12.2 Server 重启

Server 重启后：

- Agent 会重连。
- Server 根据 DB 中 running run 做恢复。
- 简化版本可以先把本 Server 名下 running 任务回收重排。

### 12.3 Agent 重复上报 step

需要 `(run_id, step_no)` 幂等约束，避免重连或网络重试造成重复步骤。

### 12.4 Edge 支持

当前 `chrome` 映射到 Playwright Chromium。

若要支持 Windows Edge，需要新增 `edge` 平台或内部浏览器 channel：

- 对外可以先不开放 `edge`。
- 内部可先给 Agent 配 `chrome` 使用 Chromium/Chrome。
- 稳定后再评估是否在对外平台加 `edge`，这是新增能力，不是 Agent 改造的必要条件。

## 13. 可行性判断

可行。

原因：

- 当前内核 `WebVLMRunner` 只依赖 `BrowserContext/Page/resolve_asset`，适合搬到 Agent。
- 当前报告、回调、队列已经在 Server，有现成收口能力。
- 当前 `Run.claimed_by` 和 `heartbeat_at` 已经接近分布式执行需要。
- Agent 模式不要求调用方改调用。

不建议一开始就追求“完整 ai-phone 感官”。

推荐先做 MVP：

```text
1. 本地模式保持可用
2. Agent 能连接
3. Server 能派一个任务给 Agent
4. Agent 能执行并回传步骤
5. Server 能生成原有报告和回调
6. Web 能配置 win-01 的浏览器容量
```

## 14. MVP 验收

- `main` 分支原有本地执行不受影响。
- 新分支上不改对外清单。
- 不传任何 Agent 配置时，不派发浏览器任务。
- 配置 `win-01.chrome=1` 后，Chrome 任务可派给 Agent。
- Agent 断开后，不再派新任务给它。
- Agent 执行中断后，Server 能回收 running。
- `POST /api/submissions` 响应结构不变。
- Webhook 字段不删、不改名。
- 报告 URL 仍由 Server 生成。
