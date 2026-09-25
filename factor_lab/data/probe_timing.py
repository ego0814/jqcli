# -*- coding: utf-8 -*-
r"""加载阶段探针：只复现 compute_ic_processed 的加载逻辑并打点，不跑 IC / 审计。"""
from __future__ import annotations
import sys, time
from pathlib import Path
import pandas as pd

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "data"))
sys.path.insert(0, str(LAB / "analysis"))
import compute_ic_processed as M  # noqa: E402

def mark(name, t0, extra=""):
    print("[probe] {}: {:.2f}s {}".format(name, time.time() - t0, extra), flush=True)

t = time.time()
factor = pd.read_parquet(LAB / "output" / "factor_values" / "fscore.parquet",
                         columns=["symbol", "rebalance_date", "f_score", "complete"])
factor = factor[factor["complete"] & factor["f_score"].notna()][["symbol", "rebalance_date", "f_score"]]
mark("读 factor 表", t, "rows={:,}".format(len(factor)))

t = time.time()
mask = pd.read_parquet(LAB / "data" / "cache" / "st_mask.parquet")
mark("读 st_mask", t, "shape={}".format(mask.shape))
del mask

symbols = sorted(factor["symbol"].unique())
dates = set(factor["rebalance_date"].unique())

t = time.time()
mv, industry = M.load_aux(symbols, dates)
mark("load_aux(basic 12 文件)", t, "mv={:,} industry={:,}".format(len(mv), len(industry)))

t = time.time()
industry_sb = pd.read_parquet(LAB / "data" / "cache" / "stock_basic.parquet", columns=["ts_code", "market"])
mark("读 stock_basic(market)", t, "rows={:,}".format(len(industry_sb)))

t = time.time()
px = M.load_px(M.CACHE, columns=["ts_code", "trade_date", "pct_chg", "vol", "amount"], symbols=symbols)
mark("load_px", t, "rows={:,}".format(len(px)))
del px

t = time.time()
multi = M.build_multi_returns(factor, 500)
mark("收益推导(4 周期)", t, "rows={:,}".format(len(multi)))
print("[probe] DONE", flush=True)
