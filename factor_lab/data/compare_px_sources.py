# -*- coding: utf-8 -*-
r"""对照实验：同一批 (symbol, T) 分别用 px_merged 与 12 分片计算窗口特征并逐行 diff。"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

LAB = Path(__file__).resolve().parent.parent
CACHE = LAB / "data" / "cache"
COLS = ["ts_code", "trade_date", "pct_chg", "vol", "amount"]


def norm(v):
    t = str(v).replace("-", "")[:8]
    return "{}-{}-{}".format(t[:4], t[4:6], t[6:8]) if len(t) == 8 and t.isdigit() else None


factor = pd.read_parquet(LAB / "output" / "factor_values" / "fscore_sample.parquet",
                         columns=["symbol", "rebalance_date", "f_score", "complete"])
pairs = (factor[factor["complete"] & factor["f_score"].notna()][["symbol", "rebalance_date"]]
         .drop_duplicates().head(100).reset_index(drop=True))
print("对照样本: {} 组 (symbol, T)".format(len(pairs)), flush=True)

cal = pd.read_parquet(CACHE / "trade_cal.parquet")
cal = cal[cal["is_open"].astype(int) == 1]
days = sorted({norm(d) for d in cal["cal_date"].astype(str)})
days = [d for d in days if d]
pos = {d: i for i, d in enumerate(days)}
win_map = {}
for T in pairs["rebalance_date"].unique():
    i = pos.get(T)
    if i is not None and i + 21 < len(days):
        win_map[T] = days[i + 1:i + 22]
syms = sorted(pairs["symbol"].unique())
needed = sorted({d for w in win_map.values() for d in w})
print("涉及股票 {} 只，需要的交易日 {} 个".format(len(syms), len(needed)), flush=True)

def build_index(frames):
    data = pd.concat(frames, ignore_index=True)
    data["trade_date"] = data["trade_date"].astype(str).map(norm)
    data = data[data["trade_date"].isin(needed)]
    data = data[data["ts_code"].isin(syms)]
    return {(r.ts_code, r.trade_date): (r.pct_chg, r.vol, r.amount) for r in data.itertuples(index=False)}

merged_index = build_index([pd.read_parquet(CACHE / "px_merged.parquet", columns=COLS)])
print("合并文件索引: {:,} 条".format(len(merged_index)), flush=True)
shards = []
for f in sorted(CACHE.glob("px_*.parquet")):
    if f.name == "px_merged.parquet":
        continue
    d = pd.read_parquet(f, columns=COLS, filters=[("ts_code", "in", syms)])
    if not d.empty:
        shards.append(d)
shard_index = build_index(shards)
print("分片索引: {:,} 条".format(len(shard_index)), flush=True)

def features(index, symbol, T):
    window = win_map.get(T)
    if not window:
        return None
    rows = [index.get((symbol, d)) for d in window]
    present = [r for r in rows if r is not None]
    amounts = [r[2] for r in present if r[2] == r[2]]
    susp = sum(1 for r in present if r[1] != r[1] or r[1] == 0)
    first = index.get((symbol, window[0]))
    last = index.get((symbol, window[-1]))
    return (sum(amounts) / len(amounts) * 1000.0 if amounts else 0.0,
            susp,
            None if first is None else first[0],
            None if last is None else last[0])

same = diff = 0
details = []
for r in pairs.itertuples(index=False):
    a = features(merged_index, r.symbol, r.rebalance_date)
    b = features(shard_index, r.symbol, r.rebalance_date)
    if a == b:
        same += 1
    else:
        diff += 1
        if len(details) < 10:
            details.append((r.symbol, r.rebalance_date, a, b))
print("")
print("一致 {:,} | 不一致 {:,}".format(same, diff))
for d in details:
    print("  差异:", d)
print("结论:", "PASS（无回归）" if diff == 0 else "FAIL（需回退）")
