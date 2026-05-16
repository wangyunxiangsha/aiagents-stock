#!/bin/sh
# Zeabur / Railway 等平台会注入 PORT；本地 Docker 未设置时默认 8503（与 README 一致）
set -e
PORT="${PORT:-8503}"
exec streamlit run app.py --server.port="$PORT" --server.address=0.0.0.0"
