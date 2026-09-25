# -*- coding: utf-8 -*-
r"""下载 7 只 ETF 的日行情（后复权）到本地缓存。

优先 AkShare（免积分）：
    fund_etf_hist_em(adjust="hfq")，失败则 fund_lof_hist_em（162411 是 LOF），
    仍失败则回退 Tushare fund_daily + fund_adj（手工算后复权）。

输出：factor_lab/data/cache/etf_daily.parquet
列：code（聚宽格式，如 510500.XSHG）, date, open, close, high, low, volume, amount, source
"""
from __future__ import annotations
import os
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"
START, END = "20180101", "20260901"

POOL = {
    "510500": "510500.XSHG", "512800": "512800.XSHG", "513100": "513100.XSHG",
    "513050": "513050.XSHG", "518880": "518880.XSHG", "162411": "162411.XSHE",
    "511260": "511260.XSHG",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def _norm(df: pd.DataFrame, code6: str, jq: str, source: str) -> pd.DataFrame:
    rename = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
              "最低": "low", "成交量": "volume", "成交额": "amount"}
    out = df.rename(columns=rename)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["code"] = jq
    out["source"] = source
    keep = ["code", "date", "open", "close", "high", "low", "volume", "amount", "source"]
    return out[[c for c in keep if c in out.columns]]


def fetch_akshare(code6: str) -> tuple[pd.DataFrame | None, str]:
    import akshare as ak
    for attempt in range(3):
        for fn, tag in ((ak.fund_etf_hist_em, "akshare-etf-hfq"), (ak.fund_lof_hist_em, "akshare-lof-hfq")):
            try:
                df = fn(symbol=code6, period="daily", start_date=START, end_date=END, adjust="hfq")
                if df is not None and not df.empty:
                    return df, tag
            except Exception as exc:
                last = "{}: {}".format(tag, str(exc)[:90])
        time.sleep(2.0 * (attempt + 1))
    return None, last if 'last' in dir() else "akshare 失败"


def fetch_tushare(code6: str) -> tuple[pd.DataFrame | None, str]:
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return None, "无 TUSHARE_TOKEN"
    pro = ts.pro_api(token)
    suffix = ".SH" if code6.startswith(("5", "6")) else ".SZ"
    ts_code = code6 + suffix
    try:
        px = pro.fund_daily(ts_code=ts_code, start_date=START, end_date=END)
        adj = pro.fund_adj(ts_code=ts_code, start_date=START, end_date=END)
        if px is None or px.empty:
            return None, "tushare fund_daily 空"
        px = px[["trade_date", "open", "close", "high", "low", "vol", "amount"]].copy()
        px["trade_date"] = px["trade_date"].astype(str)
        px["date"] = px["trade_date"].str[:4] + "-" + px["trade_date"].str[4:6] + "-" + px["trade_date"].str[6:8]
        if adj is not None and not adj.empty:
            a = adj.copy()
            a["trade_date"] = a["trade_date"].astype(str)
            px = px.merge(a[["trade_date", "adj_factor"]], on="trade_date", how="left")
            px["adj_factor"] = px["adj_factor"].ffill().bfill().fillna(1.0)
            for col in ("open", "close", "high", "low"):
                px[col] = px[col] * px["adj_factor"]      # 后复权
        else:
            log("  警告：{} 无 fund_adj，使用不复权价".format(ts_code))
        return px.rename(columns={"vol": "volume"}), "tushare-fund_daily-hfq"
    except Exception as exc:
        return None, "tushare: {}".format(str(exc)[:90])


def main() -> int:
    frames, failed = [], []
    for code6, jq in POOL.items():
        df, tag = fetch_akshare(code6)
        if df is None:
            log("{} AkShare 失败（{}），改试 Tushare".format(code6, tag))
            df, tag = fetch_tushare(code6)
        if df is None:
            failed.append((code6, tag))
            log("{} 全部失败: {}".format(code6, tag))
            continue
        frames.append(_norm(df, code6, jq, tag))
        log("{} -> {} 行 / {} ~ {} / {}".format(code6, len(df), df.iloc[0, 0], df.iloc[-1, 0], tag))
        time.sleep(1.0)
    if not frames:
        log("没有任何数据")
        return 1
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code", "date"], keep="last")
    dst = CACHE / "etf_daily.parquet"
    out.to_parquet(dst, index=False)
    log("\n写出 {} 行={:,} 只={} 大小={:.2f} MB".format(
        dst.name, len(out), out["code"].nunique(), dst.stat().st_size / 1e6))
    log("来源分布: {}".format(out.groupby("source").size().to_dict()))
    if failed:
        log("失败: {}".format(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
