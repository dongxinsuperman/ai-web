"""集中配置：从环境变量 / .env 读取（前缀 AIWEB_）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# provider → 端点名（main=决策循环；assistant=一次性裁决，openai 用 chat/completions）
_ENDPOINT_SUFFIX = {
    ("doubao", "main"): "responses", ("doubao", "assistant"): "responses",
    ("claude", "main"): "messages", ("claude", "assistant"): "messages",
    ("anthropic", "main"): "messages", ("anthropic", "assistant"): "messages",
    ("openai", "main"): "responses", ("openai", "assistant"): "chat/completions",
    ("gpt", "main"): "responses", ("gpt", "assistant"): "chat/completions",
}


def _endpoint(provider: str, base_url: str, *, role: str) -> str:
    """base_url（含版本段，如 .../v1 或 .../api/v3）+ 按家端点名 → 完整 URL。幂等。"""
    prov = (provider or "doubao").strip().lower()
    base = (base_url or "").strip().rstrip("/")
    for ep in ("/responses", "/messages", "/chat/completions"):
        if base.endswith(ep):
            base = base[: -len(ep)]
    suffix = _ENDPOINT_SUFFIX.get((prov, role), "responses")
    return f"{base}/{suffix}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIWEB_", env_file=".env", extra="ignore")

    # 服务
    host: str = "0.0.0.0"
    port: int = 8009
    pod_id: str = "local-1"

    # 数据库
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/aiweb"

    # VLM（主模型：决策循环）。AI phone 风格：只配 provider + base_url，端点按家自动推导。
    vlm_provider: str = "doubao"  # doubao | claude | openai
    vlm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"  # API 根（含版本段）
    vlm_api_key: str = ""
    vlm_model: str = ""
    # 各家主 VLM 细项（仅对应 provider 生效）
    vlm_history_window_steps: int = 12        # claude：客户端历史滑窗（步对）
    vlm_main_thinking_budget: int = 0         # claude：thinking 预算 tokens，0=关
    vlm_main_prompt_caching_enabled: bool = False  # claude：prompt caching
    vlm_main_reasoning_effort: str = "medium"  # gpt：low|medium|high

    # 辅助模型（二次断言 / 免登编译；独立于主模型，可不同家）。base/key/model 留空=回退主模型。
    assistant_provider: str = "doubao"  # doubao | claude | openai
    assistant_base_url: str = ""
    assistant_api_key: str = ""
    assistant_model: str = ""

    @property
    def vlm_api_url(self) -> str:
        return _endpoint(self.vlm_provider, self.vlm_base_url, role="main")

    # 辅助模型：自成一套，可与主模型不同家（参考 ai-phone AUX 独立设计）。
    # 仅当“与主模型同家”时，未填的 base/key/model 才安全回退主模型；
    # 跨家时不回退（避免 doubao provider 套 claude base_url 推出错端点）。
    @property
    def assistant_provider_resolved(self) -> str:
        return (self.assistant_provider or self.vlm_provider or "doubao").strip().lower()

    def _assistant_same_family(self) -> bool:
        return self.assistant_provider_resolved == (self.vlm_provider or "doubao").strip().lower()

    @property
    def assistant_api_url(self) -> str:
        base = self.assistant_base_url or (self.vlm_base_url if self._assistant_same_family() else "")
        return _endpoint(self.assistant_provider_resolved, base, role="assistant")

    @property
    def assistant_api_key_resolved(self) -> str:
        return self.assistant_api_key or (self.vlm_api_key if self._assistant_same_family() else "")

    @property
    def assistant_model_resolved(self) -> str:
        return self.assistant_model or (self.vlm_model if self._assistant_same_family() else "")

    # 内核
    max_steps: int = 100
    viewport: str = "1280x800"
    headless: bool = True
    # 反检测：自定义 UA（留空=用浏览器默认并自动抹掉 Headless 标记）
    user_agent: str = ""
    # 浏览器 Agent 节点容量：{"mac-01":{"chrome":1}}。Server 不再本机执行浏览器任务。
    # 需在维护台配置节点，并启动同名 Agent。
    browser_slots: str = "{}"
    cache_enabled: bool = True
    cache_reset_threshold: int = 30000
    # functionMapContext 产品层字符上限；<=0 表示不限，不做静默截断。
    function_map_context_max_chars: int = 0

    # 调度
    run_heartbeat_ttl_sec: int = 300
    poll_interval_ms: int = 500

    # 存储
    storage_dir: str = "./data"
    # Server 生成报告 / 素材的公开访问 URL。部署环境必须显式配置，避免返回 127.0.0.1。
    public_base_url: str = "http://127.0.0.1:8009"

    # Webhook
    webhook_timeout_sec: int = 5

    # 鉴权（留空=匿名）
    api_token: str = ""

    @property
    def viewport_size(self) -> tuple[int, int]:
        try:
            w, h = self.viewport.lower().split("x")
            return int(w), int(h)
        except Exception:
            return 1280, 800


@lru_cache
def get_settings() -> Settings:
    return Settings()
