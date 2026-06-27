#!/usr/bin/env python3
"""动作探针：用受控任务诱发各类动作，抓取豆包真实输出的 Action 文本，
用于校准 ACTION_ALIASES 与 parser（技术规格 §5.8）。

前提：后端已启动、VLM 已配置。运行：
  python tools/probe_actions.py [--base http://127.0.0.1:8000] [--timeout 600]

输出：每个探针任务的逐步 actionRaw，以及全局出现过的动作名集合，便于发现新写法 / 漂移。
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

# 每个探针：一句必然诱发目标动作的 runContent。可按需扩充 / 换更可控的目标页。
PROBES: list[dict] = [
    {"caseId": "p_click", "runContent": "打开 https://example.com ，点击页面中的 More information 链接，验证进入新页面"},
    {"caseId": "p_scroll", "runContent": "打开 https://en.wikipedia.org/wiki/Web_browser ，向下滚动浏览正文，验证能看到 History 章节"},
    {"caseId": "p_type", "runContent": "打开 https://www.bing.com ，在搜索框输入 playwright 并回车，验证出现搜索结果"},
    {"caseId": "p_hotkey", "runContent": "打开 https://www.bing.com ，点击搜索框输入 hello，使用组合键全选已输入内容，再输入 world"},
    {"caseId": "p_right", "runContent": "打开 https://example.com ，在正文空白处点击右键呼出浏览器上下文菜单，然后按 Esc 关闭"},
    {"caseId": "p_navfail", "runContent": "打开 https://example.com ，寻找一个不存在的‘立即支付’按钮；若确实找不到且无法继续，请调用人工"},
]


def req(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("AIWEB_BASE_URL", "http://127.0.0.1:8009"))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    token = os.getenv("AIWEB_API_TOKEN", "")

    sub = req("POST", f"{base}/api/submissions", token,
              {"submissionName": "action-probe", "items": PROBES})
    sid = sub["submissionId"]
    print(f"已投递探针批次 {sid}，共 {len(PROBES)} 个任务，等待执行…")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        detail = req("GET", f"{base}/api/submissions/{sid}", token)
        if detail["state"] == "done":
            break
        time.sleep(5)

    seen_actions: set[str] = set()
    for probe in PROBES:
        cid = probe["caseId"]
        data = req("GET", f"{base}/api/submissions/{sid}/items/{cid}?include_run=true", token)
        print(f"\n===== {cid} | state={data.get('state')} reason={data.get('statusReason')} =====")
        run = data.get("run") or {}
        for st in run.get("stepList", []):
            seen_actions.add(st.get("action"))
            print(f"  step {st['stepNo']:>2} [{st.get('action')}] raw: {st.get('actionRaw')}")

    print("\n===== 出现过的动作名（用于校准别名表）=====")
    print(sorted(a for a in seen_actions if a))


if __name__ == "__main__":
    main()
