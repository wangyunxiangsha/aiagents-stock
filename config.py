import env_bootstrap  # noqa: F401 — 与 app 入口一致：先加载根目录 .env 并处理 DISABLE_ENV_HTTP_PROXY

import os


def _normalize_deepseek_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return "https://api.deepseek.com/v1"
    # OpenAI 兼容客户端一般要求 base 以 /v1 结尾
    if u.endswith("/v1"):
        return u
    return f"{u}/v1"


# DeepSeek API配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _normalize_deepseek_base_url(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))

# 默认AI模型名称（支持任何OpenAI兼容的模型；兼容 .env 中的 DEEPSEEK_MODEL）
DEFAULT_MODEL_NAME = (
    os.getenv("DEFAULT_MODEL_NAME")
    or os.getenv("DEEPSEEK_MODEL")
    or "deepseek-chat"
)

# 其他配置
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

# 股票数据源配置
DEFAULT_PERIOD = "1y"  # 默认获取1年数据
DEFAULT_INTERVAL = "1d"  # 默认日线数据

# MiniQMT量化交易配置
MINIQMT_CONFIG = {
    'enabled': os.getenv("MINIQMT_ENABLED", "false").lower() == "true",
    'account_id': os.getenv("MINIQMT_ACCOUNT_ID", ""),
    'host': os.getenv("MINIQMT_HOST", "127.0.0.1"),
    'port': int(os.getenv("MINIQMT_PORT", "58610")),
}

# TDX股票数据API配置项目地址github.com/oficcejo/tdx-api
TDX_CONFIG = {
    'enabled': os.getenv("TDX_ENABLED", "false").lower() == "true",
    'base_url': os.getenv("TDX_BASE_URL", "http://192.168.1.222:8181"),
}