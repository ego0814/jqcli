# -*- coding: utf-8 -*-
r"""诊断 neutralize 的 merge 是否导致行数膨胀。"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
LAB = Path(__file__).resolve().parent.parent
OUT, CACHE = LAB / "output" / "factor_values", LAB / "data" / "cache"

def norm(v):
    t = str(v).replace("-", "")[:8]
    return "{}-{}-{}".format(t[:4], t[4:6], t[6:8]) if len(t) == 8 and t.isdigit() else None

f = pd.read_parquet(OUT / "ep_sample.parquet", columns=["symbol", "rebalance_date", "complete"])
f = f[f["complete"]][["symbol", "rebalance_date"]]
print("输入因子表: {:,} 行，唯一 (symbol,T): {:,}".format(len(f), len(f.drop_duplicates())))
syms, dates = sorted(f["symbol"].unique()), set(f["rebalance_date"].unique())
parts = []
for p in sorted(CACHE.glob("basic_*.parquet")):
    x = pd.read_parquet(p, columns=["ts_code", "trade_date", "total_mv"], filters=[("ts_code", "in", syms)])
    if x.empty: continue
    x["trade_date"] = x["trade_date"].map(norm)
    x = x[x["trade_date"].isin(dates)]
    if not x.empty: parts.append(x)
mv = pd.concat(parts, ignore_index=True).rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
print("mv 表: {:,} 行，唯一 (symbol,T): {:,}，重复 {:,}".format(
    len(mv), len(mv.drop_duplicates(subset=["symbol", "rebalance_date"])), len(mv) - len(mv.drop_duplicates(subset=["symbol", "rebalance_date"]))))
ind = pd.read_parquet(CACHE / "stock_basic.parquet", columns=["ts_code", "industry"]).rename(columns={"ts_code": "symbol"})
print("industry 表: {:,} 行，唯一 symbol: {:,}，重复 {:,}".format(len(ind), ind["symbol"].nunique(), len(ind) - ind["symbol"].nunique()))
m1 = f.merge(mv.drop_duplicates(subset=["symbol", "rebalance_date"], keep="last"), on=["symbol", "rebalance_date"], how="left")
print("merge mv 后: {:,} 行（去重 mv 后）".format(len(m1)))
m1r = f.merge(mv, on=["symbol", "rebalance_date"], how="left")
print("merge mv 后: {:,} 行（不去重 mv）".format(len(m1r)))
m2 = m1.merge(ind.drop_duplicates(subset=["symbol"], keep="last"), on="symbol", how="left")
print("merge industry 后: {:,} 行".format(len(m2)))
