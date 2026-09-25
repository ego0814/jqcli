# -*- coding: utf-8 -*-
r"""三元组抽样验证：从 px_merged.parquet 随机取 1000 行，回原始分片比对。"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).resolve().parent / "cache"
n = 1000
merged = pd.read_parquet(CACHE / "px_merged.parquet", columns=["ts_code", "trade_date", "pct_chg", "vol", "amount"])
sample = merged.sample(n=n, random_state=42)
print("抽样 {} 行，合并文件总行数 {:,}".format(len(sample), len(merged)), flush=True)

cache_by_year = {}
matched = mismatch = missing = 0
diffs = []
for row in sample.itertuples(index=False):
    year = str(row.trade_date)[:4]
    if year not in cache_by_year:
        path = CACHE / "px_{}.parquet".format(year)
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "pct_chg", "vol", "amount"])
        frame["trade_date"] = frame["trade_date"].astype(str)
        cache_by_year[year] = frame.set_index(["ts_code", "trade_date"])
    key = (row.ts_code, str(row.trade_date))
    try:
        src = cache_by_year[year].loc[key]
    except KeyError:
        missing += 1
        continue
    if isinstance(src, pd.DataFrame):
        src = src.iloc[0]
    ok = (abs(float(src["pct_chg"]) - float(row.pct_chg)) < 1e-9
          and abs(float(src["vol"]) - float(row.vol)) < 1e-9
          and abs(float(src["amount"]) - float(row.amount)) < 1e-6)
    if ok:
        matched += 1
    else:
        mismatch += 1
        if len(diffs) < 5:
            diffs.append((key, src["pct_chg"], row.pct_chg, src["vol"], row.vol))

print("一致 {:,} | 不一致 {} | 缺失 {}".format(matched, mismatch, missing))
for d in diffs:
    print("  差异:", d)
print("PASS" if (matched == n and mismatch == 0 and missing == 0) else "FAIL")
