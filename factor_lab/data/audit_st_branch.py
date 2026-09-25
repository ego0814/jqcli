# -*- coding: utf-8 -*-
r"""ST 分支实测：用 panel 中 ST 的 DROPPED 行验证 5% vs 10% 阈值差异。"""
from __future__ import annotations
import sys, time
from pathlib import Path
import numpy as np, pandas as pd

LAB = Path(__file__).resolve().parent.parent
CACHE = LAB / "data" / "cache"
PANEL = LAB / "output" / "factor_values" / "fscore_panel.parquet"
MARGIN = 0.002


def norm(v):
    t = str(v).replace("-", "")[:8]
    return "{}-{}-{}".format(t[:4], t[4:6], t[6:8]) if len(t) == 8 and t.isdigit() else None


t0 = time.time()
panel = pd.read_parquet(PANEL, columns=["symbol", "rebalance_date", "row_status", "drop_reasons"])
st_rows = panel[panel["row_status"] == "DROPPED"]
mask_series = st_rows["drop_reasons"].apply(lambda v: "ST" in list(v) if v is not None else False)
st_rows = st_rows[mask_series]
print("ST 行总数: {:,}".format(len(st_rows)), flush=True)
print("涉及股票 {} 只，调仓日 {}".format(st_rows["symbol"].nunique(), st_rows["rebalance_date"].nunique()))

cal = pd.read_parquet(CACHE / "trade_cal.parquet")
cal = cal[cal["is_open"].astype(int) == 1]
days = sorted({norm(d) for d in cal["cal_date"].astype(str)})
days = [d for d in days if d]
pos = {d: i for i, d in enumerate(days)}
tmap = {}
for T in st_rows["rebalance_date"].unique():
    i = pos.get(T)
    if i is not None and i + 21 < len(days):
        tmap[T] = (days[i + 1], days[i + 21])
print("可算下期窗口的调仓日: {}/{}".format(len(tmap), st_rows["rebalance_date"].nunique()), flush=True)

mask = pd.read_parquet(CACHE / "st_mask.parquet")
mask.index = [norm(i) for i in mask.index]
pos_by_date = {d: k for k, d in enumerate(mask.index)}
pos_by_sym = {str(c): k for k, c in enumerate(mask.columns)}
di = st_rows["rebalance_date"].map(lambda d: pos_by_date.get(d, -1)).to_numpy()
sj = st_rows["symbol"].map(lambda s: pos_by_sym.get(str(s), -1)).to_numpy()
valid = (di >= 0) & (sj >= 0)
is_st = np.zeros(len(st_rows), dtype=bool)
is_st[valid] = mask.values[di[valid], sj[valid]]
print("st_mask 判定为 ST 的行数: {:,} / {:,}（可查 {:,}）".format(int(is_st.sum()), len(st_rows), int(valid.sum())), flush=True)

sym_filter = list(st_rows["symbol"].unique())
parts = []
for path in sorted(CACHE.glob("px_*.parquet")):
    frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "pct_chg"],
                            filters=[("ts_code", "in", sym_filter)])
    if not frame.empty:
        frame["trade_date"] = frame["trade_date"].map(norm)
        parts.append(frame)
px = pd.concat(parts, ignore_index=True)
print("px 读取: {:,} 行，用时 {:.1f}s".format(len(px), time.time() - t0), flush=True)

work = st_rows[["symbol", "rebalance_date"]].copy()
work["d1"] = work["rebalance_date"].map(lambda T: tmap.get(T, (None, None))[0])
work["d21"] = work["rebalance_date"].map(lambda T: tmap.get(T, (None, None))[1])
p1 = px.rename(columns={"ts_code": "symbol", "trade_date": "d1", "pct_chg": "p1"})[["symbol", "d1", "p1"]]
p21 = px.rename(columns={"ts_code": "symbol", "trade_date": "d21", "pct_chg": "p21"})[["symbol", "d21", "p21"]]
work = work.merge(p1, on=["symbol", "d1"], how="left").merge(p21, on=["symbol", "d21"], how="left")

def hit(pct, th, up=True):
    a = np.abs(pd.Series(pct) / 100.0 - (th if up else -th)) <= MARGIN
    return a.fillna(False)

up5, dn5 = hit(work["p1"], 0.05, True), hit(work["p21"], 0.05, False)
up10, dn10 = hit(work["p1"], 0.10, True), hit(work["p21"], 0.10, False)
st5 = up5 | dn5
st10 = up10 | dn10
missed = st5 & ~st10
print("")
print("ST 行总数        : {:,}".format(len(work)))
print("5% 阈值命中      : {:,}（涨停 {:,} + 跌停 {:,}）".format(int(st5.sum()), int(up5.sum()), int(dn5.sum())))
print("10% 阈值命中     : {:,}（涨停 {:,} + 跌停 {:,}）".format(int(st10.sum()), int(up10.sum()), int(dn10.sum())))
print("被 10% 漏判      : {:,}".format(int(missed.sum())))
print("总耗时: {:.1f}s".format(time.time() - t0))
