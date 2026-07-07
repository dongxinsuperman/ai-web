#!/usr/bin/env python3
"""AI Web CLI —— 基于对外 API 的薄封装（仅用标准库，无第三方依赖）。

用法示例：
  python aiweb_cli.py submit --content "打开 https://example.com 并验证标题包含 Example" --case demo1
  python aiweb_cli.py submit --file batch.json
  python aiweb_cli.py get <submissionId>
  python aiweb_cli.py cancel <submissionId> [--case demo1]
  python aiweb_cli.py open-report <submissionId> [--case demo1]

环境变量：
  AIWEB_BASE_URL   服务地址（默认 http://127.0.0.1:8009）
  AIWEB_API_TOKEN  若服务端开启鉴权则需提供
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import webbrowser

BASE_URL = os.getenv("AIWEB_BASE_URL", "http://127.0.0.1:8009").rstrip("/")
API_TOKEN = os.getenv("AIWEB_API_TOKEN", "")


def _optional_text(value: str | None, file_path: str | None) -> str | None:
    if file_path:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    return value


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        print(f"[HTTP {e.code}] {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[连接失败] {e}", file=sys.stderr)
        sys.exit(1)


def cmd_submit(args: argparse.Namespace) -> None:
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        if not args.content:
            print("需要 --content 或 --file", file=sys.stderr)
            sys.exit(2)
        item = {"caseId": args.case or "case-1", "caseName": args.name, "runContent": args.content}
        item_fmc = _optional_text(args.item_function_map_context, args.item_function_map_file)
        if item_fmc:
            item["functionMapContext"] = item_fmc
        if args.asset:
            item["assets"] = args.asset
        payload = {"submissionName": args.name, "callbackUrl": args.callback, "items": [item]}
        fmc = _optional_text(args.function_map_context, args.function_map_file)
        if fmc:
            payload["functionMapContext"] = fmc
    out = _request("POST", "/api/submissions", payload)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_get(args: argparse.Namespace) -> None:
    out = _request("GET", f"/api/submissions/{args.submission_id}")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_cancel(args: argparse.Namespace) -> None:
    if args.case:
        out = _request("POST", f"/api/submissions/{args.submission_id}/cases/{args.case}/cancel")
    else:
        out = _request("POST", f"/api/submissions/{args.submission_id}/cancel")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_open_report(args: argparse.Namespace) -> None:
    out = _request("GET", f"/api/submissions/{args.submission_id}")
    url = None
    if args.case:
        for it in out.get("items", []):
            if it.get("caseId") == args.case:
                url = it.get("reportUrl")
                break
    else:
        url = out.get("summaryReportUrl")
    if not url:
        print("报告尚未生成", file=sys.stderr)
        sys.exit(1)
    print(url)
    webbrowser.open(url)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aiweb", description="AI Web CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="投递任务")
    s.add_argument("--content", help="自然语言目标 runContent")
    s.add_argument("--case", help="caseId")
    s.add_argument("--name", help="批次 / 用例名")
    s.add_argument("--callback", help="Webhook 回调 URL")
    s.add_argument("--asset", action="append", help="引用素材名（可多次）")
    s.add_argument("--function-map-context", help="批次级只读执行参考")
    s.add_argument("--function-map-file", help="从文件读取批次级只读执行参考")
    s.add_argument("--item-function-map-context", help="当前 case 的只读执行参考")
    s.add_argument("--item-function-map-file", help="从文件读取当前 case 的只读执行参考")
    s.add_argument("--file", help="批次 JSON 文件（与 --content 二选一）")
    s.set_defaults(func=cmd_submit)

    g = sub.add_parser("get", help="查询批次")
    g.add_argument("submission_id")
    g.set_defaults(func=cmd_get)

    c = sub.add_parser("cancel", help="取消批次或单条")
    c.add_argument("submission_id")
    c.add_argument("--case", help="只取消该 caseId")
    c.set_defaults(func=cmd_cancel)

    o = sub.add_parser("open-report", help="打开报告")
    o.add_argument("submission_id")
    o.add_argument("--case", help="打开该 caseId 的报告，否则打开批次汇总")
    o.set_defaults(func=cmd_open_report)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
