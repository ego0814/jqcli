# -*- coding: utf-8 -*-
"""
阶段1: 下载全A股 10 年行情数据 (tushare)
=====================================
输出: cache/ 目录下按年分片的 parquet
  px_{year}.parquet      : 日线行情(含复权价)
  basic_{year}.parquet   : 每日指标(市值/换手/PE/PB)
  stock_basic.parquet    : 股票列表
  trade_cal.parquet      : 交易日历

设计要点:
- 按 trade_date 单日全市场拉取(0.3s/天), 比按股票拉快得多
- 断点续传: 已存在的分片文件直接跳过
- 缓存原始未复权价 + adj_factor, 复权在计算阶段做
"""
import os
import sys
import time
import traceback

import pandas as pd
import tushare as ts

TOKEN = os.environ.get("TUSHARE_TOKEN")
if not TOKEN:
    sys.exit("ERROR: TUSHARE_TOKEN 未设置")
pro = ts.pro_api(TOKEN)

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
os.makedirs(CACHE, exist_ok=True)

START, END = "20150101", "20260918"


def log(*a):
    print(*a, flush=True)


# ---------- 1. 交易日历 ----------
cal_path = os.path.join(CACHE, "trade_cal.parquet")
if not os.path.exists(cal_path):
    cal = pro.trade_cal(exchange="SSE", start_date=START, end_date=END)
    cal.to_parquet(cal_path, index=False)
    log(f"[cal] 写入 {len(cal)} 行")
cal = pd.read_parquet(cal_path)
cal["cal_date"] = cal["cal_date"].astype(str)
trade_days = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].tolist())
log(f"[cal] 交易日 {len(trade_days)} 天: {trade_days[0]} ~ {trade_days[-1]}")

# ---------- 2. 股票列表 ----------
sb_path = os.path.join(CACHE, "stock_basic.parquet")
if not os.path.exists(sb_path):
    frames = []
    for st in ["L", "D", "P"]:
        try:
            frames.append(pro.stock_basic(exchange="", list_status=st))
        except Exception as e:
            log(f"[stock_basic] {st} FAIL {str(e)[:60]}")
        time.sleep(0.4)
    sb = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    sb.to_parquet(sb_path, index=False)
    log(f"[stock_basic] 写入 {len(sb)} 行")
sb = pd.read_parquet(sb_path)
log(f"[stock_basic] {len(sb)} 只标的")

# ---------- 3. 按年分片下载 ----------
YEARS = list(range(2015, 2027))
FIELDS_DAILY = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"

for year in YEARS:
    days = [d for d in trade_days if d.startswith(str(year))]
    if not days:
        continue

    px_path = os.path.join(CACHE, f"px_{year}.parquet")
    bs_path = os.path.join(CACHE, f"basic_{year}.parquet")

    # ---- 日线 ----
    if not os.path.exists(px_path):
        rows, fail = [], []
        t0 = time.time()
        for i, d in enumerate(days, 1):
            for attempt in range(3):
                try:
                    df = pro.daily(trade_date=d, fields=FIELDS_DAILY)
                    if df is not None and len(df):
                        rows.append(df)
                    break
                except Exception as e:
                    if attempt == 2:
                        fail.append((d, str(e)[:60]))
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if i % 50 == 0:
                log(f"  [px {year}] {i}/{len(days)} 天, 累计 {sum(len(r) for r in rows)} 行, "
                    f"{time.time()-t0:.0f}s")
            time.sleep(0.13)
        if rows:
            out = pd.concat(rows, ignore_index=True)
            out.to_parquet(px_path, index=False)
            log(f"[px {year}] 完成 {len(out)} 行, 失败 {len(fail)} 天, {time.time()-t0:.0f}s")
            if fail:
                log(f"   失败日: {[f[0] for f in fail][:10]}")
        else:
            log(f"[px {year}] 无数据!")
    else:
        log(f"[px {year}] 已存在, 跳过")

    # ---- daily_basic ----
    if not os.path.exists(bs_path):
        rows, fail = [], []
        t0 = time.time()
        for i, d in enumerate(days, 1):
            for attempt in range(3):
                try:
                    df = pro.daily_basic(trade_date=d)
                    if df is not None and len(df):
                        rows.append(df)
                    break
                except Exception as e:
                    if attempt == 2:
                        fail.append((d, str(e)[:60]))
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if i % 50 == 0:
                log(f"  [basic {year}] {i}/{len(days)} 天, 累计 {sum(len(r) for r in rows)} 行, "
                    f"{time.time()-t0:.0f}s")
            time.sleep(0.13)
        if rows:
            out = pd.concat(rows, ignore_index=True)
            out.to_parquet(bs_path, index=False)
            log(f"[basic {year}] 完成 {len(out)} 行, 失败 {len(fail)} 天, {time.time()-t0:.0f}s")
    else:
        log(f"[basic {year}] 已存在, 跳过")

log("\n===== 下载完成 =====")
for f in sorted(os.listdir(CACHE)):
    p = os.path.join(CACHE, f)
    log(f"  {f:28s} {os.path.getsize(p)/1024/1024:8.2f} MB")
