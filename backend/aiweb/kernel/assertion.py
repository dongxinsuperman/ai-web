"""辅助系统：结构化通道判定 + 二次断言（最终裁决）。

与 ai-phone 的差异（有意精简 / 改进）：
- 通道判定：**纯标签计数 + 必须含「预期结果」**，确定性、可解释、零模型调用；
  去掉 ai-phone 的多信号严格度评分 + 审判模型兜底分类（默认本就关闭、徒增复杂度与不确定性）。
- 二次断言：结构化才触发；输出**严格 JSON**（passed/reason），比 PASS/FAIL/SKIP 文本更易稳定解析；
  独立一次性模型调用（不进主会话缓存），避免"自我背书"；异常/超时回退采纳主模型结果（SKIP 语义）。
"""
from __future__ import annotations

import json
import re

# 四级标签（段头）。命中 ≥2 个且含「预期结果」→ 结构化通道。
_LABELS = ("测试标题", "测试用例", "前置条件", "操作步骤", "测试步骤", "预期结果")
_EXPECTED_LABEL = "预期结果"
_MIN_LABEL_HITS = 2

_SEGMENT_SPLIT_RE = re.compile(
    r"(" + "|".join(re.escape(x) for x in _LABELS) + r")\s*[:：]\s*"
)


def detect_structured(run_content: str) -> tuple[bool, list[str], str]:
    """返回 (是否结构化, 命中的标签列表, 预期结果段文本)。

    结构化条件：命中 ≥2 个四级标签 且 含「预期结果」（否则无可断言对象）。
    """
    text = run_content or ""
    hits = [label for label in _LABELS if label in text]
    structured = len(hits) >= _MIN_LABEL_HITS and _EXPECTED_LABEL in hits
    expected = _extract_expected(text) if structured else ""
    return structured, hits, expected


def _extract_expected(text: str) -> str:
    """按段头标签切分，取「预期结果」段内容。"""
    parts = _SEGMENT_SPLIT_RE.split(text)
    # split 结果形如 [前缀, 标签1, 内容1, 标签2, 内容2, ...]
    for i in range(1, len(parts) - 1, 2):
        if parts[i] == _EXPECTED_LABEL:
            return parts[i + 1].strip()
    return ""


_ASSERT_SYSTEM = (
    "你是 Web 测试的最终断言裁判，严格但不吹毛求疵。职责：依据「预期结果」、双图截图（动作前/后）"
    "和完整步骤摘要，判断任务是否**真的达成**。被测模型的自述不可直接采信，一切以截图证据为准。"
)

_ASSERT_RULE = (
    '输出严格 JSON（只输出 JSON，不要多余文字）：{"passed": true 或 false, "reason": "指出依据/缺哪条证据"}。\n'
    "把「预期结果」拆成若干条，**逐条核对，全部成立才 passed=true**：\n"
    "① 语义等价（措辞/同控件不同表达/不同载体不挑刺；数值「约/前N」可±1）——只判是否达成，不纠文字差异。\n"
    "② 关键事实需硬证据：数值、数量、选中态、开关态、页面名、弹窗态、控件存在性等，**必须在截图里有明确证据**；"
    "**找不到明确证据即视为不成立**（不要给'拿不准'放行）。\n"
    "③ 否定型预期（不存在/没有/不应/无/未显示）：必须有『已充分查看』的证据（入口/菜单已展开、列表到底、已进正确页面）；"
    "无法确认充分查看 → 不成立。\n"
    "④ 伪成功（双图）：自述了明显视觉变化（已跳转/已返回/已切换/已提交）但附图1 与附图2 几乎相同 → 自述不可信，不成立。\n"
    "⑤ 结合步骤摘要：若摘要显示根本没执行到验证所需的操作（如要查某菜单却从未点开/展开），即使截图无异常也视为证据不足 → 不成立。\n"
    "⑥ 证据不足、被遮挡、页面明显不对、与步骤摘要矛盾 → 一律 passed=false。"
)


async def run_final_assertion(
    assistant, expected: str, main_thought: str, final_bytes: bytes,
    before_bytes: bytes | None = None, history: str | None = None,
) -> tuple[str, str]:
    """二次断言（严格：逐条核对 + 需证据 + 双图 + 全局步骤摘要）。

    走辅助模型 assistant.verify_finished（一次性裁决，独立于主会话）。
    返回 (verdict, reason)，verdict ∈ {'pass','fail','skip'}。
    skip = 调用/解析异常或超时，回退采纳主模型 finished 结果（不阻塞 Run 收尾）。
    """
    img_note = (
        "附图1 = 最后一个动作之前的画面，附图2 = 最终画面（验收对象）。"
        if before_bytes
        else "附图 = 最终画面（验收对象）。"
    )
    prompt = (
        f"【预期结果】\n{expected}\n\n"
        f"【完整步骤摘要（每步：序号 动作 思考；用于判断是否真的执行到位）】\n{history or '（无）'}\n\n"
        f"【被测模型自述（仅背景，不可直接采信）】\n{main_thought or '（无）'}\n\n"
        f"{img_note}\n\n{_ASSERT_RULE}"
    )
    try:
        text = await assistant.verify_finished(
            prompt=prompt, prev_before_bytes=before_bytes, final_bytes=final_bytes,
            thinking=True, system=_ASSERT_SYSTEM,
        )
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return "skip", f"断言输出无法解析为 JSON，回退采纳主模型结果。原文：{text[:120]}"
        data = json.loads(m.group(0))
        passed = bool(data.get("passed"))
        reason = str(data.get("reason", "")).strip() or ("达成" if passed else "未达成")
        return ("pass" if passed else "fail"), reason
    except Exception as e:  # 调用/超时/解析异常 → SKIP 回退
        return "skip", f"断言调用异常，回退采纳主模型结果：{e}"
