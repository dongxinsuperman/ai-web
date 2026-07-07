# functionMapContext 执行单元级支持方案

## 1. 背景

AI Web 当前已经支持批次级 `functionMapContext`。调用方在 `POST /api/submissions` 顶层传入一段只读执行参考，Server 会保存到 `t_aiweb_submission.function_map_context`，worker 创建 Run 时再把它作为 `functionMapContext` 下发给 Agent，最终注入 prompt。

这能覆盖整批公共信息，例如：

- 登录账号、验证码规则、通用弹窗处理。
- 通用业务术语。
- 全局站点入口或环境说明。

但它不适合承载某个 case 专属的信息。比如一批任务里同时有登录、个人资料、订单、支付状态检查。如果只有支付状态检查需要知道“待支付页面入口在 我的-订单-待支付”，现在只能把这段放进顶层 `functionMapContext`，导致所有执行单元都会看到这段支付说明。

这会带来两个问题：

1. 顶层功能地图越来越大，为少数 case 堆入大量局部说明。
2. 执行单元拿到不属于自己的参考，增加无关上下文，降低执行清晰度。

因此需要把 `functionMapContext` 扩展为两层：

- 批次级：整批共享的公共参考。
- 执行单元级：当前 raw item 自己需要的补充参考。

最终 Agent 仍然只收到一份合并后的 `functionMapContext`，不需要知道来源层级。

## 2. 当前 aiweb 架构现状

当前链路和 ai-phone 属于同类架构：

```text
POST /api/submissions
  -> Submission
  -> Item（按 caseId + platform fan-out）
  -> Run
  -> Agent payload
  -> prompt 注入
```

当前已经具备的能力：

- `Submission.function_map_context` 存批次级参考。
- `items[].platforms` 会 fan-out 成多条 `Item`，同一个 `caseId` 可以在不同浏览器引擎各跑一次。
- worker 创建 Run 后，下发 payload 中包含 `functionMapContext`。
- Agent 继续通过 `payload.functionMapContext` 注入 runner。

当前缺口：

- `Item` 没有 `function_map_context` 字段。
- `Run` 没有 `function_map_context` 字段，未保存本次 Run 实际注入文本。
- `parse_and_validate` 只读取顶层 `functionMapContext`，忽略 `items[].functionMapContext`。
- worker 下发时直接使用 `submission.function_map_context`，没有执行单元级合并。
- 顶层 `functionMapContext` 当前按 8000 字硬截断，不是“不限”或显式拒绝。

## 3. 目标行为

支持 `POST /api/submissions` 的每个 `items[]` 单独传入 `functionMapContext`。

语义定义：

- 顶层 `functionMapContext`：批次级公共参考，覆盖本批所有执行单元。
- `items[].functionMapContext`：当前 raw item 的追加参考，随该 raw item fan-out 到每个 `caseId + platform` 执行单元。
- 最终 Run 注入内容：顶层文本 + 当前 item 文本。
- 合并是追加，不是替换、清空或覆盖。
- Agent 下发协议不改，仍然通过现有 `functionMapContext` 字段接收最终文本。

请求示例：

```json
{
  "submissionName": "release-smoke",
  "functionMapContext": "全局：登录入口在首页右上角；测试账号 demo/password",
  "items": [
    {
      "caseId": "login",
      "caseName": "登录检查",
      "runContent": "登录后确认进入首页",
      "platforms": ["chrome"]
    },
    {
      "caseId": "pay-status",
      "caseName": "支付状态检查",
      "runContent": "登录后进入支付状态页，确认待支付状态展示正确",
      "platforms": ["chrome", "firefox"],
      "functionMapContext": "支付：支付状态页入口在 我的-订单-待支付"
    }
  ]
}
```

展开后的执行效果：

- `login + chrome` 只拿到顶层登录说明。
- `pay-status + chrome` 拿到顶层登录说明 + 支付页入口说明。
- `pay-status + firefox` 也拿到顶层登录说明 + 支付页入口说明。

当前不设计平台差异化 function map，不支持类似 `functionMapContextsByPlatform` 或 `chromeFunctionMapContext` 的结构。

## 4. 接口契约调整

`POST /api/submissions` 请求体新增字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `functionMapContext` | 否 | 批次级只读执行参考，覆盖所有展开后的执行单元 |
| `items[].functionMapContext` | 否 | 当前 raw item 的追加只读执行参考，随 `platforms` fan-out 到多个执行单元 |

兼容性：

- 只传顶层 `functionMapContext`：行为和当前一致，所有执行单元共享。
- 只传 `items[].functionMapContext`：只有该 item 展开的执行单元拿到该文本。
- 同时传顶层和 item 级：最终 Run 拿到两段文本的合并结果。
- 不传任何 `functionMapContext`：不注入额外功能地图。

字段校验：

- 字段类型必须是字符串。
- 空字符串或全空白字符串按未传处理。
- 不做自动截断。
- 长度策略见第 8 节。

## 5. 数据模型调整

需要新增字段：

```python
class Item(Base):
    function_map_context: Mapped[str | None] = mapped_column(Text)
```

对应数据库列：

```sql
ALTER TABLE t_aiweb_item
  ADD COLUMN IF NOT EXISTS function_map_context TEXT NULL;
```

建议同时新增 Run 字段，用于保存本次实际注入给 Agent 的最终文本：

```python
class Run(Base):
    function_map_context: Mapped[str | None] = mapped_column(Text)
```

对应数据库列：

```sql
ALTER TABLE t_aiweb_run
  ADD COLUMN IF NOT EXISTS function_map_context TEXT NULL;
```

为什么 Run 也要保存：

- 报告和排查能看到本次 Run 实际注入了什么。
- 重试时可以明确每次 Run 的上下文快照。
- 避免以后修改 Submission 或 Item 后，历史 Run 的真实输入不可还原。

## 6. 合并规则

建议新增一个集中函数处理合并：

```python
def merge_function_map_context(batch_text: str | None, item_text: str | None) -> str | None:
    parts = [
        (batch_text or "").strip(),
        (item_text or "").strip(),
    ]
    merged = "\n\n".join(part for part in parts if part)
    return merged or None
```

执行流：

1. `parse_and_validate` 解析顶层 `functionMapContext`。
2. `parse_and_validate` 解析每条 raw item 的 `functionMapContext`。
3. raw item 按 `platforms` fan-out 成多条 `Item` 时，把 item 级 function map 原样复制到每条 `Item`。
4. worker 创建 Run 时，读取 `Submission.function_map_context` 和 `Item.function_map_context`。
5. 合并后写入 `Run.function_map_context`。
6. payload 下发给 Agent 时使用 `Run.function_map_context`。
7. Agent 和 runner 不需要改协议，只继续消费 `payload.functionMapContext`。

## 7. 需要改造的模块

| 模块 | 当前行为 | 目标行为 |
|---|---|---|
| `backend/aiweb/models/item.py` | 无 item 级 function map 字段 | 增加 `function_map_context` |
| `backend/aiweb/models/run.py` | Run 不保存注入文本 | 增加 `function_map_context`，保存最终合并文本 |
| `backend/aiweb/db.py` | `create_all()` 只覆盖新库，老库不会自动加列 | 增加兼容性 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` |
| `backend/aiweb/scheduler/service.py` | 只解析顶层 `functionMapContext`，并截断 8000 字 | 解析顶层和 item 级字段；不截断；按统一长度策略校验 |
| `backend/aiweb/scheduler/worker.py` | payload 直接使用 `submission.function_map_context` | 创建 Run 时合并 batch + item，保存到 Run，再下发 |
| `backend/aiweb/api/submissions.py` | 查询 item 不返回 function map | 可按需要在详情接口返回 item 级或 run 级 function map，避免列表接口膨胀 |
| `web/src/pages/Submissions.vue` | 新建任务表单没有 function map 输入 | 增加批次级和 item 级输入能力，payload 带上对应字段 |
| `cli/aiweb_cli.py` | CLI 直投不能填写 function map | 增加批次级和 item 级参数；JSON 文件投递天然透传 |
| `docs/对外接口文档（集成方）.md` | 只说明顶层 `functionMapContext` | 补充 `items[].functionMapContext` 语义 |

## 8. 长度限制策略

当前代码会把顶层 `functionMapContext` 静默截断到 8000 字。这个行为需要调整。

建议策略：

- 不做静默截断。
- 默认不设置产品层字符上限。
- 如果后续要限制，使用配置表达，例如 `AIWEB_FUNCTION_MAP_CONTEXT_MAX_CHARS`。
- `max_chars <= 0` 表示不限。
- `max_chars > 0` 时，顶层和 item 级字段都按同一规则校验，超限直接拒绝。

拒绝比截断更稳妥，因为截断后的功能地图可能缺关键步骤，Agent 看到的是不完整说明。

## 9. 不做范围

本阶段不做：

- 不做平台维度 function map。
- 不改 Agent payload 字段名。
- 不改 prompt 注入协议。
- 不改轨迹缓存或历史回放逻辑。
- 不处理模型输出 token 预算。
- 不把大型功能地图拆文件或做动态检索。
- 不把 function map 写入 Webhook，避免账号、验证码规则、内部入口等参考信息外传。
- 不让 function map 参与站点映射或结构化断言判定，避免补充说明意外改变执行环境和断言通道。

## 10. 验收标准

后端验收：

- 只传顶层 `functionMapContext`，所有 fan-out 后的执行单元都拿到顶层文本。
- 只传 `items[].functionMapContext`，只有该 raw item 展开的执行单元拿到 item 文本。
- 同时传顶层和 item 级，`Run.function_map_context` 保存合并后的最终文本。
- 一条 raw item 选择多个 `platforms`，每个平台生成的 `Item` 都保存同一份 item 级文本。
- 非字符串 `functionMapContext` 被拒绝。
- 空字符串按未传处理。
- 不再静默截断 8000 字。

前端验收：

- 新建任务时能填写批次级 function map。
- 新建任务时能填写当前 item 的 function map。
- 提交 payload 包含 `functionMapContext` 和 `items[].functionMapContext`。
- 若后端配置为不限，前端不因 8000 字禁用提交。

CLI 验收：

- `submit --content ...` 可通过参数传批次级 function map。
- `submit --content ...` 可通过参数传当前 item 的 function map。
- `submit --file batch.json` 保持透传完整 JSON。

协议验收：

- Agent 收到的 `payload.functionMapContext` 是最终合并文本。
- Agent、runner、prompt 构造不需要理解两层来源。

## 11. 简化场景说明

可以把顶层 `functionMapContext` 理解成“全班都要看的黑板说明”，把 `items[].functionMapContext` 理解成“某个同学自己桌上的小纸条”。

比如一批任务里有三件事：

- 登录检查。
- 个人资料检查。
- 支付状态检查。

大家都需要知道测试账号，所以账号放顶层。只有支付状态检查需要知道“待支付页面入口”，这段就放到支付 item 里。

执行时：

- 登录任务只看测试账号。
- 个人资料任务只看测试账号。
- 支付任务看测试账号 + 待支付页面入口。

这样每个执行单元拿到的说明刚好够用，不会把所有业务说明都塞给所有任务。
