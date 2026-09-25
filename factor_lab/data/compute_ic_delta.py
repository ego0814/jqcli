# -*- coding: utf-8 -*-
r"""F-score 变化量（ΔF）的 IC：ΔF = f_score_T - 上一个 rebalance 日的 f_score。

F-score 只在年报切换时变化，多数月份 ΔF = 0，因此同时报告：
    全部月份（ΔF 多为 0，横截面区分度低）
    切换月（ΔF != 0 的月份，才是真正有信号的时点）

用法：
    python compute_ic_delta.py --input output\factor_values\fscore_sample.parquet --output output\factor_values\fscore_ic_delta_sample.parquet
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
MIN_NONZERO_RATIO = 0.05


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="F-score 变化量 IC")
    parser.add_argument("--input", default=str(OUT_DIR / "fscore.parquet"))
    parser.add_argument("--output", default=str(OUT_DIR / "fscore_ic_delta.parquet"))
    parser.add_argument("--chunk-symbols", type=int, default=500)
    parser.add_argument("--horizon", type=int, default=21)
    args = parser.parse_args()

    factor = pd.read_parquet(Path(args.input), columns=["symbol", "rebalance_date", "f_score", "complete"])
    factor = factor[factor["complete"] & factor["f_score"].notna()].copy()
    factor["f_score"] = factor["f_score"].astype(float)
    factor = factor.sort_values(["symbol", "rebalance_date"])
    factor["delta_f"] = factor.groupby("symbol")["f_score"].diff()
    rebalance_dates = sorted(factor["rebalance_date"].unique())
    symbols = sorted(factor["symbol"].unique())
    log("因子行数: {:,}  ΔF 非空: {:,}  其中非零: {:,}（{:.2%}）".format(
        len(factor), int(factor["delta_f"].notna().sum()),
        int((factor["delta_f"].fillna(0) != 0).sum()),
        float((factor["delta_f"].fillna(0) != 0).mean())))

    cal = trading_calendar()
    tmap = forward_target(cal, rebalance_dates, args.horizon)

    rows = []
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
                    rows.append((T, code, ret))
        log("  批次 {}-{}: 完成".format(start + 1, start + len(batch)))

    returns = pd.DataFrame(rows, columns=["rebalance_date", "symbol", "forward_return"])
    merged = factor.merge(returns, on=["symbol", "rebalance_date"], how="inner")
    log("合并后样本: {:,} 行".format(len(merged)))

    ic_rows = []
    for T, group in merged.groupby("rebalance_date"):
        g = group[group["delta_f"].notna()]
        if len(g) < MIN_SYMBOLS:
            continue
        nonzero_ratio = float((g["delta_f"] != 0).mean())
        if g["delta_f"].std() == 0 or pd.isna(g["delta_f"].std()):
            continue
        icon = pd.Series(g["delta_f"].to_numpy()).rank().corr(pd.Series(g["forward_return"].to_numpy()).rank())
        record = {"rebalance_date": T, "n_symbols": int(len(g)),
                  "switch_ratio": nonzero_ratio, "rank_ic_delta": float(icon),
                  "rank_ic_level": float(pd.Series(g["f_score"].to_numpy()).rank().corr(
                      pd.Series(g["forward_return"].to_numpy()).rank()))}
        try:
            layer = pd.qcut(pd.Series(g["delta_f"]).rank(method="first"), LAYERS, labels=False)
            layer_ret = g["forward_return"].groupby(layer).mean()
            for q in range(LAYERS):
                record["q{}_return".format(q + 1)] = float(layer_ret.get(q, np.nan))
            record["q5_minus_q1"] = record["q5_return"] - record["q1_return"]
        except ValueError:
            pass
        ic_rows.append(record)

    if not ic_rows:
        log("没有可用的 IC 记录。")
        return 1
    ic = pd.DataFrame(ic_rows).sort_values("rebalance_date")
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ic.to_parquet(dst, index=False)

    switch = ic[ic["switch_ratio"] >= MIN_NONZERO_RATIO]
    for label, frame in (("全部月份", ic), ("切换月（ΔF 非零占比 >= {:.0%}）".format(MIN_NONZERO_RATIO), switch)):
        if frame.empty:
            log("{}: 无记录".format(label))
            continue
        m, s = frame["rank_ic_delta"].mean(), frame["rank_ic_delta"].std(ddof=1)
        log("{}: {} 个月  ΔF IC 均值 {:.4f}  标准差 {:.4f}  ICIR {:.3f}  胜率 {:.2%}  t {:.2f}".format(
            label, len(frame), m, s, m / s if s else float("nan"), (frame["rank_ic_delta"] > 0).mean(),
            m / (s / np.sqrt(len(frame))) if s else float("nan")))
        if "q5_minus_q1" in frame.columns:
            log("    Q1 {:.3%} | Q5 {:.3%} | Q5-Q1 {:.3%}".format(
                frame["q1_return"].mean(), frame["q5_return"].mean(), frame["q5_minus_q1"].mean()))
        ml, sl = frame["rank_ic_level"].mean(), frame["rank_ic_level"].std(ddof=1)
        log("    同期 f_score 水平值 IC 均值 {:.4f}（对照）".format(ml))
    log("输出: {} rows={} bytes={}".format(dst.name, len(ic), dst.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())