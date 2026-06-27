# AI Web（个人开源版）

**AI Web 是一个用自然语言驱动浏览器执行 Web 自动化的独立平台——"Web 版的、精简的 ai-phone"。**

提交一段自然语言目标，平台用 VLM 视觉大模型驱动内置浏览器把任务跑出来，产出自包含报告，并通过 Webhook 回调结果。它只有「队列 / 并发执行 / 执行记录 / 接口与回调」，没有用例、项目、步骤等测试平台概念。

## 核心特性

- **自然语言直跑**：输入目标即执行，无需编写用例或脚本。
- **单服务自包含**：内置 Playwright 本地浏览器，一台机器即可部署，不依赖远程 Selenium Grid。
- **并发执行**：可配置 N 个并发槽，队列调度，谁先空闲谁先跑。
- **对外接口 + Webhook + CLI**：可被 CI / 平台 / 脚本集成。
- **自包含 HTML 报告**：每步截图、Thought、动作、结果可追溯。

## 技术栈

- 后端：FastAPI + Playwright + SQLAlchemy 2.0 + PostgreSQL
- 前端：Vue3 + Vite + Element Plus + Pinia
- 打包：Docker（基于 `mcr.microsoft.com/playwright/python`）

## 文档

- 总体方案：[`docs/方案设计（总体方案）.md`](./docs/方案设计（总体方案）.md)
- 实施蓝图（技术规格）：[`docs/实施蓝图（技术规格）.md`](./docs/实施蓝图（技术规格）.md)
- 里程碑与实施计划：[`docs/里程碑与实施计划.md`](./docs/里程碑与实施计划.md)
- 对外接口文档（集成方）：[`docs/对外接口文档（集成方）.md`](./docs/对外接口文档（集成方）.md)
- 辅助系统（结构化通道与二次断言）：[`docs/辅助系统（结构化通道与二次断言）.md`](./docs/辅助系统（结构化通道与二次断言）.md)
- 站点映射与免登：[`docs/站点映射与免登.md`](./docs/站点映射与免登.md)

## 项目结构

```text
AI Web个人/
├── docs/        设计文档（方案 / 技术规格 / 里程碑）
├── backend/     FastAPI + Playwright + PG 执行中台（见 backend/README.md）
├── web/         Vue3 + Vite 维护台（见 web/README.md）
└── cli/         命令行（aiweb_cli.py）
```

## 状态

V1 主链路已实现：自然语言投递 → 队列（SKIP LOCKED 多 Pod 安全）→ 并发执行（Playwright 内核 +
主动式上下文缓存）→ 自包含 HTML 报告 → Webhook 回调；含素材库、CLI、动作探针、前端维护台。

待接入运行环境后联调：需 Python 3.11 环境 + PostgreSQL + 火山方舟 VLM 接入点。
辅助系统（审判 / 断言模型）、执行回放缓存（走 ai-phone V2 路线）、实时镜像不在 V1 范围。
