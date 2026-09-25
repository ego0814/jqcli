# -*- coding: utf-8 -*-
r"""刷新本地交易日历（cache/trade_cal.parquet）。

背景：原日历由 step1_download 下载，只覆盖到 2026-09-18，且按日期**倒序**存储，
导致 run_monthly.ps1 的"本月最后一个交易日"守卫失效。

本脚本用 Tushare trade_cal 重新拉取（默认 2015-01-01 ~ 次年年末），保持原列结构：
    exchange, cal_date, is_open, pretrade_date

用法：
    python refresh_trade_cal.py [--start 20150101] [--end 20271231]
"""
from __future__ import annotations
import argparse
import os
import shutil
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"
CAL_PATH = CACHE / "trade_cal.parquet"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新交易日历")
    ap.add_argument("--start", default="20150101")
    ap.add_argument("--end", default="20271231")
    args = ap.parse_args()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        log("缺少 TUSHARE_TOKEN，无法刷新")
        return 2
    import tushare as ts
    pro = ts.pro_api(token)

    old_max = None
    if CAL_PATH.exists():
        old = pd.read_parquet(CAL_PATH)
        old_max = old["cal_date"].astype(str).max()
        log("旧日历: {} 行，最大日期 {}".format(len(old), old_max))
        shutil.copy2(CAL_PATH, CAL_PATH.with_suffix(".parquet.bak"))

    cal = pro.trade_cal(exchange="SSE", start_date=args.start, end_date=args.end)
    if cal is None or cal.empty:
        log("Tushare 返回空，未更新")
        return 1
    cols = [c for c in ("exchange", "cal_date", "is_open", "pretrade_date") if c in cal.columns]
    cal = cal[cols].sort_values("cal_date").reset_index(drop=True)
    cal.to_parquet(CAL_PATH, index=False)
    trade = cal[cal["is_open"].astype(int) == 1]["cal_date"].astype(str)
    log("新日历: {} 行 / 交易日 {} 天 / {} ~ {}（升序存储）".format(
        len(cal), len(trade), trade.min(), trade.max()))
    log("已写出 {}（旧文件备份为 trade_cal.parquet.bak）".format(CAL_PATH.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
