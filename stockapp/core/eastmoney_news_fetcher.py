"""
A 股新闻与公告获取（参考 PokieTicker backend/ashare/client.py）

- 个股新闻：AkShare stock_news_em（东财），带重试与日期过滤
- 公司公告：东财 np-anotice-stock 直连 API（不依赖全市场 spot 表，更稳定）
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

NOTICE_TYPES = (
    "重大事项",
    "财务报告",
    "融资公告",
    "风险提示",
    "资产重组",
    "信息变更",
    "持股变动",
)

NOTICE_API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _without_proxy_env():
    """与 PokieTicker 一致：行情请求前临时去掉进程代理（除非显式允许）。"""
    if _env_truthy("ASHARE_USE_ENV_PROXY"):
        yield
        return
    old = {k: os.environ.get(k) for k in PROXY_ENV_KEYS}
    try:
        for k in PROXY_ENV_KEYS:
            os.environ.pop(k, None)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    if len(value) != 6 or not value.isdigit():
        raise ValueError(f"Invalid A-share symbol: {symbol}")
    return value


def _pick(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _text_or_none(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _datetime_or_none(value: Any) -> Optional[datetime]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _news_id(symbol: str, title: str, published: str, url: Optional[str]) -> str:
    digest = hashlib.sha1(f"{symbol}|{title}|{published}|{url or ''}".encode()).hexdigest()
    return f"ashare:{symbol}:{digest[:16]}"


def _notice_id(symbol: str, title: str, published: str, url: Optional[str]) -> str:
    digest = hashlib.sha1(f"{symbol}|{title}|{published}|{url or ''}".encode()).hexdigest()
    return f"ashare_notice:{symbol}:{digest[:16]}"


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = _env_truthy("ASHARE_USE_ENV_PROXY")
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; aiagents-stock/1.0)",
            "Referer": "https://data.eastmoney.com/",
        }
    )
    return session


def normalize_news_frame(
    frame: pd.DataFrame,
    symbol: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []

    start_date = start.date() if start else None
    end_date = end.date() if end else None
    rows: list[dict[str, Any]] = []

    for _, row in frame.iterrows():
        published = _datetime_or_none(_pick(row, "发布时间", "public_time", "date"))
        if published is None:
            continue
        if start_date and published.date() < start_date:
            continue
        if end_date and published.date() > end_date:
            continue

        title = _text_or_none(_pick(row, "新闻标题", "title"))
        if not title:
            continue

        article_url = _text_or_none(_pick(row, "新闻链接", "url"))
        description = _text_or_none(_pick(row, "新闻内容", "content"))
        publisher = _text_or_none(_pick(row, "文章来源", "publisher")) or "东方财富"
        pub_iso = published.isoformat()

        rows.append(
            {
                "id": _news_id(symbol, title, pub_iso, article_url),
                "kind": "新闻",
                "publisher": publisher,
                "title": title,
                "published_utc": pub_iso,
                "article_url": article_url,
                "description": description,
                "source": "东方财富",
                "新闻标题": title,
                "发布时间": published.strftime("%Y-%m-%d %H:%M:%S"),
                "新闻内容": description or "",
                "新闻链接": article_url or "",
                "文章来源": publisher,
            }
        )
    return rows


def fetch_akshare_news(
    symbol: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    retries: int = 3,
) -> list[dict[str, Any]]:
    import akshare as ak

    code = normalize_symbol(symbol)
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with _without_proxy_env():
                frame = ak.stock_news_em(symbol=code)
            items = normalize_news_frame(frame, code, start=start, end=end)
            if items:
                return items
            last_err = RuntimeError("东方财富新闻为空")
        except Exception as exc:
            last_err = exc
            print(f"   [新闻] AkShare 第 {attempt}/{retries} 次失败: {exc}")
            if attempt < retries:
                time.sleep(1.2 * attempt)
    if last_err:
        print(f"   [新闻] AkShare 最终失败: {last_err}")
    return []


def _fetch_notice_frame(
    code: str,
    notice_type: str,
    start: str,
    end: str,
    timeout: int = 12,
    max_pages: int = 3,
) -> pd.DataFrame:
    report_map = {
        "全部": "0",
        "财务报告": "1",
        "融资公告": "2",
        "风险提示": "3",
        "信息变更": "4",
        "重大事项": "5",
        "资产重组": "6",
        "持股变动": "7",
    }
    params = {
        "sr": "-1",
        "page_size": "100",
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "f_node": report_map.get(notice_type, "5"),
        "s_node": "0",
        "stock_list": code,
        "begin_time": start,
        "end_time": end,
    }
    with _without_proxy_env():
        session = _new_session()
        response = session.get(NOTICE_API_URL, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    total_hits = int(((payload.get("data") or {}).get("total_hits")) or 0)
    if total_hits <= 0:
        return pd.DataFrame()

    total_pages = min(max_pages, math.ceil(total_hits / 100))
    records: list[dict[str, Any]] = []
    with _without_proxy_env():
        session = _new_session()
        for page in range(1, total_pages + 1):
            params["page_index"] = str(page)
            page_response = session.get(NOTICE_API_URL, params=params, timeout=timeout)
            page_response.raise_for_status()
            for item in (page_response.json().get("data") or {}).get("list") or []:
                codes = item.get("codes") or []
                code_info = codes[0] if len(codes) == 1 else next(
                    (c for c in codes if str(c.get("ann_type", "")).startswith("A")),
                    codes[0] if codes else {},
                )
                stock_code = code_info.get("stock_code") or code
                art_code = item.get("art_code")
                records.append(
                    {
                        "代码": stock_code,
                        "公告标题": item.get("title"),
                        "公告类型": notice_type,
                        "公告日期": item.get("notice_date") or item.get("display_time"),
                        "网址": f"https://data.eastmoney.com/notices/detail/{stock_code}/{art_code}.html"
                        if art_code
                        else None,
                    }
                )
    return pd.DataFrame(records)


def normalize_notice_frame(
    frame: pd.DataFrame,
    symbol: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []

    code = normalize_symbol(symbol)
    start_date = start.date() if start else None
    end_date = end.date() if end else None
    rows: list[dict[str, Any]] = []

    for _, row in frame.iterrows():
        row_code = _text_or_none(_pick(row, "代码", "股票代码"))
        if row_code and row_code.split(".")[0].zfill(6) != code:
            continue

        published = _datetime_or_none(_pick(row, "公告日期", "日期"))
        if published is None:
            continue
        if start_date and published.date() < start_date:
            continue
        if end_date and published.date() > end_date:
            continue

        raw_title = _text_or_none(_pick(row, "公告标题", "标题"))
        if not raw_title:
            continue

        notice_type = _text_or_none(_pick(row, "公告类型")) or "公告"
        article_url = _text_or_none(_pick(row, "网址", "链接"))
        title = f"【{notice_type}】{raw_title}"
        pub_iso = published.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        description = f"{notice_type}公告：{raw_title}"

        rows.append(
            {
                "id": _notice_id(code, title, pub_iso, article_url),
                "kind": "公告",
                "publisher": "东方财富公告",
                "title": title,
                "published_utc": pub_iso,
                "article_url": article_url,
                "description": description,
                "source": "东方财富公告",
                "新闻标题": title,
                "发布时间": published.strftime("%Y-%m-%d %H:%M:%S"),
                "新闻内容": description,
                "新闻链接": article_url or "",
                "文章来源": "东方财富公告",
            }
        )
    return rows


def fetch_eastmoney_notices(
    symbol: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    code = normalize_symbol(symbol)
    end_dt = end or datetime.now()
    start_dt = start or (end_dt - timedelta(days=365))
    start_s = start_dt.strftime("%Y-%m-%d")
    end_s = end_dt.strftime("%Y-%m-%d")

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for notice_type in NOTICE_TYPES:
        try:
            frame = _fetch_notice_frame(code, notice_type, start_s, end_s)
            for item in normalize_notice_frame(frame, code, start=start_dt, end=end_dt):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                merged.append(item)
        except Exception as exc:
            print(f"   [公告] {notice_type} 获取失败: {exc}")
    return merged


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item.get("id") or f"{item.get('title')}|{item.get('发布时间')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def fetch_stock_news_and_notices(
    symbol: str,
    max_items: int = 30,
    news_days: int = 180,
    notice_days: int = 365,
    include_notices: bool = True,
) -> list[dict[str, Any]]:
    """
    合并个股新闻（AkShare）与公告（东财 API），按发布时间倒序。
    """
    end = datetime.now()
    news_start = end - timedelta(days=news_days)
    notice_start = end - timedelta(days=notice_days)

    items: list[dict[str, Any]] = []
    items.extend(fetch_akshare_news(symbol, start=news_start, end=end))

    if include_notices:
        print(f"   [公告] 正在从东财公告 API 获取 {symbol} 公告...")
        notices = fetch_eastmoney_notices(symbol, start=notice_start, end=end)
        if notices:
            print(f"   [公告] 获取 {len(notices)} 条")
        items.extend(notices)

    items = _dedupe_items(items)
    items.sort(key=lambda x: x.get("published_utc") or x.get("发布时间") or "", reverse=True)
    return items[:max_items]
