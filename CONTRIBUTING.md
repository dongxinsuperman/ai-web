# 参与贡献

欢迎提交 Bug 报告、文档改进、功能建议和代码改动。请先阅读 [开源边界与安全](docs/开源边界与安全.md)。

## 提交前的边界

- 不要提交 `.env`、模型 Key、数据库连接、Cookie、账号密码、token 或真实请求报文。
- 不要提交真实运行报告、网页截图、上传素材、Function Map Context 或包含业务数据的日志。
- 示例请使用 `example.test`、`demo@example.test` 和虚构任务数据。
- 安全漏洞不要公开提交 Issue，见 [安全策略](SECURITY.md)。

## 建议流程

1. 先通过 Issue 描述问题或讨论较大的方案；小型文档修正可以直接提交 Pull Request。
2. 从当前 `main` 创建分支；一个 Pull Request 只解决一个清晰问题。
3. 同步更新受影响的 README、接口说明或环境变量示例。
4. 提交前至少运行与改动相关的检查：后端运行 `ruff check aiweb`，前端运行 `npm run build`。
5. 在 Pull Request 中说明改动目的、验证方式，以及是否影响 API、任务兼容性或部署配置。

## 代码与文档风格

- 保持现有的 Python、Vue 和 Markdown 风格，避免无关格式化。
- 新增配置必须同时提供安全的 `.env.example` 示例和说明。
- 新增任务字段、回调字段或报告字段时，明确兼容性与失败时的行为。

## 提交信息

使用简短、可读的提交信息，例如：`docs: clarify agent setup`、`fix: handle agent reconnect`。

维护者会在合并前确认实现质量、文档、安全边界和兼容性。
