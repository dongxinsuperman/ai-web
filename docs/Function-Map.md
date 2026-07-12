# Function Map Context

Function Map Context 是给执行 Agent 的**只读业务参考**。它不替代任务目标，也不会自动修改任务范围；它只是让 Agent 在执行时知道必要的登录规则、业务术语、页面入口或异常处理约定。

## 什么时候需要它

适合放入：

- 测试环境账号的获取方式或登录前提。
- 页面入口、业务术语、固定弹窗或验证码规则。
- 任务目标没有写全、但执行必须知道的环境约束。

不适合放入：

- 要求 Agent 自己猜测、补全或改变的业务目标。
- 大段产品文档、历史聊天记录或和当前任务无关的说明。
- 生产账号、长期有效 token、真实用户信息。

## 两个层级

### 批次级

顶层 `functionMapContext` 对当前 Submission 中的所有任务生效。适合放整批通用信息。

### 执行单元级

`items[].functionMapContext` 只对该 item 生效；一个 item 选择多个浏览器时，会随每个浏览器执行单元一起下发。适合放某条任务专属的页面入口或术语。

最终 Agent 收到的是“批次级文本 + 当前 item 文本”的合并结果。

```json
{
  "submissionName": "release-smoke",
  "functionMapContext": "测试环境登录入口在首页右上角；如已登录则不要重复登录。",
  "items": [
    {
      "caseId": "order-refund",
      "runContent": "进入订单页，筛选已退款订单并验证第一条订单状态。",
      "platforms": ["chrome", "firefox"],
      "functionMapContext": "退款筛选入口位于订单页顶部的状态下拉框。"
    }
  ]
}
```

这里的 Chrome 和 Firefox 都会收到两段参考；其他 item 不会收到“退款筛选入口”这段局部说明。

## 安全边界

Function Map 会保存到执行记录，并下发给受信 Browser Agent。它默认不会出现在 Webhook 中，但仍可能包含敏感业务规则。

- 使用独立测试账号，不要写生产凭据。
- 只把当前任务真正需要的信息放进去。
- 如果部署设置了 `AIWEB_FUNCTION_MAP_CONTEXT_MAX_CHARS`，超限会被请求校验拒绝，不会被静默截断。

关于存储、报告和 Agent 信任边界，见 [开源边界与安全](开源边界与安全.md)。

