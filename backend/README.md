# AI Web 后端

FastAPI + PostgreSQL + Browser Agent 实现的自然语言驱动 Web 自动化执行中台。
设计文档见上级 `docs/`。

## 本地运行

前置：Python 3.11+、一个可用的 PostgreSQL（先建库 `aiweb`）。

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env      # 填写 AIWEB_DATABASE_URL 与 AIWEB_VLM_* 
python -m aiweb.main      # 启动 Server，默认 :8009
```

启动时自动建表（`create_all`）、启动调度器与心跳回收器。浏览器执行、登录态页面验证都由独立 Agent 负责。
Server 不安装 Playwright 浏览器二进制，也不启动浏览器驱动。

Agent 分支需要另起一个 Agent；本机测试示例：

```bash
cd backend
python -m pip install -e ".[agent]"
python -m playwright install chromium firefox webkit
.venv/bin/python -m aiweb.agent --server http://127.0.0.1:8009 --agent-id mac-01
```

完整 Agent 执行环境安装见 `../docs/浏览器Agent执行环境安装.md`。

## 冒烟验证

```bash
curl -X POST http://127.0.0.1:8009/api/submissions \
  -H 'Content-Type: application/json' \
  -d '{"submissionName":"smoke","items":[{"caseId":"d1","runContent":"打开 https://example.com 并验证标题包含 Example"}]}'

# 查询（用返回的 submissionId）
curl http://127.0.0.1:8009/api/submissions/<submissionId>
# 报告在响应的 reportUrl / summaryReportUrl
```

CLI：

```bash
python ../cli/aiweb_cli.py submit --content "打开 https://example.com 验证标题包含 Example" --case d1
python ../cli/aiweb_cli.py get <submissionId>
python ../cli/aiweb_cli.py open-report <submissionId> --case d1
```

动作探针（校准豆包真实输出，见技术规格 §5.8）：

```bash
python tools/probe_actions.py
```

## 关键模块

| 路径 | 职责 |
|---|---|
| `aiweb/api/` | 投递 / 查询 / 取消 / 素材 / 配置 路由 |
| `aiweb/scheduler/` | 队列领取（SKIP LOCKED）/ 并发调度 / worker / 心跳回收 |
| `aiweb/kernel/` | VLM 决策循环 + 动作解析/别名 + Playwright 执行内核（由 Agent 进程使用） |
| `aiweb/report/` | 自包含 HTML 报告 |
| `aiweb/webhook/` | 终态回调 |
| `aiweb/storage/` | 截图 / 报告 / 素材 存储（本地卷） |

## 接口一览

- `POST /api/submissions` 投递
- `GET /api/submissions/{id}` 查批次
- `GET /api/submissions/{id}/items/{caseId}?include_run=true` 查单条（含步骤）
- `POST /api/submissions/{id}/cancel`、`POST /api/submissions/{id}/cases/{caseId}/cancel` 取消
- `POST/GET/DELETE /api/assets` 素材库
- `GET/PUT /api/config` 配置（热调 Agent 节点容量 `browser_slots`、有头无头 `headless`）
- `GET /health`、静态 `GET /files/...`（报告 / 截图 / 素材）

> 默认端口 8009（避开本机其他服务）。如需改端口，调 `.env` 的 `AIWEB_PORT` 与 `AIWEB_PUBLIC_BASE_URL`，并同步 `web/vite.config.js` 代理目标。

`AIWEB_PUBLIC_BASE_URL` 是 Server 生成 `reportUrl` / `summaryReportUrl` / 素材 URL 的公开基址。Agent 只负责执行和回传截图，不生成报告链接；测试/生产环境必须把它配置成调用方和浏览器可访问的 AI Web Server 地址，不能保留默认 `http://127.0.0.1:8009`。

鉴权：默认匿名；设置 `AIWEB_API_TOKEN` 后所有 `/api` 需带 `Authorization: Bearer <token>`，Agent 启动时也要传 `--token`。
