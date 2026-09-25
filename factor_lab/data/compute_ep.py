# -*- coding: utf-8 -*-
r"""EP 因子 = 净利润 TTM / 总市值。

输入：fscore_panel*.parquet（复用其 (symbol, rebalance_date) 骨架与 n_income）
      basic_*.parquet（total_mv）
输出：ep*.parquet

单位：n_income 为元，basic total_mv 为万元 → EP = n_income / (total_mv * 10000)
方向：正向（EP 越高越便宜）。极端值保留，交由后续 winsorize 处理。
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
import numpy as np, pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
MV_TO_YUAN = 10000.0


def norm_date(v):
    t = str(v).replace("-", "")[:8]
    return "{}-{}-{}".format(t[:4], t[4:6], t[6:8]) if len(t) == 8 and t.isdigit() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="计算 EP 因子")
    ap.add_argument("--input", default=str(OUT_DIR / "fscore_panel.parquet"))
    ap.add_argument("--output", default=str(OUT_DIR / "ep.parquet"))
    args = ap.parse_args()
    t0 = time.time()

    panel = pd.read_parquet(Path(args.input), columns=[
        "symbol", "rebalance_date", "period_end_date", "row_status",
        "financial.n_income.value", "financial.n_income.status", "pit_status"])
    print("[ep] 读 panel: {:.2f}s rows={:,}".format(time.time() - t0, len(panel)), flush=True)
    panel = panel[panel["row_status"] == "OK"].copy()
    panel = panel.rename(columns={"financial.n_income.value": "n_income",
                                  "financial.n_income.status": "n_income_status"})
    print("[ep] OK 行: {:,} (status=AVAILABLE {:,})".format(
        len(panel), int((panel["n_income_status"] == "AVAILABLE").sum())), flush=True)

    symbols = sorted(panel["symbol"].unique())
    dates = set(panel["rebalance_date"].unique())
    t1 = time.time()
    parts = []
    for path in sorted(CACHE.glob("basic_*.parquet")):
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "total_mv"],
                                filters=[("ts_code", "in", symbols)])
        if frame.empty:
            continue
        frame["trade_date"] = frame["trade_date"].map(norm_date)
        frame = frame[frame["trade_date"].isin(dates)]
        if not frame.empty:
            parts.append(frame)
    mv = pd.concat(parts, ignore_index=True).rename(
        columns={"ts_code": "symbol", "trade_date": "rebalance_date"}) if parts else pd.DataFrame(
        columns=["symbol", "rebalance_date", "total_mv"])
    print("[ep] 读 total_mv: {:.2f}s rows={:,}".format(time.time() - t1, len(mv)), flush=True)

    merged = panel.merge(mv, on=["symbol", "rebalance_date"], how="left")
    valid_mv = merged["total_mv"].notna() & (merged["total_mv"] > 0)
    merged["ep_value"] = np.where(valid_mv, merged["n_income"] / (merged["total_mv"] * MV_TO_YUAN), np.nan)
    merged["complete"] = merged["ep_value"].notna()
    missing_income = int(merged["n_income"].isna().sum())
    missing_mv = int((~valid_mv).sum())
    print("[ep] 缺失: n_income {:,} / total_mv {:,}".format(missing_income, missing_mv), flush=True)

    out = merged[["symbol", "rebalance_date", "period_end_date", "ep_value", "complete", "pit_status"]]
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst, index=False)
    v = out["ep_value"].dropna()
    print("")
    print("输出: {} rows={:,} bytes={:,}".format(dst.name, len(out), dst.stat().st_size))
    print("complete=True: {:,} ({:.2%})".format(int(out["complete"].sum()), out["complete"].mean()))
    print("ep_value 分布: min={:.6f} p25={:.6f} median={:.6f} p75={:.6f} max={:.6f}".format(
        v.min(), v.quantile(0.25), v.median(), v.quantile(0.75), v.max()))
    print("抽样 3 行:")
    print(out.dropna(subset=["ep_value"]).head(3).to_string(index=False))
    print("[ep] 总耗时: {:.2f}s".format(time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
