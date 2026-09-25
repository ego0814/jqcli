# -*- coding: utf-8 -*-
r"""用聚宽 PIT 转股溢价率重算可转债双低（PIT 安全）。

输入：
    cache/cb_daily.parquet            转债日行情（tushare，close 为不复权价）
    cache/cb_convert_<year>.parquet   聚宽 bond.CONBOND_DAILY_CONVERT 年度分片
                                      （code, date, convert_price, convert_premium_rate）

输出：
    output/factor_values/cb_double_low_pit.parquet
        双低值 = 转债收盘价 + 转股溢价率(%)   ← 溢价率取聚宽当日值（PIT 安全）
    output/factor_values/cb_liq_avg_amount_20d.parquet
        可投池流动性（过去 20 交易日日均成交额，元），供 quantile_alpha_scan --liq-file 直接使用

与旧口径的差异：旧口径用 cb_basic 的**当前**转股价自己算溢价率（非 PIT）；
本脚本直接用聚宽的**当日** convert_premium_rate。
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(DATA_DIR))
from compute_ic import norm_date, trading_calendar  # noqa: E402


def log(message: str) -> None:
    print(message, flush=True)


def monthly_rebalance_dates(calendar: list[str]) -> list[str]:
    out, seen = [], set()
    for day in calendar:
        key = day[:7]
        if key not in seen:
            seen.add(key)
            out.append(day)
    return out


def main() -> int:
    daily = pd.read_parquet(CACHE / "cb_daily.parquet",
                            columns=["ts_code", "trade_date", "close", "amount", "vol"])
    daily["trade_date"] = daily["trade_date"].map(norm_date)
    daily["code"] = daily["ts_code"].str.split(".").str[0]
    log("cb_daily: {:,} 行 / {} 只".format(len(daily), daily["code"].nunique()))

    parts = []
    for path in sorted(CACHE.glob("cb_convert_*.parquet")):
        frame = pd.read_parquet(path)
        frame["date"] = frame["date"].map(norm_date)
        parts.append(frame)
    conv = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["code", "date"], keep="last")
    log("聚宽转股价/溢价率: {:,} 行 / {} 只 / {} ~ {}".format(
        len(conv), conv["code"].nunique(), conv["date"].min(), conv["date"].max()))

    merged = daily.merge(conv.rename(columns={"date": "trade_date"}),
                         on=["code", "trade_date"], how="inner")
    merged["cb_close"] = pd.to_numeric(merged["close"], errors="coerce")
    merged["premium_rate"] = pd.to_numeric(merged["convert_premium_rate"], errors="coerce")
    merged["conv_price"] = pd.to_numeric(merged["convert_price"], errors="coerce")
    ok = (merged["cb_close"] > 0) & merged["premium_rate"].notna() & (merged["vol"] > 0)
    merged = merged[ok].copy()
    merged["cb_double_low_value"] = merged["cb_close"] + merged["premium_rate"]
    log("合并后有效样本: {:,} 行 / {} 只（{} ~ {}）".format(
        len(merged), merged["code"].nunique(), merged["trade_date"].min(), merged["trade_date"].max()))

    calendar = trading_calendar()
    reb = monthly_rebalance_dates(calendar)
    factor = merged[merged["trade_date"].isin(set(reb))][
        ["ts_code", "trade_date", "cb_double_low_value", "cb_close", "premium_rate", "conv_price", "amount"]
    ].rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"}).copy()
    factor["complete"] = True
    factor["pit_status"] = "CB_JQ_CONVERT_PIT"
    factor = factor.sort_values(["rebalance_date", "symbol"]).reset_index(drop=True)
    dst = OUT_DIR / "cb_double_low_pit.parquet"
    factor.to_parquet(dst, index=False)
    log("PIT 双低因子写出: {} 行={:,} 期={:,} 转债={:,}（{} ~ {}）".format(
        dst.name, len(factor), factor["rebalance_date"].nunique(), factor["symbol"].nunique(),
        factor["rebalance_date"].min(), factor["rebalance_date"].max()))
    log("双低值分布: min={:.2f} p25={:.2f} 中位={:.2f} p75={:.2f} max={:.2f}".format(
        factor["cb_double_low_value"].min(), factor["cb_double_low_value"].quantile(0.25),
        factor["cb_double_low_value"].median(), factor["cb_double_low_value"].quantile(0.75),
        factor["cb_double_low_value"].max()))

    # 流动性文件：过去 20 交易日日均成交额（元）
    d = daily.sort_values(["code", "trade_date"]).copy()
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d["avg_amount"] = d.groupby("code")["amount"].transform(lambda s: s.rolling(20, min_periods=20).mean()) * 1000.0
    liq = d[d["trade_date"].isin(set(reb))][["ts_code", "trade_date", "avg_amount"]].rename(
        columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
    dst2 = OUT_DIR / "cb_liq_avg_amount_20d.parquet"
    liq.to_parquet(dst2, index=False)
    log("流动性文件写出: {} 行={:,}（20 交易日日均成交额，元）".format(dst2.name, len(liq)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
