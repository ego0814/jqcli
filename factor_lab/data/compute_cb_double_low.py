# -*- coding: utf-8 -*-
r"""可转债双低因子（价格 + 转股溢价率×100）+ 21 日前瞻收益。

输入：
    cache/cb_basic.parquet        转债基础信息（conv_price / stk_code / list_date / delist_date / conv_stop_date）
    cache/cb_daily.parquet        转债日行情（close / amount / pct_chg）
    cache/px_*.parquet            正股日行情（close，用于算转股价值）
    cache/trade_cal.parquet       交易日历

输出：
    output/factor_values/cb_double_low.parquet    symbol, rebalance_date, cb_double_low_value, complete, pit_status
    output/factor_values/cb_returns_21d.parquet   rebalance_date, symbol, forward_return

定义：
    转股价值   = 100 × 正股收盘价 / 转股价
    转股溢价率 = 转债收盘价 / 转股价值 - 1
    双低值     = 转债收盘价 + 转股溢价率 × 100        （**越小越好**）

PIT 局限（必须随结果一起报告）：
    cb_price_chg（转股价变动历史）本账号无权限，只能用 cb_basic 的**当前**转股价；
    对历史上发生过下修或调整的转债，历史溢价率会被扭曲。
    输出行的 pit_status 统一标记为 CB_CONV_PRICE_NOT_PIT。

用法：
    python compute_cb_double_low.py [--horizon 21]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(DATA_DIR))
from compute_ic import forward_target, norm_date, symbol_returns, trading_calendar  # noqa: E402


def log(message: str) -> None:
    print(message, flush=True)


def load_stock_close(stk_codes: list[str], columns=("ts_code", "trade_date", "close")) -> pd.DataFrame:
    parts = []
    for path in sorted(CACHE.glob("px_*.parquet")):
        if path.name == "px_merged.parquet":
            continue
        frame = pd.read_parquet(path, columns=list(columns), filters=[("ts_code", "in", stk_codes)])
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return pd.DataFrame(columns=list(columns))
    out = pd.concat(parts, ignore_index=True)
    out["trade_date"] = out["trade_date"].map(norm_date)
    return out.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")


def monthly_rebalance_dates(calendar: list[str]) -> list[str]:
    """每月第一个交易日。"""
    out, seen = [], set()
    for day in calendar:
        key = day[:7]
        if key not in seen:
            seen.add(key)
            out.append(day)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="可转债双低因子")
    ap.add_argument("--horizon", type=int, default=21, help="前瞻收益交易日数")
    ap.add_argument("--factor-output", default=str(OUT_DIR / "cb_double_low.parquet"))
    ap.add_argument("--returns-output", default=str(OUT_DIR / "cb_returns_21d.parquet"))
    args = ap.parse_args()

    basic = pd.read_parquet(CACHE / "cb_basic.parquet")
    daily = pd.read_parquet(CACHE / "cb_daily.parquet")
    daily["trade_date"] = daily["trade_date"].map(norm_date)
    basic["list_date"] = basic["list_date"].map(norm_date)
    basic["delist_date"] = basic["delist_date"].map(norm_date)
    basic["conv_start_date"] = basic["conv_start_date"].map(norm_date)
    basic["conv_stop_date"] = basic["conv_stop_date"].map(norm_date)
    log("cb_basic {} 行 / cb_daily {:,} 行 {} 只".format(len(basic), len(daily), daily["ts_code"].nunique()))

    basic = basic[["ts_code", "stk_code", "conv_price", "list_date", "delist_date",
                   "conv_start_date", "conv_stop_date"]].drop_duplicates(subset=["ts_code"], keep="last")
    frame = daily.merge(basic, on="ts_code", how="left")
    stk_codes = sorted(frame["stk_code"].dropna().unique())
    log("正股 {} 只，读取正股收盘价…".format(len(stk_codes)))
    stock = load_stock_close(stk_codes)
    log("正股行情 {:,} 行".format(len(stock)))
    frame = frame.merge(stock.rename(columns={"ts_code": "stk_code", "close": "stk_close"}),
                        on=["stk_code", "trade_date"], how="left")

    frame["cb_close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["conv_price"] = pd.to_numeric(frame["conv_price"], errors="coerce")
    frame["stk_close"] = pd.to_numeric(frame["stk_close"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    conv_value = 100.0 * frame["stk_close"] / frame["conv_price"]
    frame["conv_value"] = conv_value
    frame["premium"] = frame["cb_close"] / conv_value - 1.0
    frame["cb_double_low_value"] = frame["cb_close"] + frame["premium"] * 100.0

    ok = (frame["cb_close"] > 0) & (frame["stk_close"] > 0) & (frame["conv_price"] > 0) \
        & (frame["amount"] > 0) & frame["cb_double_low_value"].notna()
    ok &= frame["trade_date"] >= frame["conv_start_date"].fillna("1900-01-01")
    stop = frame["conv_stop_date"].where(frame["conv_stop_date"].notna(), frame["delist_date"]).fillna("2999-12-31")
    ok &= frame["trade_date"] < stop
    frame = frame[ok].copy()
    log("有效样本（转股期内、有正股价格、当日有成交）：{:,} 行 / {} 只".format(len(frame), frame["ts_code"].nunique()))

    calendar = trading_calendar()
    reb_dates = [d for d in monthly_rebalance_dates(calendar) if d >= "2016-01-01"]
    reb_set = set(reb_dates)
    factor = frame[frame["trade_date"].isin(reb_set)][["ts_code", "trade_date", "cb_double_low_value", "cb_close",
                                                       "premium", "amount", "conv_price", "stk_close"]].copy()
    factor = factor.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
    factor["complete"] = True
    factor["pit_status"] = "CB_CONV_PRICE_NOT_PIT"
    factor = factor.sort_values(["rebalance_date", "symbol"]).reset_index(drop=True)
    dst = Path(args.factor_output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    factor.to_parquet(dst, index=False)
    log("因子表写出: {} 行={:,} 调仓日={:,} 转债={:,}（{} ~ {}）".format(
        dst.name, len(factor), factor["rebalance_date"].nunique(), factor["symbol"].nunique(),
        factor["rebalance_date"].min(), factor["rebalance_date"].max()))
    log("双低值分布: min={:.2f} p25={:.2f} 中位={:.2f} p75={:.2f} max={:.2f}".format(
        factor["cb_double_low_value"].min(), factor["cb_double_low_value"].quantile(0.25),
        factor["cb_double_low_value"].median(),
        factor["cb_double_low_value"].quantile(0.75), factor["cb_double_low_value"].max()))

    # 前瞻收益：用 cb_daily 的 pct_chg 复利
    dates = sorted(frame["trade_date"].unique())
    tmap = forward_target(calendar, reb_dates, args.horizon)
    log("前瞻收益：调仓日 {} 个有对应目标日".format(len(tmap)))
    raw = daily.copy()
    rows = []
    for code, group in raw.groupby("ts_code"):
        group = group.sort_values("trade_date")
        for T, ret in symbol_returns(group, tmap).items():
            rows.append((T, code, ret))
    returns = pd.DataFrame(rows, columns=["rebalance_date", "symbol", "forward_return"])
    dst2 = Path(args.returns_output)
    returns.to_parquet(dst2, index=False)
    log("收益缓存写出: {} 行={:,} 调仓日={:,}".format(dst2.name, len(returns), returns["rebalance_date"].nunique()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
