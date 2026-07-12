# 架构说明：Browser Agent 主线

AI Web 当前只有一种运行架构：**Server 调度，Browser Agent 执行。**

Server 不直接启动 Playwright 浏览器。它负责 API、数据库队列、节点选择、执行记录、HTML 报告和 Webhook；Agent 在自己的机器上启动浏览器、调用模型、执行动作并回传结果。

## 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| Server | 接收提交、校验、队列、调度、Run/Step 落库、报告、Webhook、站点配置 | 打开浏览器、驱动页面 |
| Browser Agent | 连接 Server、启动 Playwright、执行 VLM 决策、截图、回传步骤和终态 | 生成最终报告、直接写数据库 |
| PostgreSQL | 提交、执行单元、Run、Step、配置、站点元数据 | 存储浏览器进程状态 |
| 存储目录 | 报告、截图、素材 | 登录态的安全托管服务 |
| 维护台 | 查看提交、报告、素材、站点和节点容量 | 代替 Agent 执行任务 |

## 一条任务如何运行

```text
调用方提交 Submission
  → Server 校验 items 和浏览器平台
  → PostgreSQL 队列保存 queued Item
  → Dispatcher 根据 AIWEB_BROWSER_SLOTS 选择在线 Agent
  → Server 创建 Run，并通过 WebSocket 发送 start_run
  → Agent 启动目标浏览器，运行 VLM + Playwright
  → Agent 按步骤回传截图、Thought、动作和心跳
  → Server 保存 RunStep，生成报告，收口 Item / Submission
  → Server 发送 item / submission Webhook
```

## 容量模型

`AIWEB_BROWSER_SLOTS` 是 Server 看到的可用容量，例如：

```json
{
  "mac-01": {"chrome": 1, "firefox": 1, "webkit": 0},
  "win-01": {"chrome": 2, "firefox": 0, "webkit": 0}
}
```

- 节点名必须和对应 Agent 的 `agentId` 一致。
- 每个数值表示该节点允许同时执行的浏览器任务数。
- `0` 表示该节点不提供此浏览器。
- 总并发是所有在线节点的容量之和；离线 Agent 不参与派发。

例如 `win-01.chrome=2` 表示该节点可以同时跑两条 Chrome 任务，不代表浏览器常驻两个窗口；浏览器会随 Run 建立和释放。

## 浏览器平台

提交中的 `platforms` 是浏览器引擎，而不是操作系统：

| 值 | 实际执行器 |
|---|---|
| `chrome` | Playwright Chromium |
| `firefox` | Playwright Firefox |
| `webkit` / `safari` | Playwright WebKit |

同一条任务传入多个平台时，系统会展开为多条独立执行单元，分别生成报告和终态。

## 数据与信任边界

- Server 与 Agent 之间通过鉴权后的 WebSocket 交换任务、截图和登录态注入数据。
- Agent 是受信执行节点：它可能收到素材下载 URL、Cookie、storageState 和 Function Map Context。
- 报告和截图保存在 Server 配置的存储目录，生产环境必须使用持久化卷或对象存储。
- 模型 Key、数据库密码和 API Token 只应放在环境变量或密钥管理系统，不进入 Git。

完整安全要求见 [开源边界与安全](开源边界与安全.md)。

