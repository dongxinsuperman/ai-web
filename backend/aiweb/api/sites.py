"""站点映射与免登：CRUD + 录制登录态（headful 一次性手动登录，导出 storageState）。"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from aiweb.db import session_scope
from aiweb.kernel.browser import browser_manager
from aiweb.models.site import Site

router = APIRouter(tags=["sites"])

# 录制会话：token -> {browser, context, created}
_RECORD: dict[str, dict] = {}
_RECORD_TTL = 600  # 10 分钟未保存自动放弃


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


def _to_dict(s: Site) -> dict:
    return {
        "id": s.id, "name": s.name, "keywords": s.keywords, "url": s.url,
        "authType": s.auth_type, "authPayload": s.auth_payload or {},
        "enabled": s.enabled, "updatedAt": s.updated_at.isoformat(),
    }


# ---------------- CRUD ----------------
@router.get("/sites", dependencies=[_guard()])
async def list_sites():
    async with session_scope() as s:
        rows = (await s.execute(select(Site).order_by(Site.created_at.desc()))).scalars().all()
        return [_to_dict(x) for x in rows]


@router.post("/sites", dependencies=[_guard()])
async def create_site(payload: dict = Body(...)):
    if not payload.get("keywords") or not payload.get("url"):
        raise HTTPException(status_code=400, detail="keywords 与 url 必填")
    async with session_scope() as s:
        site = Site(
            name=payload.get("name") or payload["url"],
            keywords=payload["keywords"],
            url=payload["url"],
            auth_type=payload.get("authType", "none"),
            auth_payload=payload.get("authPayload") or {},
            enabled=bool(payload.get("enabled", True)),
        )
        s.add(site)
        await s.flush()
        return _to_dict(site)


@router.put("/sites/{site_id}", dependencies=[_guard()])
async def update_site(site_id: str, payload: dict = Body(...)):
    async with session_scope() as s:
        site = await s.get(Site, site_id)
        if not site:
            raise HTTPException(status_code=404, detail="site not found")
        for key, attr in (("name", "name"), ("keywords", "keywords"), ("url", "url"),
                          ("authType", "auth_type"), ("authPayload", "auth_payload"),
                          ("enabled", "enabled")):
            if key in payload:
                setattr(site, attr, payload[key])
        await s.flush()
        return _to_dict(site)


@router.delete("/sites/{site_id}", dependencies=[_guard()])
async def delete_site(site_id: str):
    async with session_scope() as s:
        site = await s.get(Site, site_id)
        if not site:
            raise HTTPException(status_code=404, detail="site not found")
        await s.delete(site)
        return {"deleted": site_id}


# ---------------- 录制登录态 ----------------
def _gc_records() -> None:
    now = time.time()
    for tok in [t for t, v in _RECORD.items() if now - v["created"] > _RECORD_TTL]:
        sess = _RECORD.pop(tok, None)
        if sess:
            try:
                import asyncio
                asyncio.create_task(sess["browser"].close())
            except Exception:
                pass


@router.post("/sites/record/start", dependencies=[_guard()])
async def record_start(payload: dict = Body(...)):
    """弹出有头浏览器并打开 url，等待用户手动登录。返回 recordToken。"""
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url 必填")
    _gc_records()
    try:
        browser = await browser_manager.launch(headless=False)
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法启动录制浏览器（需有显示器环境）：{e}")
    token = uuid.uuid4().hex[:12]
    _RECORD[token] = {"browser": browser, "context": context, "created": time.time()}
    return {"recordToken": token, "message": "请在弹出的浏览器中手动登录，完成后调用 save"}


@router.post("/sites/record/save", dependencies=[_guard()])
async def record_save(payload: dict = Body(...)):
    """导出当前登录态 storageState 并关闭录制浏览器。"""
    token = payload.get("recordToken")
    sess = _RECORD.pop(token, None)
    if not sess:
        raise HTTPException(status_code=404, detail="录制会话不存在或已过期")
    try:
        state = await sess["context"].storage_state()
    finally:
        try:
            await sess["browser"].close()
        except Exception:
            pass
    return {"storageState": state}


_PARSE_SYSTEM = (
    "你是免登配方编译器。把用户用自然语言描述的『如何为某网站获取并注入登录态』"
    "编译成结构化 JSON 配方。只输出 JSON，不要多余文字。"
)
_PARSE_RULE = """
请输出如下结构（按描述能填的填，填不了的省略）：
{
  "login_url": "登录/取token的接口地址",
  "method": "POST 或 GET",
  "headers": {可选请求头},
  "payload": {登录请求体，如测试账号},
  "token_path": "响应JSON里token的点路径，如 shadowToken 或 data.token",
  "token_prefix": "可选前缀，如 'Bearer '",
  "me": {"url":"可选二次换值接口","header_name":"携带token的请求头名","resp_header":"从响应头取最终值的头名"},
  "inject": {
    "cookies": [{"name":"要写入的cookie名","domain":"可选域"}],
    "local_storage": [{"key":"要写入的localStorage键名"}]
  }
}
要点：token 的值会被自动填到 inject 的 cookies.value 与 local_storage.value；你只需给出键名与取值路径。通用即可，不要绑定具体业务。
"""


@router.post("/sites/parse-auth", dependencies=[_guard()])
async def parse_auth(payload: dict = Body(...)):
    """模糊→精确：把自然语言免登描述在线编译成精确配方（配置期一次性，供用户 review）。"""
    desc = (payload.get("description") or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="description 必填")
    import json
    import re as _re

    from aiweb.kernel.llm import create_assistant

    try:
        text = await create_assistant().chat(
            _PARSE_SYSTEM, [{"type": "text", "text": desc + "\n\n" + _PARSE_RULE}]
        )
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            raise ValueError(f"模型未返回 JSON：{text[:200]}")
        recipe = json.loads(m.group(0))
        return {"recipe": recipe}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析失败：{e}")


def _looks_logged_in(final_url: str, title: str) -> bool:
    low = (str(final_url) + " " + str(title or "")).lower()
    return not any(k in low for k in ("login", "signin", "sign-in", "登录", "登陆"))


async def _open_and_check(cookies: list, local_storage: list, url: str) -> dict:
    """带凭证无头打开站点，返回落点信息。"""
    from aiweb import sites as SITES

    origin = SITES._origin_of(url)
    storage_state = {"cookies": cookies,
                     "origins": [{"origin": origin, "localStorage": local_storage}] if local_storage else []}
    browser = None
    try:
        browser = await browser_manager.launch(headless=True)
        context = await browser_manager.new_context(browser, storage_state=storage_state)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        final_url, title = page.url, await page.title()
        return {"opened": True, "finalUrl": final_url, "title": title, "loggedIn": _looks_logged_in(final_url, title)}
    except Exception as e:
        return {"opened": False, "error": str(e), "finalUrl": None, "title": None, "loggedIn": False}
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


@router.post("/sites/verify-auth", dependencies=[_guard()])
async def verify_auth(payload: dict = Body(...)):
    """真跑一次配方 + 无头打开站点，回报能不能登进去（手动复核已编辑的配方用）。"""
    from aiweb import sites as SITES

    recipe = payload.get("recipe") or {}
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url 必填（站点前端地址）")
    run = await SITES.run_login_recipe(recipe, url)
    if not run["ok"]:
        return {"ok": False, "stage": "login_api", "detail": run["error"], "loginResponse": run.get("login_response")}
    opened = await _open_and_check(run["cookies"], run["local_storage"], url)
    return {
        "ok": opened.get("loggedIn", False), "stage": "done", "tokenObtained": True,
        "cookies": [c["name"] for c in run["cookies"]], "localStorage": [x["name"] for x in run["local_storage"]],
        "finalUrl": opened.get("finalUrl"), "title": opened.get("title"),
        "detail": ("已带凭证打开站点，未落在登录页，鉴权大概率有效" if opened.get("loggedIn")
                   else ("疑似仍在登录页，鉴权可能未生效" if opened.get("opened") else f"打开失败：{opened.get('error')}")),
    }


_COMPILE_SYSTEM = (
    "你是免登配方工程师。用户会用（可能不精确的）自然语言描述某网站怎么登录；"
    "你产出一个 JSON 配方用于自动登录。系统会真实执行并把结果（含登录接口真实响应、落点页面）反馈给你，"
    "你据此修正配方，直到能登进去。每次只输出 JSON 配方，不要多余文字。"
)
_COMPILE_RULE = """
配方字段（按需填，能省则省）：
{
  "login_url": "取token的接口地址",
  "method": "POST 或 GET",
  "headers": {可选},
  "payload": {登录请求体，如账号密码},
  "token_path": "响应JSON里token的点路径（看真实响应定位，如 token / data.token / 某自定义字段）",
  "token_prefix": "可选前缀如 'Bearer '",
  "me": {"url":"可选：需要二次换值时的接口","send_headers":["携带token的请求头名"],"resp_header":"从响应头取最终值的头名","resp_token_path":"或从响应体取"},
  "inject": {"cookies":[{"name":"要写入的cookie名"}],"local_storage":[{"key":"要写入的键名"}]}
}
修正要点：若反馈里给了"登录接口真实响应"，请据此把 token_path 改成真实字段；若已拿到token但落在登录页，考虑 token 是否需要 Bearer 前缀 / 是否需要二次换值 / cookie 名或注入位置是否正确。
"""


@router.post("/sites/compile-auth", dependencies=[_guard()])
async def compile_auth(payload: dict = Body(...)):
    """Agentic 自修正：模糊描述 → 出配方 → 真跑 → 看真实响应/落点 → 修正 → 直到能登进去。"""
    import json
    import re as _re

    from aiweb import sites as SITES
    from aiweb.kernel.llm import create_assistant

    description = (payload.get("description") or "").strip()
    url = payload.get("url")
    if not description or not url:
        raise HTTPException(status_code=400, detail="description 与 url 必填")
    max_iters = int(payload.get("maxIters", 4))

    vc = create_assistant()
    feedback = ""
    last_recipe: dict = {}
    trail: list = []

    for i in range(max_iters):
        user = (
            f"网站前端地址：{url}\n登录方式描述：{description}\n\n{_COMPILE_RULE}"
            + (f"\n\n=== 上一次尝试的反馈（请据此修正）===\n{feedback}" if feedback else "")
        )
        try:
            text = await vc.chat(_COMPILE_SYSTEM, [{"type": "text", "text": user}])
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            recipe = json.loads(m.group(0)) if m else {}
        except Exception as e:
            feedback = f"上次未能产出合法 JSON（{e}），请只输出 JSON 配方。"
            trail.append({"attempt": i + 1, "error": "bad_json"})
            continue
        last_recipe = recipe

        run = await SITES.run_login_recipe(recipe, url)
        if not run["ok"]:
            resp_snip = json.dumps(run.get("login_response"), ensure_ascii=False)[:1500]
            feedback = (f"上次配方：{json.dumps(recipe, ensure_ascii=False)}\n执行失败：{run['error']}\n"
                        f"登录接口真实响应（据此定位 token 字段）：{resp_snip}\n请修正后重试。")
            trail.append({"attempt": i + 1, "error": run["error"]})
            continue

        opened = await _open_and_check(run["cookies"], run["local_storage"], url)
        if opened.get("loggedIn"):
            return {"ok": True, "recipe": recipe, "attempts": i + 1,
                    "finalUrl": opened.get("finalUrl"), "title": opened.get("title"),
                    "detail": "已能登录", "trail": trail}
        resp_snip = json.dumps(run.get("login_response"), ensure_ascii=False)[:1200]
        feedback = (f"上次配方：{json.dumps(recipe, ensure_ascii=False)}\n"
                    f"已拿到 token 并注入 cookies={[c['name'] for c in run['cookies']]}，"
                    f"但打开站点后落在：{opened.get('finalUrl')}（标题：{opened.get('title')}），疑似未登录。\n"
                    f"登录接口响应：{resp_snip}\n请检查 token 是否取对/是否需 Bearer 前缀/是否需二次换值/注入键名或域是否正确，修正配方。")
        trail.append({"attempt": i + 1, "finalUrl": opened.get("finalUrl")})

    return {"ok": False, "recipe": last_recipe, "attempts": max_iters,
            "detail": "多次自修正仍未确认登录，请人工检查/调整配方", "trail": trail}


@router.post("/sites/record/cancel", dependencies=[_guard()])
async def record_cancel(payload: dict = Body(...)):
    token = payload.get("recordToken")
    sess = _RECORD.pop(token, None)
    if sess:
        try:
            await sess["browser"].close()
        except Exception:
            pass
    return {"cancelled": token}
