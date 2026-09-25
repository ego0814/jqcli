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
- 默认区间: START="20150101", END=今天; 可用 --start/--end 覆盖
- --incremental: 分片已存在时只补 [max(trade_date)+1, END], 追加并去重, 不覆盖历史
"""
import argparse
import os
import sys
import time
import traceback
from datetime import datetime

import pandas as pd
import tushare as ts

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
os.makedirs(CACHE, exist_ok=True)


def _existing_dates(_path, _col="trade_date"):
    _d = pd.read_parquet(_path, columns=[_col])
    _s = _d[_col].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    return set(_s.tolist())


def plan_shard(_path, _days, _tag, _year, _logf):
    if not os.path.exists(_path):
        return list(_days)
    if not INCREMENTAL:
        _logf("[{} {}] 已存在, 跳过".format(_tag, _year))
        return []
    _have = _existing_dates(_path)
    _mx = max(_have) if _have else ""
    _todo = [d for d in _days if d not in _have]
    if _todo:
        _gap = [d for d in _todo if d <= _mx]
        _logf("[{} {}] 增量补数 已有至 {} / 待补 {} 天 (内部缺口 {} 天)".format(
            _tag, _year, _mx, len(_todo), len(_gap)))
    else:
        _logf("[{} {}] 已最新 {}, 无需补数据".format(_tag, _year, _mx))
    return _todo


def merge_shard(_path, _rows):
    _new = pd.concat(_rows, ignore_index=True)
    if not os.path.exists(_path):
        return _new
    _out = pd.concat([pd.read_parquet(_path), _new], ignore_index=True)
    _keys = [c for c in ("ts_code", "trade_date") if c in _out.columns]
    if _keys:
        _out = _out.drop_duplicates(_keys, keep="last")
    _order = [c for c in ("trade_date", "ts_code") if c in _out.columns]
    if _order:
        _out = _out.sort_values(_order).reset_index(drop=True)
    return _out

def parse_args():
    ap = argparse.ArgumentParser(description="stage1: download A-share px/basic from tushare")
    ap.add_argument("--start", default="20150101", help="start date YYYYMMDD (default 20150101)")
    ap.add_argument("--end", default=None, help="end date YYYYMMDD (default: today)")
    ap.add_argument("--incremental", action="store_true",
                    help="append only the tail beyond max(trade_date) of each shard")
    return ap.parse_args()


ARGS = parse_args()
START = ARGS.start
END = ARGS.end or datetime.now().strftime("%Y%m%d")
INCREMENTAL = bool(ARGS.incremental)
TODAY = datetime.now().strftime("%Y%m%d")
FAILS = []

TOKEN = os.environ.get("TUSHARE_TOKEN")
if not TOKEN:
    sys.exit("ERROR: TUSHARE_TOKEN 未设置")
pro = ts.pro_api(TOKEN)


def log(*a):
    print(*a, flush=True)


# ---------- 1. 交易日历 ----------
def _cal_coverage_ok(_path, _end):
    if not os.path.exists(_path):
        return False
    try:
        _d = pd.read_parquet(_path, columns=["cal_date"])
    except Exception:
        return False
    return bool(len(_d)) and str(_d["cal_date"].astype(str).max()) >= _end


cal_path = os.path.join(CACHE, "trade_cal.parquet")
if not _cal_coverage_ok(cal_path, END):
    _new = pro.trade_cal(exchange="SSE", start_date=START, end_date=END)
    if os.path.exists(cal_path):
        _new = pd.concat([pd.read_parquet(cal_path), _new], ignore_index=True)
        _new = _new.drop_duplicates(["exchange", "cal_date"], keep="last")
    _new = _new.sort_values("cal_date").reset_index(drop=True)
    _new.to_parquet(cal_path, index=False)
    log("[cal] 写入 {} 行".format(len(_new)))
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
YEARS = list(range(int(START[:4]), int(END[:4]) + 1))
FIELDS_DAILY = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"

for year in YEARS:
    days = [d for d in trade_days if d.startswith(str(year)) and START <= d <= END]
    if not days:
        continue

    px_path = os.path.join(CACHE, f"px_{year}.parquet")
    bs_path = os.path.join(CACHE, f"basic_{year}.parquet")

    # ---- 日线 ----
    px_todo = plan_shard(px_path, days, "px", year, log)
    if px_todo:
        rows, fail = [], []
        t0 = time.time()
        for i, d in enumerate(px_todo, 1):
            got, err = False, ""
            for attempt in range(3):
                try:
                    df = pro.daily(trade_date=d, fields=FIELDS_DAILY)
                    if df is not None and len(df):
                        rows.append(df)
                        got = True
                        break
                    err = "empty response"
                except Exception as e:
                    err = str(e)[:80]
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
            if not got:
                if d >= TODAY:
                    log("   [warn] {} 无数据, 可能尚未发布; 下次运行重试 ({})".format(d, err))
                else:
                    fail.append((d, err))
            if i % 50 == 0:
                log(f"  [px {year}] {i}/{len(px_todo)} 天, 累计 {sum(len(r) for r in rows)} 行, "
                    f"{time.time()-t0:.0f}s")
            time.sleep(0.13)
        if rows:
            out = merge_shard(px_path, rows)
            out.to_parquet(px_path, index=False)
            log(f"[px {year}] 完成 {len(out)} 行, 失败 {len(fail)} 天, {time.time()-t0:.0f}s")
            if fail:
                FAILS.extend(["{0}:{1}".format(year, _d) for _d, _ in fail])
                log("   失败日: {}".format([f[0] for f in fail][:10]))
        else:
            log(f"[px {year}] 无数据!")

    # ---- daily_basic ----
    bs_todo = plan_shard(bs_path, days, "basic", year, log)
    if bs_todo:
        rows, fail = [], []
        t0 = time.time()
        for i, d in enumerate(bs_todo, 1):
            got, err = False, ""
            for attempt in range(3):
                try:
                    df = pro.daily_basic(trade_date=d)
                    if df is not None and len(df):
                        rows.append(df)
                        got = True
                        break
                    err = "empty response"
                except Exception as e:
                    err = str(e)[:80]
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
            if not got:
                if d >= TODAY:
                    log("   [warn] {} 无数据, 可能尚未发布; 下次运行重试 ({})".format(d, err))
                else:
                    fail.append((d, err))
            if i % 50 == 0:
                log(f"  [basic {year}] {i}/{len(bs_todo)} 天, 累计 {sum(len(r) for r in rows)} 行, "
                    f"{time.time()-t0:.0f}s")
            time.sleep(0.13)
        if rows:
            out = merge_shard(bs_path, rows)
            out.to_parquet(bs_path, index=False)
            if fail:
                FAILS.extend(["{0}:{1}".format(year, _d) for _d, _ in fail])
            log("[basic {}] 完成 {} 行, 失败 {} 天, {:.0f}s".format(year, len(out), len(fail), time.time()-t0))

log("\n===== 下载完成 =====")
for f in sorted(os.listdir(CACHE)):
    p = os.path.join(CACHE, f)
    log(f"  {f:28s} {os.path.getsize(p)/1024/1024:8.2f} MB")

if FAILS:
    log("ERROR: {} trade dates failed: {}".format(len(FAILS), FAILS[:10]))
    sys.exit(1)
log("[ok] shards refreshed, no failed trade dates")
