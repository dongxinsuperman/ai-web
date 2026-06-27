# AI Web 后端

FastAPI + Playwright + PostgreSQL 实现的自然语言驱动 Web 自动化执行中台。
设计文档见上级 `docs/`。

## 本地运行

前置：Python 3.11+、一个可用的 PostgreSQL（先建库 `aiweb`）。

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m playwright install chromium

cp .env.example .env      # 填写 AIWEB_DATABASE_URL 与 AIWEB_VLM_* 
python -m aiweb.main      # 启动服务，默认 :8009
```

启动时自动建表（`create_all`）、拉起浏览器、调度器与心跳回收器。

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
| `aiweb/kernel/` | VLM 决策循环 + 动作解析/别名 + Playwright 执行 + 上下文缓存 |
| `aiweb/report/` | 自包含 HTML 报告 |
| `aiweb/webhook/` | 终态回调 |
| `aiweb/storage/` | 截图 / 报告 / 素材 存储（本地卷） |

## 接口一览

- `POST /api/submissions` 投递
- `GET /api/submissions/{id}` 查批次
- `GET /api/submissions/{id}/items/{caseId}?include_run=true` 查单条（含步骤）
- `POST /api/submissions/{id}/cancel`、`POST /api/submissions/{id}/cases/{caseId}/cancel` 取消
- `POST/GET/DELETE /api/assets` 素材库
- `GET/PUT /api/config` 配置（热调每引擎台数 `browser_slots`、有头无头 `headless`）
- `GET /health`、静态 `GET /files/...`（报告 / 截图 / 素材）

> 默认端口 8009（避开本机其他服务）。如需改端口，调 `.env` 的 `AIWEB_PORT` 与 `AIWEB_PUBLIC_BASE_URL`，并同步 `web/vite.config.js` 代理目标。

鉴权：默认匿名；设置 `AIWEB_API_TOKEN` 后所有 `/api` 需带 `Authorization: Bearer <token>`。
