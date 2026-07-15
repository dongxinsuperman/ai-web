"""system prompt 构建。

- 豆包：文本 DSL（Thought/Action，0-1000 归一化坐标）。
- Claude / GPT computer-use：原生 computer 工具（绝对像素），浏览器导航/终态走文本协议。
按 provider 分派：build_system_prompt_for_backend。
"""
from __future__ import annotations


def build_system_prompt_for_backend(
    provider: str,
    goal: str,
    has_assets: bool = False,
    assets: list[dict] | None = None,
    function_map_context: str | None = None,
    site_directory: str | None = None,
) -> str:
    p = (provider or "doubao").strip().lower()
    if p in ("claude", "anthropic", "openai", "gpt"):
        return build_system_prompt_cu(
            goal, has_assets=has_assets, assets=assets, function_map_context=function_map_context,
            site_directory=site_directory,
        )
    return build_system_prompt(
        goal, has_assets=has_assets, assets=assets, function_map_context=function_map_context,
        site_directory=site_directory,
    )


def _shared_blocks(goal, function_map_context, site_directory, has_assets, assets):
    instruction = (
        f"{goal}\n\n"
        "⚠️ 完成铁律：宣告完成前，必须从当前截图中看到明确视觉证据证明任务已完成。"
        "严禁推测。「可能已完成」「应该已完成」= 未完成，必须继续操作。"
    )
    context_block = (
        f"\n\n## 执行参考（只读上下文，可能含登录规则 / 测试账号 / 业务术语 / 异常处理；"
        f"仅作参考，不改变任务目标）\n{function_map_context.strip()}"
        if function_map_context and function_map_context.strip() else ""
    )
    site_block = (
        f"\n\n## 网址簿（任务未给出明确网址时，按关键字打开对应地址；登录态可能已自动注入）\n{site_directory.strip()}"
        if site_directory and site_directory.strip() else ""
    )
    asset_rows = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        if not name:
            continue
        detail = []
        if asset.get("mime"):
            detail.append(str(asset["mime"]))
        if asset.get("size") is not None:
            detail.append(f"{asset['size']} bytes")
        asset_rows.append(f"- {name}" + (f"（{'，'.join(detail)}）" if detail else ""))
    asset_block = (
        "\n\n## 任务素材清单（只读）\n" + "\n".join(asset_rows)
        if asset_rows
        else ("\n\n## 任务素材\n本任务带有素材，但未取得可用文件清单。" if has_assets else "")
    )
    return instruction, context_block, site_block, asset_block


def build_system_prompt_cu(
    goal: str,
    has_assets: bool = False,
    assets: list[dict] | None = None,
    function_map_context: str | None = None,
    site_directory: str | None = None,
) -> str:
    """Claude / GPT computer-use 的 web 系统提示。

    坐标：绝对像素（相对你看到的截图，**不要**归一化到 0-1000）。
    屏幕交互（点击/输入/滚动/按键）→ 用 computer 工具。
    浏览器导航/标签/上传（工具做不到）→ 输出文本协议 PLATFORM_ACTION 行。
    终态 → 输出文本协议 FINISHED / ASSERT_FAIL / CALL_USER 行。
    """
    instruction, context_block, site_block, asset_block = _shared_blocks(
        goal, function_map_context, site_directory, has_assets, assets
    )
    asset_hint = (
        "\n- 上传文件：输出 `PLATFORM_ACTION: upload_file(name='文件名')`，文件名来自任务素材清单。"
        if has_assets else ""
    )
    return f"""You are operating a desktop web browser to complete a task. You will receive a screenshot each step.

## Task
{instruction}{context_block}{site_block}{asset_block}

## How to act
- Use the `computer` tool for on-screen interactions: click / double_click / right_click / type / scroll / key / drag / wait.
- Coordinates are ABSOLUTE PIXELS relative to the screenshot you see. Do NOT normalize to 0-1000.
- Type text only after clicking the target input to focus it.

## Browser navigation (the computer tool cannot do these) — output a text line, exactly one per line:
- PLATFORM_ACTION: open_url(url='https://...')      open a URL
- PLATFORM_ACTION: refresh()                         reload page
- PLATFORM_ACTION: new_tab()                          open new tab
- PLATFORM_ACTION: switch_tab(tab_id='tab_2')        switch to a tab from browser state
- PLATFORM_ACTION: close_tab()                        close current tab{asset_hint}

## Finishing (output a text line):
- FINISHED: <reason>        task done AND assertion passed
- ASSERT_FAIL: <reason>     assertion failed (expected X, got Y)
- CALL_USER: <reason>       cannot proceed, human needed

## Rules
1. Prefer the computer tool for clicking/typing/scrolling; use PLATFORM_ACTION only for navigation/tabs/upload.
2. A newly opened tab does not automatically become current. Use the browser-state text paired with the screenshot to decide whether to stay or call switch_tab(tab_id='...').
3. If repeating the same action has no effect, change strategy (scroll to find / different spot / check overlay/popup).
4. Before FINISHED, verify the expected result is visible in the current screenshot. No guessing.
5. Keep reasoning concise; you may write thoughts in Chinese."""


def build_system_prompt(
    goal: str,
    has_assets: bool = False,
    assets: list[dict] | None = None,
    function_map_context: str | None = None,
    site_directory: str | None = None,
) -> str:
    instruction = (
        f"{goal}\n\n"
        "⚠️ 完成铁律：使用 finished() 前，必须从当前截图中看到明确视觉证据证明任务已完成。"
        "严禁推测。「可能已完成」「应该已完成」= 未完成，必须继续操作。"
    )
    _, context_block, site_block, asset_block = _shared_blocks(
        goal, function_map_context, site_directory, has_assets, assets
    )
    asset_hint = (
        "\n- 若任务需要上传文件，使用 upload_file(name='文件名')，文件名来自任务提供的素材清单。"
        if has_assets
        else ""
    )
    return f"""你是一个浏览器网页操作助手。你会收到浏览器网页的截图，根据用户指令分析当前页面并决定下一步操作。

## 你的任务
{instruction}{context_block}{site_block}{asset_block}

## 坐标系统
- 使用 0-1000 归一化坐标，左上角 (0,0)，右下角 (1000,1000)。
- 坐标格式：<point>x y</point>，例如 <point>500 500</point> 表示中心。

## 可用动作（每次只输出一个）
### 视觉键鼠
- click(point='<point>x y</point>')              单击
- left_double(point='<point>x y</point>')         双击
- right_single(point='<point>x y</point>')        右键（呼出上下文菜单）
- hover(point='<point>x y</point>')               悬停（展开菜单）
- drag(start_point='<point>x1 y1</point>', end_point='<point>x2 y2</point>')   拖拽
- scroll(point='<point>x y</point>', direction='down')   滚动（up/down/left/right）
- type(content='文本')                            输入文本（先点击输入框聚焦；结尾加 \\n 表示提交）
- select_all_and_type(content='文本')             全选并替换输入
- hotkey(key='ctrl c')                            组合键（空格分隔、小写、≤3 键）

### 浏览器 / 工具
- open_url(url='https://...')                     打开网址
- refresh()                                       刷新
- new_tab() / switch_tab(tab_id='tab_2') / close_tab() 标签管理；标签状态会随截图提供
- upload_file(name='文件名')                       上传素材文件{asset_hint}
- wait()                                          等待页面加载（约 5 秒）

### 终止
- finished(content='完成说明')                    任务完成且断言通过
- assert_fail(content='失败原因')                 断言不通过
- call_user(content='需要人工的原因')             无法继续、需人工介入

## 完成与断言
- 若指令含【断言要求】：通过→finished(content='断言通过：...')；不通过→assert_fail(content='期望X实际Y')。
- 无断言要求：操作完成直接 finished()。

## 输出格式（严格遵守）
Thought: <中文描述你对当前页面的分析与下一步计划>
Action: <一个动作调用>

## 重要规则
1. 每次只能输出一个 Action。
2. 输入文本前必须先 click 输入框聚焦。
3. 连续操作同一位置无效时，换方式（滚动查找 / 换位置 / 检查弹窗遮挡）。
4. 新开标签不会自动成为当前标签；结合截图附带的标签状态，自行决定继续当前页还是 switch_tab(tab_id='...')。
5. 导航 / 刷新 / 切标签等浏览器级操作用对应内置动作，不要点地址栏。
6. Thought 用中文。"""
