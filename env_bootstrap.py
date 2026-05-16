"""
最早 import：从项目根加载 .env，并按 DISABLE_ENV_HTTP_PROXY 清理环境代理。

须在 streamlit、akshare 等会发起网络请求的库之前执行（在 app.py / run.py 首行 import 本模块）。
仅清除环境变量无法绕过系统级 VPN；若仍出现 RemoteDisconnected，多为东财限流/断连，请配置 TUSHARE_TOKEN 或稍后重试。
"""
from __future__ import annotations

import os
from pathlib import Path


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

    v = os.getenv("DISABLE_ENV_HTTP_PROXY", "").strip().lower()
    if v not in ("1", "true", "yes", "on"):
        return
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    # 避免 urllib3/requests 仍按「需代理」解析；与 pop 代理变量配合使用
    os.environ["NO_PROXY"] = "*"


bootstrap()
