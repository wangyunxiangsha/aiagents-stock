# -*- coding: utf-8 -*-
"""已执行过的一次性迁移脚本：创建 stockapp/、移动模块、改写 import。

⚠️ 不要再次运行：源文件已不在仓库根目录，重复执行会报错或破坏路径。
如需在新分支重做，请先 git checkout 到迁移前的提交再运行。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative_source, package_subdir) — filenames unchanged
MOVES: list[tuple[str, str]] = []

COMMON = [
    "database.py",
    "deepseek_client.py",
    "config_manager.py",
    "model_config.py",
    "data_source_manager.py",
    "miniqmt_interface.py",
]
CORE = [
    "stock_data.py",
    "ai_agents.py",
    "pdf_generator.py",
    "pdf_generator_fixed.py",
    "pdf_generator_pandoc.py",
    "quarterly_report_data.py",
    "fund_flow_akshare.py",
    "market_sentiment_data.py",
    "risk_data_fetcher.py",
    "qstock_news_data.py",
    "news_announcement_data.py",
]
MONITORING = [
    "monitor_manager.py",
    "monitor_service.py",
    "monitor_db.py",
    "monitor_ui.py",
    "monitor_scheduler.py",
    "notification_service.py",
]
MAIN_FORCE = [
    "main_force_selector.py",
    "main_force_analysis.py",
    "main_force_ui.py",
    "main_force_pdf_generator.py",
    "main_force_batch_db.py",
    "main_force_history_ui.py",
]
SECTOR = [
    "sector_strategy_data.py",
    "sector_strategy_agents.py",
    "sector_strategy_engine.py",
    "sector_strategy_ui.py",
    "sector_strategy_pdf.py",
    "sector_strategy_db.py",
    "sector_strategy_scheduler.py",
]
LONGHUBANG = [
    "longhubang_data.py",
    "longhubang_db.py",
    "longhubang_agents.py",
    "longhubang_engine.py",
    "longhubang_ui.py",
    "longhubang_pdf.py",
    "longhubang_scoring.py",
]
NEWS_FLOW = [
    "news_flow_data.py",
    "news_flow_model.py",
    "news_flow_sentiment.py",
    "news_flow_agents.py",
    "news_flow_alert.py",
    "news_flow_db.py",
    "news_flow_engine.py",
    "news_flow_scheduler.py",
    "news_flow_ui.py",
    "news_flow_pdf.py",
]
MACRO_ANALYSIS = [
    "macro_analysis_data.py",
    "macro_analysis_agents.py",
    "macro_analysis_engine.py",
    "macro_analysis_ui.py",
]
MACRO_CYCLE = [
    "macro_cycle_data.py",
    "macro_cycle_agents.py",
    "macro_cycle_engine.py",
    "macro_cycle_ui.py",
    "macro_cycle_pdf.py",
]
PORTFOLIO = [
    "portfolio_db.py",
    "portfolio_manager.py",
    "portfolio_ui.py",
    "portfolio_scheduler.py",
]
SMART_MONITOR = [
    "smart_monitor_deepseek.py",
    "smart_monitor_ui.py",
    "smart_monitor_data.py",
    "smart_monitor_qmt.py",
    "smart_monitor_db.py",
    "smart_monitor_engine.py",
    "smart_monitor_kline.py",
    "smart_monitor_tdx_data.py",
]
STRATEGIES = [
    "low_price_bull_selector.py",
    "low_price_bull_strategy.py",
    "low_price_bull_ui.py",
    "low_price_bull_monitor.py",
    "low_price_bull_monitor_ui.py",
    "low_price_bull_service.py",
    "small_cap_selector.py",
    "small_cap_ui.py",
    "profit_growth_selector.py",
    "profit_growth_ui.py",
    "profit_growth_monitor.py",
    "value_stock_selector.py",
    "value_stock_strategy.py",
    "value_stock_ui.py",
]

for name in COMMON:
    MOVES.append((name, "stockapp/common"))
for name in CORE:
    MOVES.append((name, "stockapp/core"))
for name in MONITORING:
    MOVES.append((name, "stockapp/monitoring"))
for name in MAIN_FORCE:
    MOVES.append((name, "stockapp/main_force"))
for name in SECTOR:
    MOVES.append((name, "stockapp/sector_strategy"))
for name in LONGHUBANG:
    MOVES.append((name, "stockapp/longhubang"))
for name in NEWS_FLOW:
    MOVES.append((name, "stockapp/news_flow"))
for name in MACRO_ANALYSIS:
    MOVES.append((name, "stockapp/macro_analysis"))
for name in MACRO_CYCLE:
    MOVES.append((name, "stockapp/macro_cycle"))
for name in PORTFOLIO:
    MOVES.append((name, "stockapp/portfolio"))
for name in SMART_MONITOR:
    MOVES.append((name, "stockapp/smart_monitor"))
for name in STRATEGIES:
    MOVES.append((name, "stockapp/strategies"))

# module basename -> full dotted path under stockapp
MODULE_TO_PKG: dict[str, str] = {}
for rel, subdir in MOVES:
    mod = Path(rel).stem
    pkg = subdir.replace("/", ".")
    MODULE_TO_PKG[mod] = f"{pkg}.{mod}"


def ensure_dirs():
    (ROOT / "stockapp").mkdir(exist_ok=True)
    for _, subdir in MOVES:
        p = ROOT / subdir
        p.mkdir(parents=True, exist_ok=True)
        init = p / "__init__.py"
        if not init.exists():
            init.write_text('"""Package."""\n', encoding="utf-8")
    root_init = ROOT / "stockapp" / "__init__.py"
    if not root_init.exists():
        root_init.write_text('"""Application code package."""\n', encoding="utf-8")


def move_files():
    for rel, subdir in MOVES:
        src = ROOT / rel
        dst = ROOT / subdir / Path(rel).name
        if not src.exists():
            if dst.exists():
                continue
            raise FileNotFoundError(f"Missing source: {src}")
        if dst.exists():
            continue
        shutil.move(str(src), str(dst))


def pkg_for_path(path: Path) -> str | None:
    """Return 'stockapp.common' etc. if file is inside stockapp subpackage."""
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "stockapp":
        return None
    return f"stockapp.{parts[1]}"


def rewrite_file(path: Path, text: str) -> str:
    current_pkg = pkg_for_path(path)

    def sub_absolute(m: re.Match) -> str:
        stmt = m.group(1)
        mod = m.group(2)
        if mod in ("config", "streamlit", "pandas", "numpy", "plotly", "akshare", "yfinance", "schedule", "requests", "openai", "dotenv", "peewee", "jieba", "bs4", "lxml", "logging", "tempfile", "io", "json", "time", "threading", "base64", "sqlite3", "enum", "collections", "pathlib", "typing", "copy", "functools", "itertools", "hashlib", "re", "math", "random", "subprocess", "sys", "os", "csv", "html"):
            return m.group(0)
        if mod not in MODULE_TO_PKG:
            return m.group(0)
        new_mod = MODULE_TO_PKG[mod]
        return f"{stmt} {new_mod} import"

    # from xxx import yyy
    text = re.sub(
        r"^(\s*from)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+import",
        sub_absolute,
        text,
        flags=re.MULTILINE,
    )

    # import xxx (single module)
    def sub_import(m: re.Match) -> str:
        mod = m.group(1)
        if mod in MODULE_TO_PKG:
            return f"import {MODULE_TO_PKG[mod]}"
        return m.group(0)

    text = re.sub(
        r"^import\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$",
        sub_import,
        text,
        flags=re.MULTILINE,
    )

    # Intra-package: same parent -> relative
    if current_pkg:
        sub = current_pkg.split(".", 1)[1]  # e.g. monitoring
        prefix = f"stockapp.{sub}."
        lines = text.splitlines(keepends=True)
        out = []
        for line in lines:
            m = re.match(r"^(\s*)from\s+(stockapp\." + re.escape(sub) + r"\.([a-zA-Z0-9_]+))\s+import", line)
            if m:
                indent, _, modname = m.groups()
                line = f"{indent}from .{modname} import" + line.split(" import", 1)[1]
            out.append(line)
        text = "".join(out)

    return text


def patch_root_entries():
    """app.py, run.py, test_tdx_api.py — use stockapp imports."""
    for name in ("app.py", "run.py", "test_tdx_api.py"):
        p = ROOT / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        text = rewrite_file(p, text)
        p.write_text(text, encoding="utf-8")


def patch_all_stockapp():
    for dirpath, _, filenames in os.walk(ROOT / "stockapp"):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            text = p.read_text(encoding="utf-8")
            new_text = rewrite_file(p, text)
            if new_text != text:
                p.write_text(new_text, encoding="utf-8")


def main():
    os.chdir(ROOT)
    ensure_dirs()
    move_files()
    patch_all_stockapp()
    patch_root_entries()
    print("Done. Verify with: python -c \"from stockapp.core import stock_data\"")


if __name__ == "__main__":
    main()
