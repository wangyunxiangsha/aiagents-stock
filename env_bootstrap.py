"""
最早 import：从项目根加载 .env，并可按配置清理「进程内」HTTP 代理环境变量。

典型用途：全局开着 Clash 等代理时，AkShare 访问东财等国内源易异常；在进程内去掉
HTTP_PROXY/HTTPS_PROXY 让 AkShare 走直连；大模型若仍需代理，请在 .env 中配置
LLM_HTTP_PROXY（见 stockapp.common.deepseek_client）。

须在 streamlit、akshare 等会发起网络请求的库之前执行（在 app.py / run.py 首行 import 本模块）。
仅清除环境变量无法绕过系统级 VPN；若仍出现 RemoteDisconnected，多为东财限流/断连，
请配置 TUSHARE_TOKEN 或稍后再试。
"""
from __future__ import annotations

import os
from pathlib import Path

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _clear_process_proxy_env() -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"


def bootstrap() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv(override=True)

    # DISABLE_ENV_HTTP_PROXY：原语义；AKSHARE_DIRECT_NETWORK：AkShare 直连常用别名（等价触发清理）
    if not (_env_truthy("DISABLE_ENV_HTTP_PROXY") or _env_truthy("AKSHARE_DIRECT_NETWORK")):
        return
    _clear_process_proxy_env()
    # 便于对照终端：若从未出现本行，说明 .env 未生效或未开上述开关
    print(
        "[env_bootstrap] 已清除进程内 HTTP/HTTPS/ALL 代理环境变量（AkShare 走直连）；"
        "大模型需代理请配置 LLM_HTTP_PROXY。若仍 RemoteDisconnected，多为东财限流/线路问题，非仅代理。",
        flush=True,
    )


bootstrap()
