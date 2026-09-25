# -*- coding: utf-8 -*-
r"""F-score 正交化残差 IC：对 log(总市值) 回归取残差后再算 RankIC。

输入：fscore*.parquet + basic_*.parquet(total_mv) + px_*.parquet
输出：fscore_ic_neutralized*.parquet（每行一个 rebalance 日）

用法：
    python compute_ic_neutralized.py --input output\factor_values\fscore_sample.parquet --output output\factor_values\fscore_ic_neutralized_sample.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(DATA_DIR))

from compute_ic import forward_target, norm_date, symbol_returns, trading_calendar  # noqa: E402

MIN_SYMBOLS = 20
LAYERS = 5


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="F-score 正交化残差 IC")
    parser.add_argument("--input", default=str(OUT_DIR / "fscore.parquet"))
    parser.add_argument("--output", default=str(OUT_DIR / "fscore_ic_neutralized.parquet"))
    parser.add_argument("--chunk-symbols", type=int, default=500)
    parser.add_argument("--horizon", type=int, default=21)
    args = parser.parse_args()

    factor = pd.read_parquet(Path(args.input), columns=["symbol", "rebalance_date", "f_score", "complete"])
    factor = factor[factor["complete"] & factor["f_score"].notna()].copy()
    factor["f_score"] = factor["f_score"].astype(float)
    rebalance_dates = sorted(factor["rebalance_date"].unique())
    symbols = sorted(factor["symbol"].unique())
    log("因子行数: {:,}  股票: {}  rebalance 日: {}".format(len(factor), len(symbols), len(rebalance_dates)))

    cal = trading_calendar()
    tmap = forward_target(cal, rebalance_dates, args.horizon)
    log("可算下期收益的 rebalance 日: {}/{}".format(len(tmap), len(rebalance_dates)))

    returns, mvs = [], []
    for start in range(0, len(symbols), args.chunk_symbols):
        batch = symbols[start:start + args.chunk_symbols]
        for path in sorted(CACHE.glob("px_*.parquet")):
            frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "pct_chg"],
                                    filters=[("ts_code", "in", batch)])
            if frame.empty:
                continue
            frame["trade_date"] = frame["trade_date"].map(norm_date)
            for code, group in frame.groupby("ts_code"):
                for T, ret in symbol_returns(group, tmap).items():
                    returns.append((T, code, ret))
        for path in sorted(CACHE.glob("basic_*.parquet")):
            frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "total_mv"],
                                    filters=[("ts_code", "in", batch)])
            if frame.empty:
                continue
            frame["trade_date"] = frame["trade_date"].map(norm_date)
            frame = frame[frame["trade_date"].isin(set(tmap.keys()))]
            if not frame.empty:
                mvs.append(frame)
        log("  批次 {}-{}: 完成".format(start + 1, start + len(batch)))

    returns = pd.DataFrame(returns, columns=["rebalance_date", "symbol", "forward_return"])
    mv = pd.concat(mvs, ignore_index=True) if mvs else pd.DataFrame(columns=["ts_code", "trade_date", "total_mv"])
    mv = mv.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
    log("收益样本 {:,} 行，市值样本 {:,} 行".format(len(returns), len(mv)))

    merged = factor.merge(returns, on=["symbol", "rebalance_date"], how="inner")
    merged = merged.merge(mv, on=["symbol", "rebalance_date"], how="inner")
    merged = merged[merged["total_mv"] > 0].copy()
    merged["log_mv"] = np.log(merged["total_mv"].astype(float))
    log("三表合并后样本: {:,} 行".format(len(merged)))

    rows = []
    for T, group in merged.groupby("rebalance_date"):
        if len(group) < MIN_SYMBOLS:
            continue
        y = group["f_score"].to_numpy(dtype=float)
        x = group["log_mv"].to_numpy(dtype=float)
        r = group["forward_return"].to_numpy(dtype=float)
        ok = ~(np.isnan(x) | np.isnan(y) | np.isnan(r))
        x, y, r = x[ok], y[ok], r[ok]
        if len(x) < MIN_SYMBOLS:
            continue
        xd, yd = x - x.mean(), y - y.mean()
        denom = float(xd @ xd)
        if denom == 0:
            continue
        beta = float(xd @ yd) / denom
        resid = yd - beta * xd
        if np.std(resid) == 0:
            continue
        rank_ic_raw = pd.Series(y).rank().corr(pd.Series(r).rank())
        rank_ic_resid = pd.Series(resid).rank().corr(pd.Series(r).rank())
        try:
            layer = pd.qcut(pd.Series(resid).rank(method="first"), LAYERS, labels=False)
        except ValueError:
            continue
        layer_ret = pd.Series(r).groupby(layer).mean()
        record = {"rebalance_date": T, "n_symbols": int(len(x)),
                  "rank_ic_raw": float(rank_ic_raw), "rank_ic_resid": float(rank_ic_resid),
                  "beta_mv": beta}
        for q in range(LAYERS):
            record["q{}_return".format(q + 1)] = float(layer_ret.get(q, np.nan))
        record["q5_minus_q1"] = record["q5_return"] - record["q1_return"]
        rows.append(record)

    if not rows:
        log("没有可用的 IC 记录。")
        return 1
    ic = pd.DataFrame(rows).sort_values("rebalance_date")
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ic.to_parquet(dst, index=False)

    for label, col in (("原始 IC（未正交化）", "rank_ic_raw"), ("残差 IC（对 log_mv 正交化）", "rank_ic_resid")):
        m, s = ic[col].mean(), ic[col].std(ddof=1)
        log("{}: 均值 {:.4f}  标准差 {:.4f}  ICIR {:.3f}  胜率 {:.2%}  t {:.2f}".format(
            label, m, s, m / s if s else float("nan"), (ic[col] > 0).mean(),
            m / (s / np.sqrt(len(ic))) if s else float("nan")))
    log("残差分层收益:")
    for q in range(LAYERS):
        col = "q{}_return".format(q + 1)
        log("  Q{}: {:.3%}".format(q + 1, ic[col].mean()))
    log("  Q5-Q1: {:.3%}".format(ic["q5_minus_q1"].mean()))
    log("输出: {} rows={} bytes={}".format(dst.name, len(ic), dst.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())