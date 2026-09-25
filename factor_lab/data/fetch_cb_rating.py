# -*- coding: utf-8 -*-
r"""抓取/刷新可转债主体评级（Tushare cb_rating）。

接口特性（2026-09-22 实测）：
    - cb_rating **必须逐只传 ts_code**，没有"按日期拉全市场"的用法
    - 单只返回全部历史评级（首次评级 + 历年跟踪评级），字段：
      ts_code, ann_date, rating_date, rating_com_name, rating_way,
      rating_type, rating, rating_outlook
    - 311 只在市转债约 135 秒（sleep 0.3）；全部 1,167 只约 9 分钟
    - ann_date 无空值（2018-08 起），可做 PIT 过滤

增量逻辑：
    - 未缓存的券 → 必拉
    - 已缓存但最新 ann_date 距今 > --stale-days（默认 30 天）→ 拉（评级可能更新）
    - 已缓存且较新 → 跳过，不调用接口
    - 合并后按 (ts_code, ann_date, rating_date) 去重（keep=last）
      （实测 (ts_code, ann_date) 会有重复：同日多条评级动作 rating_date 不同）

输出：cache/cb_rating.parquet

用法：
    python fetch_cb_rating.py                        # 增量（默认），回补缺失的券
    python fetch_cb_rating.py --full                 # 全量重拉（1,167 只，约 9 分钟）
    python fetch_cb_rating.py --stale-days 7         # 更激进的刷新
    python fetch_cb_rating.py --start 20180101 --sleep 0.3
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"
CB_BASIC = CACHE / "cb_basic.parquet"
RATING_PATH = CACHE / "cb_rating.parquet"
KEEP_COLS = ["ts_code", "ann_date", "rating_date", "rating_com_name",
             "rating_way", "rating_type", "rating", "rating_outlook"]
DEDUP_KEYS = ["ts_code", "ann_date", "rating_date"]


def log(message: str) -> None:
    print(message, flush=True)


def get_pro():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        sys.exit("ERROR: TUSHARE_TOKEN 未设置")
    import tushare as ts
    return ts.pro_api(token)


def load_cache() -> pd.DataFrame:
    if not RATING_PATH.exists():
        return pd.DataFrame(columns=KEEP_COLS)
    frame = pd.read_parquet(RATING_PATH)
    for col in KEEP_COLS:
        if col not in frame.columns:
            frame[col] = None
    return frame[KEEP_COLS]


def fetch_one(pro, ts_code: str):
    """拉单只评级；失败重试 2 次。返回 (DataFrame|None, err)"""
    err = None
    for attempt in range(3):
        try:
            frame = pro.cb_rating(ts_code=ts_code)
            return frame, None
        except Exception as exc:
            err = exc
            time.sleep(1.0 * (attempt + 1))
    return None, err


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取/刷新可转债主体评级")
    ap.add_argument("--incremental", action="store_true", help="增量模式（默认）")
    ap.add_argument("--full", action="store_true", help="全量重拉（忽略缓存）")
    ap.add_argument("--start", default="20180101", help="只保留 ann_date >= 该日期的记录（默认 20180101）")
    ap.add_argument("--sleep", type=float, default=0.3, help="每只之间的间隔秒数（默认 0.3）")
    ap.add_argument("--stale-days", type=int, default=30,
                    help="已缓存且最新 ann_date 距今 <= 该天数则跳过（默认 30）")
    args = ap.parse_args()

    pro = get_pro()
    if not CB_BASIC.exists():
        sys.exit("ERROR: 缺少 {}".format(CB_BASIC))
    basic = pd.read_parquet(CB_BASIC)
    all_codes = sorted(basic["ts_code"].dropna().astype(str).unique())

    old = load_cache()
    old["ann_date"] = old["ann_date"].astype(str)
    log("[评级] 缓存 {:,} 行 / {} 只".format(len(old), old["ts_code"].nunique() if len(old) else 0))

    today = datetime.today()
    if args.full or old.empty:
        todo = all_codes
        mode = "全量" if args.full else "全量（缓存为空）"
    else:
        last_ann = old.groupby("ts_code")["ann_date"].max()
        todo = []
        for code in all_codes:
            prev = last_ann.get(code)
            if prev is None or str(prev) == "nan":
                todo.append(code)
                continue
            try:
                gap = (today - datetime.strptime(str(prev)[:8], "%Y%m%d")).days
            except ValueError:
                gap = 9999
            if gap > args.stale_days:
                todo.append(code)
        mode = "增量"

    log("[评级] {}模式：cb_basic {} 只，待拉取 {} 只（其余跳过）".format(mode, len(all_codes), len(todo)))
    if not todo:
        log("[评级] 无需拉取")
        if RATING_PATH.exists():
            RATING_PATH.touch()  # 已检查仍在 stale-days 窗口内
        return 0

    frames, failed, empty = [], [], []
    t0 = time.time()
    for i, code in enumerate(todo, 1):
        frame, err = fetch_one(pro, code)
        if err is not None:
            failed.append((code, str(err)[:120]))
        elif frame is None or frame.empty:
            empty.append(code)
        else:
            frame = frame.copy()
            frame["ts_code"] = code
            frames.append(frame)
        if i % 50 == 0:
            got = sum(len(f) for f in frames)
            log("  进度 {}/{}，累计 {:,} 行，用时 {:.0f}s".format(i, len(todo), got, time.time() - t0))
        time.sleep(args.sleep)

    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=KEEP_COLS)
    merged = pd.concat([old, new], ignore_index=True) if len(new) else old.copy()
    for col in KEEP_COLS:
        if col not in merged.columns:
            merged[col] = None
    merged = merged[KEEP_COLS]
    merged["ann_date"] = merged["ann_date"].astype(str)
    merged = merged[merged["ann_date"] >= args.start]
    merged = merged.sort_values(["ts_code", "rating_date", "ann_date"]).drop_duplicates(DEDUP_KEYS, keep="last")
    merged.to_parquet(RATING_PATH, index=False)

    log("[评级] 新增 {:,} 行（{:,} -> {:,}）/ 覆盖 {} 只 / ann_date {} ~ {}".format(
        len(merged) - len(old), len(old), len(merged), merged["ts_code"].nunique(),
        merged["ann_date"].min(), merged["ann_date"].max()))
    if empty:
        log("[评级] 无评级记录 {} 只：{}".format(len(empty), ", ".join(empty[:15])))
    if failed:
        log("[评级] 失败 {} 只：{}".format(len(failed), ", ".join(c for c, _ in failed[:10])))
    log("[评级] 刷新完成：用时 {:.0f}s -> {}".format(time.time() - t0, RATING_PATH.name))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
