#!/bin/sh
# Zeabur / Railway inject PORT; default 8503 for local Docker (see README)
set -e
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONUNBUFFERED=1
export STREAMLIT_SERVER_HEADLESS=true
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

PORT="${PORT:-8503}"
exec streamlit run app.py --server.port="$PORT" --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false
