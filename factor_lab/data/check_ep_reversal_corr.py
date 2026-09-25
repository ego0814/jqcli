# -*- coding: utf-8 -*-
r"""EP 与 F-score 的横截面相关性检验（逐月 Spearman）。"""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd

OUT = Path(__file__).resolve().parent.parent / "output" / "factor_values"
ep = pd.read_parquet(OUT / "ep.parquet", columns=["symbol", "rebalance_date", "ep_value", "complete"])
fs = pd.read_parquet(OUT / "reversal.parquet", columns=["symbol", "rebalance_date", "reversal_value", "complete"])
ep = ep[ep["complete"] & ep["ep_value"].notna()][["symbol", "rebalance_date", "ep_value"]]
fs = fs[fs["complete"] & fs["reversal_value"].notna()][["symbol", "rebalance_date", "reversal_value"]]
m = ep.merge(fs, on=["symbol", "rebalance_date"], how="inner")
print("EP 行数 {:,} | F-score 行数 {:,} | 内连接 {:,}".format(len(ep), len(fs), len(m)), flush=True)
m["rank_ep"] = m.groupby("rebalance_date")["ep_value"].rank()
m["rank_rv"] = m.groupby("rebalance_date")["reversal_value"].rank()
rows = []
for T, g in m.groupby("rebalance_date"):
    if len(g) < 20: continue
    c = g["rank_ep"].corr(g["rank_rv"])
    if not pd.isna(c): rows.append({"rebalance_date": T, "n": len(g), "corr": float(c)})
c = pd.DataFrame(rows)
print("")
print("逐月 Spearman 相关（{} 个月，平均每截面 {:.0f} 只）".format(len(c), c["n"].mean()))
print("  均值 {:.4f} | 标准差 {:.4f} | 中位 {:.4f}".format(c["corr"].mean(), c["corr"].std(ddof=1), c["corr"].median()))
print("  分位: 5% {:.4f} | 25% {:.4f} | 75% {:.4f} | 95% {:.4f}".format(
    c["corr"].quantile(.05), c["corr"].quantile(.25), c["corr"].quantile(.75), c["corr"].quantile(.95)))
print("  区间: min {:.4f} ~ max {:.4f}".format(c["corr"].min(), c["corr"].max()))
print("  正相关月份占比: {:.1%}".format((c["corr"] > 0).mean()))
mean_c = c["corr"].mean()
verdict = "低相关（可组合）" if abs(mean_c) < 0.3 else ("中等相关" if abs(mean_c) <= 0.5 else "高相关（信息重叠）")
print("  判断:", verdict)
c.to_parquet(OUT / "ep_reversal_corr.parquet", index=False)
print("  输出: ep_reversal_corr.parquet rows={}".format(len(c)))
