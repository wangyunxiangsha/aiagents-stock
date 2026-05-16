"""
新闻数据获取模块

主路径（参考 PokieTicker）：
- 东财个股新闻：AkShare stock_news_em + 重试 + 日期过滤
- 东财公司公告：np-anotice-stock 直连 API

备用：新浪财经、财联社（仅在主路径无数据时尝试）
"""

import sys
import io
import warnings
from datetime import datetime

import akshare as ak
import pandas as pd

from stockapp.core.eastmoney_news_fetcher import fetch_stock_news_and_notices

warnings.filterwarnings("ignore")


def _setup_stdout_encoding():
    if sys.platform == "win32" and not hasattr(sys.stdout, "_original_stream"):
        try:
            import streamlit  # noqa: F401
            return
        except ImportError:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="ignore")
            except Exception:
                pass


_setup_stdout_encoding()


class QStockNewsDataFetcher:
    """A 股新闻与公告获取（东财 + 可选备用源）"""

    def __init__(self):
        self.max_items = 30
        self.available = True
        print("[OK] 新闻数据获取器初始化（东财新闻 API + AkShare）")

    def get_stock_news(self, symbol):
        data = {
            "symbol": symbol,
            "news_data": None,
            "data_success": False,
            "source": "eastmoney",
        }

        if not self.available:
            data["error"] = "新闻数据获取器不可用"
            return data

        if not self._is_chinese_stock(symbol):
            data["error"] = "新闻数据仅支持中国A股股票"
            return data

        try:
            print(f"[新闻] 正在获取 {symbol} 的新闻与公告（东财）...")
            news_items = fetch_stock_news_and_notices(
                symbol,
                max_items=self.max_items,
                news_days=180,
                notice_days=365,
                include_notices=True,
            )

            if not news_items:
                news_items = self._fallback_news(symbol)

            if news_items:
                data["news_data"] = {
                    "items": news_items,
                    "count": len(news_items),
                    "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "date_range": "近180日新闻 + 近365日公告",
                }
                data["data_success"] = True
                print(f"   [OK] 共 {len(news_items)} 条（含新闻与公告）")
            else:
                print("   [WARN] 未获取到新闻或公告")

        except Exception as e:
            print(f"   [ERR] 获取新闻失败: {e}")
            data["error"] = str(e)

        return data

    def _is_chinese_stock(self, symbol):
        return symbol.isdigit() and len(symbol) == 6

    def _fallback_news(self, symbol):
        """主路径失败时：新浪（按名称）、财联社关键词，避免拉全市场 spot 表。"""
        items = []

        stock_name = None
        try:
            import akshare as ak_local

            info = ak_local.stock_individual_info_em(symbol=symbol)
            if info is not None and not info.empty:
                for _, row in info.iterrows():
                    if str(row.get("item", "")).strip() == "股票简称":
                        stock_name = str(row.get("value", "")).strip()
                        break
        except Exception as e:
            print(f"   [备用] 获取股票名称失败: {e}")

        if stock_name:
            try:
                df = ak.stock_news_sina(symbol=stock_name)
                if df is not None and not df.empty:
                    print(f"   [备用] 新浪财经 {len(df)} 条")
                    for _, row in df.head(self.max_items).iterrows():
                        title = str(row.get("title", row.get("新闻标题", "")))
                        if not title:
                            continue
                        items.append(
                            {
                                "kind": "新闻",
                                "source": "新浪财经",
                                "title": title,
                                "新闻标题": title,
                                "发布时间": str(row.get("date", row.get("发布时间", ""))),
                                "新闻内容": str(row.get("content", "")),
                                "新闻链接": str(row.get("url", "")),
                                "文章来源": "新浪财经",
                            }
                        )
            except Exception as e:
                print(f"   [备用] 新浪失败: {e}")

        if len(items) < 5:
            try:
                df = ak.stock_news_cls()
                if df is not None and not df.empty:
                    mask = df["内容"].astype(str).str.contains(symbol, na=False) | df[
                        "标题"
                    ].astype(str).str.contains(symbol, na=False)
                    if stock_name:
                        mask = mask | df["内容"].astype(str).str.contains(stock_name, na=False)
                    df_filtered = df[mask]
                    if not df_filtered.empty:
                        print(f"   [备用] 财联社 {len(df_filtered)} 条")
                        for _, row in df_filtered.head(self.max_items - len(items)).iterrows():
                            title = str(row.get("标题", ""))
                            items.append(
                                {
                                    "kind": "新闻",
                                    "source": "财联社",
                                    "title": title,
                                    "新闻标题": title,
                                    "发布时间": str(row.get("发布时间", "")),
                                    "新闻内容": str(row.get("内容", "")),
                                    "新闻链接": "",
                                    "文章来源": "财联社",
                                }
                            )
            except Exception as e:
                print(f"   [备用] 财联社失败: {e}")

        return items[: self.max_items]

    def format_news_for_ai(self, data):
        if not data or not data.get("data_success"):
            return "未能获取新闻数据"

        news_data = data.get("news_data") or {}
        lines = [
            "【最新新闻与公告 - 东方财富 / AkShare】",
            f"查询时间：{news_data.get('query_time', 'N/A')}",
            f"时间范围：{news_data.get('date_range', 'N/A')}",
            f"条数：{news_data.get('count', 0)}",
            "",
        ]

        for idx, item in enumerate(news_data.get("items", []), 1):
            kind = item.get("kind", "新闻")
            lines.append(f"--- {kind} {idx} ---")
            lines.append(f"标题：{item.get('新闻标题') or item.get('title', '')}")
            lines.append(f"时间：{item.get('发布时间') or item.get('published_utc', '')}")
            lines.append(f"来源：{item.get('文章来源') or item.get('source', '')}")
            content = item.get("新闻内容") or item.get("description") or item.get("content", "")
            if content:
                text = str(content)
                if len(text) > 600:
                    text = text[:600] + "..."
                lines.append(f"摘要：{text}")
            url = item.get("新闻链接") or item.get("article_url") or item.get("url", "")
            if url:
                lines.append(f"链接：{url}")
            lines.append("")

        return "\n".join(lines)


if __name__ == "__main__":
    fetcher = QStockNewsDataFetcher()
    for sym in ("600050", "000001"):
        result = fetcher.get_stock_news(sym)
        if result.get("data_success"):
            print(fetcher.format_news_for_ai(result))
        else:
            print(result.get("error"))
