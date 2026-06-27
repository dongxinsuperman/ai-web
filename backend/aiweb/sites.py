"""站点映射与免登：解析命中站点、生成网址簿、构建登录态（含 login_api 动态现取）。"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from aiweb.models.site import AUTH_COOKIES, AUTH_LOGIN_API, AUTH_STORAGE_STATE, Site

logger = logging.getLogger("aiweb.sites")
_KW_SPLIT = re.compile(r"[／/|,，、;；\s]+")


def _origin_of(url: str) -> str:
    p = urlparse(url or "")
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return url


def _host_of(url: str) -> str:
    return urlparse(url or "").netloc


def _dig(data, path: str):
    """按点路径从 dict/list 取值，如 'data.token' 或 'shadowToken'。"""
    cur = data
    for part in (path or "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def split_keywords(raw: str) -> list[str]:
    return [k.strip() for k in _KW_SPLIT.split(raw or "") if k.strip()]


async def resolve_sites(session, run_content: str) -> list[Site]:
    """返回 runContent 命中关键字的启用站点（按命中即纳入）。"""
    text = run_content or ""
    rows = (await session.execute(select(Site).where(Site.enabled == True))).scalars().all()  # noqa: E712
    matched: list[Site] = []
    for site in rows:
        for kw in split_keywords(site.keywords):
            if kw and kw in text:
                matched.append(site)
                break
    return matched


def build_directory_text(sites: list[Site]) -> str:
    """生成"网址簿"文本，注入 prompt 供模型 open_url 选对地址。"""
    if not sites:
        return ""
    lines = ["（网址簿：任务涉及以下站点时，用 open_url 打开对应地址）"]
    for s in sites:
        kws = " / ".join(split_keywords(s.keywords))
        lines.append(f"- {kws} → {s.url}")
    return "\n".join(lines)


def merge_storage_state(sites: list[Site]) -> dict | None:
    """合并多个站点的 storageState（cookies + origins）。无则返回 None。"""
    cookies: list = []
    origins: list = []
    found = False
    for s in sites:
        if s.auth_type != AUTH_STORAGE_STATE:
            continue
        payload = s.auth_payload or {}
        ss = payload.get("storage_state") or payload  # 兼容直接存 storageState
        if not isinstance(ss, dict):
            continue
        if ss.get("cookies"):
            cookies.extend(ss["cookies"])
            found = True
        if ss.get("origins"):
            origins.extend(ss["origins"])
            found = True
    if not found:
        return None
    return {"cookies": cookies, "origins": origins}


def collect_cookies(sites: list[Site]) -> list[dict]:
    """汇总 cookies 类站点的 cookie 列表。"""
    out: list[dict] = []
    for s in sites:
        if s.auth_type != AUTH_COOKIES:
            continue
        for c in (s.auth_payload or {}).get("cookies", []) or []:
            if isinstance(c, dict) and c.get("name") and c.get("value"):
                out.append(c)
    return out


async def run_login_recipe(recipe: dict, site_url: str) -> dict:
    """执行配方并返回**详细结果**（含登录接口真实响应，供 agentic 修正反馈）。

    返回 {ok, error, login_response, token, cookies, local_storage}。不抛异常。
    """
    recipe = recipe or {}
    out: dict = {"ok": False, "error": None, "login_response": None,
                 "token": None, "cookies": [], "local_storage": []}
    login_url = recipe.get("login_url")
    if not login_url:
        out["error"] = "配方缺少 login_url"
        return out
    method = (recipe.get("method") or "POST").upper()
    headers = recipe.get("headers") or {}
    payload = recipe.get("payload")
    token_path = recipe.get("token_path") or "token"
    token_prefix = recipe.get("token_prefix") or ""
    try:
        async with httpx.AsyncClient(timeout=20, verify=False) as client:
            resp = await client.request(
                method, login_url, headers=headers,
                json=payload if isinstance(payload, (dict, list)) else None,
                content=None if isinstance(payload, (dict, list)) else payload,
            )
            try:
                login_json = resp.json()
            except Exception:
                login_json = {"_status": resp.status_code, "_text": resp.text[:1000]}
            out["login_response"] = login_json
            if resp.status_code >= 400:
                out["error"] = f"登录接口返回 HTTP {resp.status_code}"
                return out
            token = _dig(login_json, token_path)
            if token is None:
                out["error"] = f"按 token_path='{token_path}' 未在响应里找到 token"
                return out
            token = f"{token_prefix}{token}"
            me = recipe.get("me")
            if me and me.get("url"):
                me_headers = {h: token for h in (me.get("send_headers") or [me.get("header_name", "Authorization")])}
                me_resp = await client.get(me["url"], headers=me_headers)
                if me.get("resp_header"):
                    token = me_resp.headers.get(me["resp_header"], token)
                elif me.get("resp_token_path"):
                    token = _dig(me_resp.json(), me["resp_token_path"]) or token

        inject = recipe.get("inject") or {}
        host = _host_of(site_url) or _host_of(login_url)
        cookies = [{"name": c["name"], "value": token, "domain": c.get("domain") or host, "path": c.get("path") or "/"}
                   for c in inject.get("cookies", []) or [] if c.get("name")]
        local_storage = [{"name": ls["key"], "value": token} for ls in inject.get("local_storage", []) or [] if ls.get("key")]
        out.update(ok=True, token=token, cookies=cookies, local_storage=local_storage)
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


async def execute_login_api(recipe: dict, site_url: str) -> tuple[list[dict], list[dict]]:
    """执行期用：拿 cookies + localStorage；失败抛异常（调用方 best-effort 兜底）。"""
    r = await run_login_recipe(recipe, site_url)
    if not r["ok"]:
        raise RuntimeError(r["error"] or "login_api 执行失败")
    return r["cookies"], r["local_storage"]


async def build_auth_storage_state(sites: list[Site]) -> dict | None:
    """把命中站点的所有免登（static storageState / cookies / login_api 现取）
    统一构建成一份 Playwright storage_state（cookies + origins.localStorage）。
    login_api 失败 best-effort 跳过，不阻断执行。"""
    cookies: list = []
    origins_map: dict[str, list] = {}

    ss = merge_storage_state(sites)
    if ss:
        cookies.extend(ss.get("cookies") or [])
        for o in ss.get("origins") or []:
            origins_map.setdefault(o.get("origin"), []).extend(o.get("localStorage") or [])

    cookies.extend(collect_cookies(sites))

    for s in sites:
        if s.auth_type != AUTH_LOGIN_API:
            continue
        try:
            c, ls = await execute_login_api((s.auth_payload or {}).get("recipe") or {}, s.url)
            cookies.extend(c)
            if ls:
                origins_map.setdefault(_origin_of(s.url), []).extend(ls)
            logger.info("login_api 现取成功 site=%s", s.name)
        except Exception as e:  # best-effort
            logger.warning("login_api 现取失败 site=%s: %s", s.name, e)

    origins = [{"origin": o, "localStorage": ls} for o, ls in origins_map.items() if o and ls]
    if not cookies and not origins:
        return None
    return {"cookies": cookies, "origins": origins}
