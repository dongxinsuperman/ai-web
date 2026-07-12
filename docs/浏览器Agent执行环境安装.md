# Browser Agent 执行环境安装

> 适用当前 `main`。本项目只有 Server 调度、Agent 执行这一种主线架构；文末对旧本机浏览器模式的说明仅是历史对比，不是部署选项。
>
> 本文说明 Agent 模式下浏览器运行环境应该装在哪里，以及 Mac / Windows / Linux Agent 的安装命令。

## 1. 核心结论

Agent 模式下，主任务执行链路变成：

```text
Server 接收任务、排队、分发、生成报告
Agent 接收任务、本机启动浏览器、执行步骤、验证登录态、回传截图和结果
Web 展示队列、报告和节点容量
```

所以浏览器二进制、系统依赖、桌面环境这些“重内容”，应该安装在 **Agent 机器** 上。

Server 不再为了主任务执行承担浏览器镜像重量。Server 核心只需要：

- Python 依赖。
- PostgreSQL 连接。
- VLM 配置。
- Agent WebSocket 接入。
- 报告 / 素材存储。
- `AIWEB_PUBLIC_BASE_URL`：生成报告 / 素材公开 URL 的 Server 地址。

Agent 必须具备：

- Python 3.11+。
- `aiweb` Python 包依赖。
- `aiweb[agent]` 可选依赖。
- Playwright 浏览器二进制。
- 对应系统依赖。
- 如果使用有头模式，需要可显示的桌面环境。

## 2. 角色与安装内容

| 角色 | 是否需要装浏览器二进制 | 说明 |
|---|---:|---|
| Server | 不需要 | 负责 API、队列、派发、报告、Webhook。不会在 Server 本机拉浏览器。 |
| Web | 不需要 | 只是 Vue 管理台。 |
| Agent | 必须 | 真正执行 Playwright 浏览器任务，也负责登录态页面验证。 |

Server 的推荐策略：

```text
不装 Playwright 浏览器二进制。
不启动浏览器驱动。
不做登录态页面探测。
所有需要打开页面的动作都发给 Agent。
```

当前保留的“站点 / 免登”配置期能力：

- `POST /api/sites/verify-auth`
- `POST /api/sites/compile-auth`

这两个接口仍由 Server 暴露，但页面打开验证由 Agent 完成。Server 只负责：

- 生成 / 执行登录 API 配方。
- 把 cookies / localStorage 发给 Agent。
- 接收 Agent 返回的 `finalUrl` / `title` / 打开错误。
- 判断登录态是否有效。

录制登录态接口已删除：

- `POST /api/sites/record/start`
- `POST /api/sites/record/save`
- `POST /api/sites/record/cancel`

## 3. Server 安装

Server 不需要执行 `python -m playwright install ...`，也不需要安装 `aiweb[agent]`。

```bash
cd /Users/dongxin/代码文件/ai-web/aiweb/backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
cp .env.example .env
python -m aiweb.main
```

启动前必须检查 `.env`：

```env
# 本地开发可保留 127.0.0.1；测试/生产必须改成外部可访问域名。
AIWEB_PUBLIC_BASE_URL=http://127.0.0.1:8009
```

这个值只配在 Server 上。报告 HTML、截图、素材链接都由 Server 按这个基址生成；Agent 不生成报告链接，Case Flow 也不会替 Server 修正 `127.0.0.1`。

如果 Server 设置了 `AIWEB_API_TOKEN`，Agent 启动时也要带同一个 token。

## 4. macOS Agent 安装

```bash
cd /Users/dongxin/代码文件/ai-web/aiweb/backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[agent]"
python -m playwright install chromium firefox webkit
```

启动 Agent：

```bash
cd /Users/dongxin/代码文件/ai-web/aiweb/backend
.venv/bin/python -m aiweb.agent --server http://127.0.0.1:8009 --agent-id mac-01
```

如果 Server 有鉴权：

```bash
.venv/bin/python -m aiweb.agent --server http://127.0.0.1:8009 --agent-id mac-01 --token <AIWEB_API_TOKEN>
```

## 5. Windows Agent 安装

PowerShell：

```powershell
cd C:\ai-web\aiweb\backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[agent]"
python -m playwright install chromium firefox webkit
```

启动 Agent：

```powershell
cd C:\ai-web\aiweb\backend
.\.venv\Scripts\python.exe -m aiweb.agent --server http://<Server-IP>:8009 --agent-id win-01
```

如果 Server 有鉴权：

```powershell
.\.venv\Scripts\python.exe -m aiweb.agent --server http://<Server-IP>:8009 --agent-id win-01 --token <AIWEB_API_TOKEN>
```

Windows 上建议先用 `headful` 有头模式压测需要人工观察的网站。Web 配置页里可以切换“浏览器模式”，切换后对下一个任务生效。

## 6. Linux Agent 安装

Linux 需要浏览器二进制，也需要系统依赖。推荐直接用 Playwright 的依赖安装命令：

```bash
cd /opt/aiweb/backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[agent]"
python -m playwright install --with-deps chromium firefox webkit
```

如果系统不允许自动安装依赖，可以先只下载浏览器：

```bash
python -m playwright install chromium firefox webkit
```

然后由运维按 Playwright 提示补齐缺失的系统库。

启动 Agent：

```bash
cd /opt/aiweb/backend
.venv/bin/python -m aiweb.agent --server http://<Server-IP>:8009 --agent-id linux-01
```

## 7. 安装校验

在 Agent 机器执行：

```bash
python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chromium")
    print("chromium ok:", browser.version)
    browser.close()
PY
```

如果需要验证 Firefox / WebKit：

```bash
python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chromium")
    print("chromium", "ok:", browser.version)
    browser.close()
    for name in ("firefox", "webkit"):
        browser = getattr(p, name).launch(headless=True)
        print(name, "ok:", browser.version)
        browser.close()
PY
```

## 8. Web 配置

Agent 启动并连接 Server 后，配置页会从 Server 枚举在线 Agent。

打开：

```text
http://localhost:8010/#/config
```

配置示例：

```text
win-01
  Chrome: 6
  Firefox: 2
  WebKit: 0
```

含义：

```text
Server 最多同时派 6 个 Chrome 任务、2 个 Firefox 任务给 win-01。
Agent 不决定容量，只负责执行。
```

## 9. 常见错误

### 任务一直排队

优先检查：

- Agent 是否在线。
- Web 配置页是否给该 Agent 配了大于 0 的浏览器容量。
- 任务的 `platform` 是否有容量，例如任务是 `chrome`，但 Chrome 配了 0。

### Executable doesn't exist

表示 Agent 机器没有安装对应 Playwright 浏览器。

如果报错路径包含 `chromium_headless_shell`，说明旧 Agent 的无头模式正在找 Playwright 单独的 headless shell。新 Agent 代码会让 Chromium 无头使用普通 Chromium 的 new headless 模式；同步代码后需要重启 Agent。也可以在 Agent 机器补装浏览器：

在 Agent 机器执行：

```bash
python -m playwright install chromium firefox webkit
```

### Linux 缺系统库

在 Agent 机器执行：

```bash
python -m playwright install --with-deps chromium firefox webkit
```

### Agent 连不上 Server

Agent 是主动连接 Server，不需要 Server 主动访问 Agent。

检查：

- Agent 机器能访问 `http://<Server-IP>:8009/health`。
- Server 端口 `8009` 对 Agent 机器开放。
- 如果设置了 `AIWEB_API_TOKEN`，Agent 启动命令必须带 `--token`。

### 访问 127.0.0.1 不符合预期

Agent 在哪台机器上执行，浏览器里的 `127.0.0.1` 就指向哪台机器。

例如 Agent 在 Windows，任务打开：

```text
http://127.0.0.1:5173
```

实际访问的是 Windows 本机的 `5173`，不是 Server 所在机器。

跨机器测试时应使用 Server / 被测系统的局域网 IP 或域名。

## 10. 镜像拆分建议

Agent 模式稳定后，建议拆成两个镜像：

```text
aiweb-server
  轻量镜像
  不内置浏览器二进制
  不安装 aiweb[agent]
  只跑 API、队列、派发、报告

aiweb-agent
  浏览器执行镜像
  基于 Playwright 官方镜像或安装 playwright browsers
  只跑 python -m aiweb.agent
```

这样 Server 镜像不会承担早期 Server 本机浏览器模式中的浏览器重量。

## 11. 实际场景简化

早期 Server 本机浏览器模式像这样：

```text
Server 自己收任务
Server 自己开浏览器
所以 Server 镜像必须带浏览器和系统依赖
```

当前 Agent 主线像这样：

```text
Server 只做调度和报告
Windows Agent 自己装 Chrome / Firefox / WebKit 运行环境
任务派给 Windows Agent 后，由 Windows Agent 开浏览器执行
登录态验证也派给 Windows Agent，由 Windows Agent 打开页面确认
```

所以你的理解是对的：主任务执行所需的浏览器环境，应由 Agent 自己安装和维护。
