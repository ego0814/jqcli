# -*- coding: utf-8 -*-
r"""EP + reversal 等权合成因子。

每个 rebalance_date 截面内：
  EP、reversal 各自 winsorize(MAD, n=5) → rank 标准化到 [0,1] → 等权平均
combined_value = 0.5 * ep_rank + 0.5 * reversal_rank
方向：两者均为正向（EP 越高越便宜；reversal 越大越超跌），合成后仍为正向。
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import pandas as pd

LAB = Path(__file__).resolve().parent.parent
OUT = LAB / "output" / "factor_values"
sys.path.insert(0, str(LAB / "analysis"))
from factor_processing import standardize, winsorize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="EP + reversal 合成因子")
    ap.add_argument("--ep-weight", type=float, default=0.74)
    ap.add_argument("--reversal-weight", type=float, default=0.26)
    ap.add_argument("--equal-weight", action="store_true", help="使用等权 0.5/0.5")
    ap.add_argument("--output", default=str(OUT / "ep_reversal.parquet"))
    args = ap.parse_args()
    w_ep = 0.5 if args.equal_weight else args.ep_weight
    w_rv = 0.5 if args.equal_weight else args.reversal_weight
    print("[combo] 权重 EP {:.2f} / reversal {:.2f}".format(w_ep, w_rv), flush=True)
    ep = pd.read_parquet(OUT / "ep.parquet", columns=["symbol", "rebalance_date", "ep_value", "complete", "pit_status"])
    rv = pd.read_parquet(OUT / "reversal.parquet", columns=["symbol", "rebalance_date", "reversal_value", "complete"])
    ep = ep[ep["complete"] & ep["ep_value"].notna()][["symbol", "rebalance_date", "ep_value", "pit_status"]]
    rv = rv[rv["complete"] & rv["reversal_value"].notna()][["symbol", "rebalance_date", "reversal_value"]]
    m = ep.merge(rv, on=["symbol", "rebalance_date"], how="inner")
    print("[combo] EP {:,} + reversal {:,} -> 内连接 {:,}".format(len(ep), len(rv), len(m)), flush=True)

    def _pipeline(s: pd.Series) -> pd.Series:
        return standardize(winsorize(s, method="mad", n=5), method="rank")

    m["ep_rank"] = m.groupby("rebalance_date")["ep_value"].transform(_pipeline)
    m["reversal_rank"] = m.groupby("rebalance_date")["reversal_value"].transform(_pipeline)
    m["combined_value"] = w_ep * m["ep_rank"] + w_rv * m["reversal_rank"]
    m["complete"] = m["combined_value"].notna()
    out = m[["symbol", "rebalance_date", "ep_rank", "reversal_rank", "combined_value", "complete", "pit_status"]]
    dst = Path(args.output)
    out.to_parquet(dst, index=False)
    v = out["combined_value"].dropna()
    print("输出: {} rows={:,} bytes={:,}".format(dst.name, len(out), dst.stat().st_size))
    print("complete=True: {:,} ({:.2%})".format(int(out["complete"].sum()), out["complete"].mean()))
    print("combined_value 分布: min={:.4f} p25={:.4f} median={:.4f} p75={:.4f} max={:.4f}".format(
        v.min(), v.quantile(.25), v.median(), v.quantile(.75), v.max()))
    print("ep_rank 与 reversal_rank 的横截面相关（逐月均值）: {:.4f}".format(
        out.groupby("rebalance_date").apply(lambda g: g["ep_rank"].corr(g["reversal_rank"]), include_groups=False).mean()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
