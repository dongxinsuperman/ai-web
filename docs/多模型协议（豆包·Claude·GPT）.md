# AI Web 多模型协议适配层

> 复刻 ai-phone 的 `shared/llm` 设计：主模型（决策循环）与辅助模型（一次性裁决）
> 各自可插拔多家协议；runner 只通过工厂拿实例，不认任何一家细节。
> 换模型只改配置，不动内核。

## 为什么要原生 computer-use（而非通用 VLM 凑合）

长流程 GUI 自动化里，Claude / GPT 的**原生 computer-use** 在点击坐标精度、
多步连贯性上明显优于"让通用视觉模型按 prompt 输出坐标"。所以这里是忠实复刻
ai-phone：每家走自己的 computer-use 协议 + 动作空间 + 解析，而不是统一 DSL。

## 架构

```
kernel/llm/
├── base.py                 # Decision / TokenCounter / BaseMainVLM·BaseAssistant 契约
├── __init__.py             # create_main_vlm / create_assistant 工厂（按需 import）
├── main/
│   ├── doubao_responses.py # 火山方舟 Responses + 主动式缓存 + 服务端续历史（文本 DSL，0-1000）
│   ├── claude_cu.py        # Anthropic Messages + computer_20250124（客户端 messages 滑窗，绝对像素）
│   ├── gpt_cu.py           # OpenAI Responses + computer_use_preview（服务端续历史，绝对像素）
│   └── _cu_common.py       # CU 共用：截图尺寸、键名→Playwright、文本协议解析
└── assistants/
    ├── doubao.py / claude.py / openai.py   # 一次性裁决（二次断言 / 免登编译）
```

- **统一决策结果**：各家 `decide(screenshot_bytes)` 都产出 `Decision.parsed_actions`
  （`list[dict]`，项目统一动作）。runner 零分支消费。
- **坐标空间**：动作 dict 带 `coord_space`。
  - 豆包：`normalized`（0-1000）。
  - Claude / GPT：`absolute`（相对模型所见截图的像素）。
  - 执行层 `ActionExecutor` 按当前帧尺寸（`set_frame`）反缩放回视口像素。
- **会话状态抽象**：`should_reset_session` / `reset_session`。豆包按 token 阈值分段；
  Claude（客户端滑窗）/ GPT（服务端续历史）恒不触发主动分段。

## CU → 浏览器动作映射

屏幕交互走各家 computer 工具；**浏览器导航/标签/上传**（工具做不到）走文本协议
`PLATFORM_ACTION:`；**终态**走 `FINISHED:` / `ASSERT_FAIL:` / `CALL_USER:`。

| computer 动作 | AI Web 动作 |
|---|---|
| left_click / right_click / double_click | click / right_single / left_double |
| left_click_drag / drag(path) | drag |
| type | type |
| scroll(direction, amount) | scroll（scroll_amount 透传，钳 1-10） |
| key / keypress | hotkey（X11 键名 → Playwright 键名） |
| wait / screenshot | wait |
| PLATFORM_ACTION: open_url/refresh/new_tab/switch_tab/close_tab/upload_file | 对应浏览器动作 |

## 配置

```bash
# 主模型
AIWEB_VLM_PROVIDER=doubao          # doubao | claude | openai
AIWEB_VLM_BASE_URL=...             # API 根（含版本段）；端点按 provider 自动推导
                                   #   doubao=BASE/responses · claude=BASE/messages · openai=BASE/responses
AIWEB_VLM_API_KEY=...
AIWEB_VLM_MODEL=...
# 各家细项：AIWEB_VLM_HISTORY_WINDOW_STEPS / VLM_MAIN_THINKING_BUDGET /
#          VLM_MAIN_PROMPT_CACHING_ENABLED（claude）、VLM_MAIN_REASONING_EFFORT（openai）

# 辅助模型（可与主模型不同家）
AIWEB_ASSISTANT_PROVIDER=doubao
AIWEB_ASSISTANT_BASE_URL= / API_KEY= / MODEL=  # 留空回退主模型（端点同样按家推导）
```

## 现状

- **豆包**：默认，已端到端实测（通道判定 → open_url → finished → success）。
- **Claude / GPT computer-use**：按 ai-phone 已验证实现忠实移植、编译通过；
  本机无对应 API key，未做线上实跑，接入真实 key 后即可用。
