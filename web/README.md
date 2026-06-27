# AI Web 前端维护台

Vue3 + Vite + Element Plus 实现的运维维护台：队列 / 执行记录 / 报告 / 素材库 / 配置。

## 运行

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:8010，已代理 /api 与 /files 到后端 :8009
```

需后端先启动（见 `../backend/README.md`）。

## 页面

- **队列 / 执行记录**：批次列表、新建任务（自然语言目标 + 可选素材）、查看明细与报告、取消。
- **素材库**：上传 / 查看 / 删除执行中要用到的文件（被任务的 `assets` 引用）。
- **配置**：热调并发数、上下文缓存开关与分段阈值。

## 配置项（可选 .env）

- `VITE_API_BASE`：直连后端地址（默认走 Vite 代理，留空即可）。
- `VITE_API_TOKEN`：后端开启鉴权时填写。
