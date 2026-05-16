"""
数据源管理器
实现 AkShare、Tushare、BaoStock 的自动切换机制：
- 日线行情：AkShare → Tushare → BaoStock
- 实时行情：AkShare → Tushare → BaoStock（5 分钟线聚合 + 日线昨收）
- 财务数据：AkShare → Tushare → BaoStock（季频指标表，非新浪全科目明细）
- 市场情绪（换手率 / 上证涨跌幅）：AkShare 全表类接口易断连时可用 BaoStock 日线字段（免 Token）
Tushare 的 daily 等接口需足够积分；BaoStock 免费无需 Token。
"""

import os
import time
from typing import Optional

import pandas as pd
from datetime import datetime, timedelta

import env_bootstrap  # noqa: F401 — 确保 .env 与 DISABLE_ENV_HTTP_PROXY 已生效


class DataSourceManager:
    """数据源管理器 - 实现akshare与tushare自动切换"""
    
    def __init__(self):
        self.tushare_token = os.getenv('TUSHARE_TOKEN', '')
        self.tushare_available = False
        self.tushare_api = None
        
        # 初始化tushare
        if self.tushare_token:
            try:
                import tushare as ts
                ts.set_token(self.tushare_token)
                self.tushare_api = ts.pro_api()
                self.tushare_available = True
                print("✅ Tushare数据源初始化成功")
            except Exception as e:
                print(f"⚠️ Tushare数据源初始化失败: {e}")
                self.tushare_available = False
        else:
            print("ℹ️ 未配置Tushare Token，将仅使用Akshare数据源")
    
    def get_stock_hist_data(self, symbol, start_date=None, end_date=None, adjust='qfq'):
        """
        获取股票历史数据（优先akshare，失败时使用tushare）
        
        Args:
            symbol: 股票代码（6位数字）
            start_date: 开始日期（格式：'20240101'或'2024-01-01'）
            end_date: 结束日期
            adjust: 复权类型（'qfq'前复权, 'hfq'后复权, ''不复权）
            
        Returns:
            DataFrame: 包含日期、开盘、收盘、最高、最低、成交量等列
        """
        # 标准化日期格式
        if start_date:
            start_date = start_date.replace('-', '')
        if end_date:
            end_date = end_date.replace('-', '')
        else:
            end_date = datetime.now().strftime('%Y%m%d')
        
        # 优先使用akshare（东财偶发 RemoteDisconnected，短暂退避重试）
        try:
            import akshare as ak
            print(f"[Akshare] 正在获取 {symbol} 的历史数据...")
            df = None
            last_err: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                    )
                    if df is not None and not df.empty:
                        break
                except Exception as e:
                    last_err = e
                    print(f"[Akshare] 第 {attempt}/3 次失败: {e}")
                    if attempt < 3:
                        time.sleep(1.2 * attempt)

            if df is not None and not df.empty:
                df = df.rename(
                    columns={
                        "日期": "date",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                        "振幅": "amplitude",
                        "涨跌幅": "pct_change",
                        "涨跌额": "change",
                        "换手率": "turnover",
                    }
                )
                df["date"] = pd.to_datetime(df["date"])
                print(f"[Akshare] ✅ 成功获取 {len(df)} 条数据")
                return df
            if last_err is not None:
                print(f"[Akshare] ❌ 获取失败: {last_err}")
        except Exception as e:
            print(f"[Akshare] ❌ 获取失败: {e}")
        
        # akshare失败，尝试tushare
        if self.tushare_available:
            try:
                print(f"[Tushare] 正在获取 {symbol} 的历史数据（备用数据源）...")
                
                # 转换股票代码格式（添加市场后缀）
                ts_code = self._convert_to_ts_code(symbol)
                
                # 转换复权类型
                adj_dict = {'qfq': 'qfq', 'hfq': 'hfq', '': None}
                adj = adj_dict.get(adjust, 'qfq')
                
                # 格式化日期
                start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}" if start_date else None
                end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}" if end_date else None
                
                # 获取数据
                df = self.tushare_api.daily(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                    adj=adj
                )
                
                if df is not None and not df.empty:
                    # 标准化列名和数据格式
                    df = df.rename(columns={
                        'trade_date': 'date',
                        'vol': 'volume',
                        'amount': 'amount'
                    })
                    df['date'] = pd.to_datetime(df['date'])
                    df = df.sort_values('date')
                    
                    # 转换成交量单位（tushare单位是手，转换为股）
                    df['volume'] = df['volume'] * 100
                    # 转换成交额单位（tushare单位是千元，转换为元）
                    df['amount'] = df['amount'] * 1000
                    
                    print(f"[Tushare] ✅ 成功获取 {len(df)} 条数据")
                    return df
            except Exception as e:
                print(f"[Tushare] ❌ 获取失败: {e}")
        
        # AkShare / Tushare 均不可用（含 Tushare 无 daily 权限）时尝试 BaoStock
        df_bs = self._get_hist_from_baostock(symbol, start_date, end_date, adjust)
        if df_bs is not None and not df_bs.empty:
            return df_bs

        print("❌ 所有数据源均获取失败")
        return None
    
    def get_stock_basic_info(self, symbol):
        """
        获取股票基本信息（优先akshare，失败时使用tushare）
        
        Args:
            symbol: 股票代码
            
        Returns:
            dict: 股票基本信息
        """
        info = {
            "symbol": symbol,
            "name": "未知",
            "industry": "未知",
            "market": "未知"
        }
        
        # 优先使用akshare（同上，重试减轻瞬时断连）
        try:
            import akshare as ak
            print(f"[Akshare] 正在获取 {symbol} 的基本信息...")
            stock_info = None
            last_err: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    stock_info = ak.stock_individual_info_em(symbol=symbol)
                    if stock_info is not None and not stock_info.empty:
                        break
                except Exception as e:
                    last_err = e
                    print(f"[Akshare] 第 {attempt}/3 次失败: {e}")
                    if attempt < 3:
                        time.sleep(1.2 * attempt)

            if stock_info is not None and not stock_info.empty:
                for _, row in stock_info.iterrows():
                    key = row["item"]
                    value = row["value"]

                    if key == "股票简称":
                        info["name"] = value
                    elif key == "所处行业":
                        info["industry"] = value
                    elif key == "上市时间":
                        info["list_date"] = value
                    elif key == "总市值":
                        info["market_cap"] = value
                    elif key == "流通市值":
                        info["circulating_market_cap"] = value

                print("[Akshare] ✅ 成功获取基本信息")
                return info
            if last_err is not None:
                print(f"[Akshare] ❌ 获取失败: {last_err}")
        except Exception as e:
            print(f"[Akshare] ❌ 获取失败: {e}")
        
        # akshare失败，尝试tushare
        if self.tushare_available:
            try:
                print(f"[Tushare] 正在获取 {symbol} 的基本信息（备用数据源）...")
                
                ts_code = self._convert_to_ts_code(symbol)
                df = self.tushare_api.stock_basic(
                    ts_code=ts_code,
                    fields='ts_code,name,area,industry,market,list_date'
                )
                
                if df is not None and not df.empty:
                    info['name'] = df.iloc[0]['name']
                    info['industry'] = df.iloc[0]['industry']
                    info['market'] = df.iloc[0]['market']
                    info['list_date'] = df.iloc[0]['list_date']
                    
                    print(f"[Tushare] ✅ 成功获取基本信息")
                    return info
            except Exception as e:
                print(f"[Tushare] ❌ 获取失败: {e}")
        
        return info
    
    def get_realtime_quotes(self, symbol):
        """
        获取实时行情数据（优先 AkShare，其次 Tushare，最后 BaoStock 5 分钟线聚合）
        
        Args:
            symbol: 股票代码
            
        Returns:
            dict: 实时行情数据
        """
        quotes = {}
        
        # 优先使用akshare
        try:
            import akshare as ak
            print(f"[Akshare] 正在获取 {symbol} 的实时行情...")
            
            df = ak.stock_zh_a_spot_em()
            stock_df = df[df['代码'] == symbol]
            
            if not stock_df.empty:
                row = stock_df.iloc[0]
                quotes = {
                    'symbol': symbol,
                    'name': row['名称'],
                    'price': row['最新价'],
                    'change_percent': row['涨跌幅'],
                    'change': row['涨跌额'],
                    'volume': row['成交量'],
                    'amount': row['成交额'],
                    'high': row['最高'],
                    'low': row['最低'],
                    'open': row['今开'],
                    'pre_close': row['昨收']
                }
                print(f"[Akshare] ✅ 成功获取实时行情")
                return quotes
        except Exception as e:
            print(f"[Akshare] ❌ 获取失败: {e}")
        
        # akshare失败，尝试tushare
        if self.tushare_available:
            try:
                print(f"[Tushare] 正在获取 {symbol} 的实时行情（备用数据源）...")
                
                ts_code = self._convert_to_ts_code(symbol)
                df = self.tushare_api.daily(
                    ts_code=ts_code,
                    start_date=datetime.now().strftime('%Y%m%d'),
                    end_date=datetime.now().strftime('%Y%m%d')
                )
                
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    quotes = {
                        'symbol': symbol,
                        'price': row['close'],
                        'change_percent': row['pct_chg'],
                        'volume': row['vol'] * 100,
                        'amount': row['amount'] * 1000,
                        'high': row['high'],
                        'low': row['low'],
                        'open': row['open'],
                        'pre_close': row['pre_close']
                    }
                    print(f"[Tushare] ✅ 成功获取实时行情")
                    return quotes
            except Exception as e:
                print(f"[Tushare] ❌ 获取失败: {e}")
        
        bs_quotes = self._get_realtime_from_baostock(symbol)
        if bs_quotes:
            return bs_quotes

        return quotes
    
    def get_financial_data(self, symbol, report_type='income'):
        """
        获取财务数据（优先 AkShare，其次 Tushare，最后 BaoStock 季频指标）
        
        Args:
            symbol: 股票代码
            report_type: 报表类型（'income'利润表, 'balance'资产负债表, 'cashflow'现金流量表）
            
        Returns:
            DataFrame: 财务数据。AkShare/Tushare 为报表科目表；BaoStock 为季频指标宽表，
            可通过 ``df.attrs['source'] == 'baostock'`` 区分。
        """
        # 优先使用akshare
        try:
            import akshare as ak
            print(f"[Akshare] 正在获取 {symbol} 的财务数据...")
            
            if report_type == 'income':
                df = ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
            elif report_type == 'balance':
                df = ak.stock_financial_report_sina(stock=symbol, symbol="资产负债表")
            elif report_type == 'cashflow':
                df = ak.stock_financial_report_sina(stock=symbol, symbol="现金流量表")
            else:
                df = None
            
            if df is not None and not df.empty:
                print(f"[Akshare] ✅ 成功获取财务数据")
                return df
        except Exception as e:
            print(f"[Akshare] ❌ 获取失败: {e}")
        
        # akshare失败，尝试tushare
        if self.tushare_available:
            try:
                print(f"[Tushare] 正在获取 {symbol} 的财务数据（备用数据源）...")
                
                ts_code = self._convert_to_ts_code(symbol)
                
                if report_type == 'income':
                    df = self.tushare_api.income(ts_code=ts_code)
                elif report_type == 'balance':
                    df = self.tushare_api.balancesheet(ts_code=ts_code)
                elif report_type == 'cashflow':
                    df = self.tushare_api.cashflow(ts_code=ts_code)
                else:
                    df = None
                
                if df is not None and not df.empty:
                    print(f"[Tushare] ✅ 成功获取财务数据")
                    return df
            except Exception as e:
                print(f"[Tushare] ❌ 获取失败: {e}")
        
        df_bs = self._get_financial_from_baostock(symbol, report_type)
        if df_bs is not None and not df_bs.empty:
            return df_bs

        return None
    
    def _convert_to_bs_code(self, symbol: str) -> Optional[str]:
        """6 位 A 股代码 -> BaoStock 代码（如 sh.600050）。"""
        if not symbol or len(symbol) != 6 or not symbol.isdigit():
            return None
        if symbol.startswith("6"):
            return f"sh.{symbol}"
        if symbol.startswith(("0", "3")):
            return f"sz.{symbol}"
        if symbol.startswith(("8", "4")):
            return f"bj.{symbol}"
        return f"sz.{symbol}"

    def _get_hist_from_baostock(
        self,
        symbol: str,
        start_date: Optional[str],
        end_date: Optional[str],
        adjust: str = "qfq",
    ) -> Optional[pd.DataFrame]:
        """
        使用 BaoStock 获取 A 股日线（免费、无需 Token）。
        与 AkShare 输出列对齐：date, open, high, low, close, volume, amount, pct_change, turnover。
        """
        try:
            import baostock as bs
        except ImportError:
            print("[BaoStock] 未安装 baostock，请执行: pip install baostock")
            return None

        bs_code = self._convert_to_bs_code(symbol)
        if not bs_code:
            return None

        adj_map = {"qfq": "2", "hfq": "1", "": "3"}
        adjustflag = adj_map.get(adjust, "2")

        if end_date and len(end_date) >= 8:
            end_s = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        else:
            end_s = datetime.now().strftime("%Y-%m-%d")
        if start_date and len(start_date) >= 8:
            start_s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        else:
            start_s = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")

        print(f"[BaoStock] 正在获取 {symbol} 的历史数据（第三数据源）...")
        lg = bs.login()
        if lg.error_code != "0":
            print(f"[BaoStock] ❌ 登录失败: {lg.error_msg}")
            return None

        fields = "date,open,high,low,close,volume,amount,turn,pctChg"
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields,
                start_date=start_s,
                end_date=end_s,
                frequency="d",
                adjustflag=adjustflag,
            )
            if rs.error_code != "0":
                print(f"[BaoStock] ❌ 查询失败: {rs.error_msg}")
                return None
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                print("[BaoStock] ❌ 返回数据为空")
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
        except Exception as e:
            print(f"[BaoStock] ❌ 获取失败: {e}")
            return None
        finally:
            bs.logout()

        for col in ("open", "high", "low", "close", "volume", "amount", "turn", "pctChg"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        rename = {"turn": "turnover", "pctChg": "pct_change"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        print(f"[BaoStock] ✅ 成功获取 {len(df)} 条数据")
        return df

    @staticmethod
    def _interpret_turnover(turnover: float) -> str:
        if turnover > 20:
            return "换手率极高（>20%），资金活跃度极高，可能存在炒作"
        if turnover > 10:
            return "换手率较高（>10%），交易活跃"
        if turnover > 5:
            return "换手率正常（5%-10%），交易适中"
        if turnover > 2:
            return "换手率偏低（2%-5%），交易相对清淡"
        return "换手率很低（<2%），交易清淡"

    def get_latest_turnover_baostock(self, symbol: str) -> Optional[dict]:
        """
        从 BaoStock 最近日线取换手率（不依赖东财 spot 全表；免 Token）。
        返回结构与 market_sentiment_data._get_turnover_rate 一致。
        """
        try:
            import baostock as bs
        except ImportError:
            print("[BaoStock] 未安装 baostock，请执行: pip install baostock")
            return None

        bs_code = self._convert_to_bs_code(symbol)
        if not bs_code:
            return None

        end_s = datetime.now().strftime("%Y-%m-%d")
        start_s = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        print(f"[BaoStock] 正在获取 {symbol} 的换手率（第三数据源）...")
        lg = bs.login()
        if lg.error_code != "0":
            print(f"[BaoStock] ❌ 登录失败: {lg.error_msg}")
            return None
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,turn",
                start_date=start_s,
                end_date=end_s,
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != "0":
                print(f"[BaoStock] ❌ 查询失败: {rs.error_msg}")
                return None
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                print("[BaoStock] ❌ 换手率数据为空")
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
            df["turn"] = pd.to_numeric(df["turn"], errors="coerce")
            last = df.iloc[-1]
            turn = float(last["turn"]) if pd.notna(last.get("turn")) else None
            if turn is None:
                return None
            print(f"[BaoStock] ✅ 成功获取换手率: {turn}%")
            return {
                "current_turnover_rate": turn,
                "interpretation": self._interpret_turnover(turn),
            }
        except Exception as e:
            print(f"[BaoStock] ❌ 获取换手率失败: {e}")
            return None
        finally:
            bs.logout()

    def get_sse_index_sentiment_baostock(self) -> Optional[dict]:
        """
        上证指数最新涨跌幅（BaoStock sh.000001 日线 pctChg）。
        不含全市场涨跌家数（需 AkShare spot 或额外数据源）。
        """
        try:
            import baostock as bs
        except ImportError:
            print("[BaoStock] 未安装 baostock，请执行: pip install baostock")
            return None

        end_s = datetime.now().strftime("%Y-%m-%d")
        start_s = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        print("[BaoStock] 正在获取上证指数涨跌幅（第三数据源）...")
        lg = bs.login()
        if lg.error_code != "0":
            print(f"[BaoStock] ❌ 登录失败: {lg.error_msg}")
            return None
        try:
            rs = bs.query_history_k_data_plus(
                "sh.000001",
                "date,close,pctChg",
                start_date=start_s,
                end_date=end_s,
                frequency="d",
                adjustflag="3",
            )
            if rs.error_code != "0":
                print(f"[BaoStock] ❌ 查询失败: {rs.error_msg}")
                return None
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                print("[BaoStock] ❌ 指数数据为空")
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
            df["pctChg"] = pd.to_numeric(df["pctChg"], errors="coerce")
            change_pct = float(df.iloc[-1]["pctChg"])
            print("[BaoStock] ✅ 成功获取上证指数涨跌幅")
            return {
                "index_name": "上证指数",
                "change_percent": change_pct,
            }
        except Exception as e:
            print(f"[BaoStock] ❌ 获取大盘指数失败: {e}")
            return None
        finally:
            bs.logout()

    @staticmethod
    def _bs_recent_quarters(count: int):
        """从当前季起向前生成 (year, quarter)。"""
        d = datetime.now()
        y, m = d.year, d.month
        q = (m - 1) // 3 + 1
        for _ in range(count):
            yield y, q
            q -= 1
            if q <= 0:
                q = 4
                y -= 1

    def _get_realtime_from_baostock(self, symbol: str) -> dict:
        """
        BaoStock 无 query_rt_data：用近端 5 分钟 K 聚合当日 OHLCV，
        昨收/涨跌幅对齐当日日线 preclose、pctChg；无分钟数据时退化为最近一根日线。
        """
        try:
            import baostock as bs
        except ImportError:
            print("[BaoStock] 未安装 baostock，请执行: pip install baostock")
            return {}

        bs_code = self._convert_to_bs_code(symbol)
        if not bs_code:
            return {}

        print(f"[BaoStock] 正在获取 {symbol} 的实时行情（第三数据源，5m+日线）...")
        lg = bs.login()
        if lg.error_code != "0":
            print(f"[BaoStock] ❌ 登录失败: {lg.error_msg}")
            return {}

        name = symbol
        try:
            rsb = bs.query_stock_basic(code=bs_code)
            if rsb.error_code == "0" and rsb.next():
                name = rsb.get_row_data()[1] or symbol
        except Exception:
            pass

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        quotes: dict = {}

        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,time,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="5",
                adjustflag="3",
            )
            mrows = []
            if rs.error_code == "0":
                while rs.error_code == "0" and rs.next():
                    mrows.append(rs.get_row_data())

            rsd = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,preclose,volume,amount,pctChg",
                start_date=(datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d"),
                end_date=end,
                frequency="d",
                adjustflag="2",
            )
            drows = []
            if rsd.error_code == "0":
                while rsd.error_code == "0" and rsd.next():
                    drows.append(rsd.get_row_data())

            if mrows:
                mdf = pd.DataFrame(mrows, columns=rs.fields)
                for col in ("open", "high", "low", "close", "volume", "amount"):
                    mdf[col] = pd.to_numeric(mdf[col], errors="coerce")
                last_day = str(mdf["date"].iloc[-1])
                day = mdf[mdf["date"] == last_day]
                o = float(day["open"].iloc[0])
                h = float(day["high"].max())
                l = float(day["low"].min())
                close_px = float(day["close"].iloc[-1])
                v = float(day["volume"].sum())
                a = float(day["amount"].sum())

                pre_close = None
                pct_day = None
                if drows:
                    ddf = pd.DataFrame(drows, columns=rsd.fields)
                    hit = ddf[ddf["date"] == last_day]
                    if not hit.empty:
                        pre_close = float(pd.to_numeric(hit["preclose"].iloc[0], errors="coerce"))
                        pct_day = float(pd.to_numeric(hit["pctChg"].iloc[0], errors="coerce"))
                if pre_close is None and drows:
                    ddf = pd.DataFrame(drows, columns=rsd.fields)
                    pre_close = float(pd.to_numeric(ddf["preclose"].iloc[-1], errors="coerce"))

                chg = close_px - pre_close if pre_close is not None else 0.0
                pct = pct_day
                if pct is None and pre_close:
                    pct = (chg / pre_close) * 100.0 if pre_close else 0.0

                quotes = {
                    "symbol": symbol,
                    "name": name,
                    "price": close_px,
                    "change_percent": pct if pct is not None else 0.0,
                    "change": chg,
                    "volume": v,
                    "amount": a,
                    "high": h,
                    "low": l,
                    "open": o,
                    "pre_close": pre_close if pre_close is not None else close_px,
                }
            elif drows:
                ddf = pd.DataFrame(drows, columns=rsd.fields)
                for col in ("open", "high", "low", "close", "preclose", "volume", "amount", "pctChg"):
                    if col in ddf.columns:
                        ddf[col] = pd.to_numeric(ddf[col], errors="coerce")
                last = ddf.iloc[-1]
                pre_close = float(last["preclose"]) if pd.notna(last.get("preclose")) else float(last["close"])
                close_px = float(last["close"])
                chg = close_px - pre_close if pre_close else 0.0
                pct = float(last["pctChg"]) if pd.notna(last.get("pctChg")) else (
                    (chg / pre_close * 100.0) if pre_close else 0.0
                )
                quotes = {
                    "symbol": symbol,
                    "name": name,
                    "price": close_px,
                    "change_percent": pct,
                    "change": chg,
                    "volume": float(last["volume"]),
                    "amount": float(last["amount"]),
                    "high": float(last["high"]),
                    "low": float(last["low"]),
                    "open": float(last["open"]),
                    "pre_close": pre_close,
                }
        except Exception as e:
            print(f"[BaoStock] ❌ 实时行情失败: {e}")
            quotes = {}
        finally:
            bs.logout()

        if quotes:
            print("[BaoStock] ✅ 成功获取实时行情（第三数据源）")
        return quotes

    def _get_financial_from_baostock(self, symbol: str, report_type: str = "income") -> Optional[pd.DataFrame]:
        """
        BaoStock 季频财务指标（非新浪/财报全科目明细）。
        income -> query_profit_data；balance -> query_balance_data；cashflow -> query_cash_flow_data。
        """
        try:
            import baostock as bs
        except ImportError:
            print("[BaoStock] 未安装 baostock，请执行: pip install baostock")
            return None

        bs_code = self._convert_to_bs_code(symbol)
        if not bs_code:
            return None

        api_map = {
            "income": "query_profit_data",
            "balance": "query_balance_data",
            "cashflow": "query_cash_flow_data",
        }
        fn = api_map.get(report_type)
        if not fn:
            return None

        print(f"[BaoStock] 正在获取 {symbol} 财务数据（第三数据源，{report_type} 季频指标）...")
        lg = bs.login()
        if lg.error_code != "0":
            print(f"[BaoStock] ❌ 登录失败: {lg.error_msg}")
            return None

        rows: list = []
        field_names: Optional[list] = None
        try:
            api = getattr(bs, fn)
            for year, quarter in self._bs_recent_quarters(12):
                rs = api(bs_code, year, quarter)
                if rs.error_code != "0":
                    continue
                if rs.next():
                    if field_names is None:
                        field_names = list(rs.fields)
                    rows.append(rs.get_row_data())
        except Exception as e:
            print(f"[BaoStock] ❌ 财务数据失败: {e}")
            rows = []
            field_names = None
        finally:
            bs.logout()

        if not rows or not field_names:
            print("[BaoStock] ❌ 财务数据为空（可能该股当季无披露）")
            return None

        df = pd.DataFrame(rows, columns=field_names)
        df.attrs["source"] = "baostock"
        df.attrs["granularity"] = "quarterly_indicators"
        df.attrs["report_type"] = report_type
        print(f"[BaoStock] ✅ 成功获取财务季频指标 {len(df)} 条")
        return df

    def _convert_to_ts_code(self, symbol):
        """
        将6位股票代码转换为tushare格式（带市场后缀）
        
        Args:
            symbol: 6位股票代码
            
        Returns:
            str: tushare格式代码（如：000001.SZ）
        """
        if not symbol or len(symbol) != 6:
            return symbol
        
        # 根据代码判断市场
        if symbol.startswith('6'):
            # 上海主板
            return f"{symbol}.SH"
        elif symbol.startswith('0') or symbol.startswith('3'):
            # 深圳主板和创业板
            return f"{symbol}.SZ"
        elif symbol.startswith('8') or symbol.startswith('4'):
            # 北交所
            return f"{symbol}.BJ"
        else:
            # 默认深圳
            return f"{symbol}.SZ"
    
    def _convert_from_ts_code(self, ts_code):
        """
        将tushare格式代码转换为6位代码
        
        Args:
            ts_code: tushare格式代码（如：000001.SZ）
            
        Returns:
            str: 6位股票代码
        """
        if '.' in ts_code:
            return ts_code.split('.')[0]
        return ts_code


# 全局数据源管理器实例
data_source_manager = DataSourceManager()

