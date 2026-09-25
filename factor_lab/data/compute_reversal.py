# -*- coding: utf-8 -*-
r"""1 个月反转因子：过去 21 个交易日收益率的相反数。

reversal_value = -(close_T / close_{T-21} - 1)
用 pct_chg 逐日复利计算（等价前复权收益），避免不复权 close 比值在除权日的偏差。
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
import numpy as np, pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
LOOKBACK = 21


def norm_date(v):
    t = str(v).replace("-", "")[:8]
    return "{}-{}-{}".format(t[:4], t[4:6], t[6:8]) if len(t) == 8 and t.isdigit() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="计算 1 个月反转因子")
    ap.add_argument("--input", default=str(OUT_DIR / "fscore_panel.parquet"))
    ap.add_argument("--output", default=str(OUT_DIR / "reversal.parquet"))
    args = ap.parse_args()
    t0 = time.time()

    panel = pd.read_parquet(Path(args.input), columns=["symbol", "rebalance_date", "row_status", "pit_status"])
    panel = panel[panel["row_status"] == "OK"][["symbol", "rebalance_date", "pit_status"]].drop_duplicates()
    symbols = sorted(panel["symbol"].unique())
    print("[reversal] 骨架行数 {:,}，股票 {}".format(len(panel), len(symbols)), flush=True)

    parts = []
    for path in sorted(CACHE.glob("px_*.parquet")):
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "pct_chg"],
                                filters=[("ts_code", "in", symbols)])
        if not frame.empty:
            frame["trade_date"] = frame["trade_date"].map(norm_date)
            parts.append(frame)
    px = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["ts_code", "trade_date", "pct_chg"])
    px = px.dropna(subset=["trade_date", "pct_chg"])
    print("[reversal] px 行数 {:,}（{:.1f}s）".format(len(px), time.time() - t0), flush=True)

    rows = []
    n_extreme = 0
    for code, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date")
        d = g["trade_date"].tolist()
        p = g["pct_chg"].to_numpy(dtype=float)
        extreme = np.abs(p) > 60.0  # A股单日上限±30%（北交所），超±60%必为脏数据
        n_extreme += int(extreme.sum())
        p = np.where(extreme, 0.0, p)
        cum = np.cumprod(1.0 + p / 100.0)
        pos = {x: i for i, x in enumerate(d)}
        for T in panel.loc[panel["symbol"] == code, "rebalance_date"]:
            i = pos.get(T)
            if i is None or i < LOOKBACK:
                continue
            ret = cum[i] / cum[i - LOOKBACK] - 1.0
            rows.append((code, T, -float(ret)))
    out = panel.merge(pd.DataFrame(rows, columns=["symbol", "rebalance_date", "reversal_value"]),
                      on=["symbol", "rebalance_date"], how="left")
    out["complete"] = out["reversal_value"].notna()
    out = out[["symbol", "rebalance_date", "reversal_value", "complete", "pit_status"]]
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst, index=False)
    v = out["reversal_value"].dropna()
    print("")
    print("输出: {} rows={:,} bytes={:,}".format(dst.name, len(out), dst.stat().st_size))
    print("complete=True: {:,} ({:.2%})".format(int(out["complete"].sum()), out["complete"].mean()))
    print("reversal_value 分布: min={:.6f} p25={:.6f} median={:.6f} p75={:.6f} max={:.6f}".format(
        v.min(), v.quantile(.25), v.median(), v.quantile(.75), v.max()))
    print("抽样 3 行:")
    print(out.dropna(subset=["reversal_value"]).head(3).to_string(index=False))
    print("[reversal] 过滤极端 pct_chg(|x|>60%): {:,} 条".format(n_extreme))
    print("[reversal] 总耗时: {:.2f}s".format(time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
