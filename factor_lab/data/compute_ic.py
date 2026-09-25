# -*- coding: utf-8 -*-
r"""F-score 的 IC（信息系数）分析。

输入：
    output\factor_values\fscore.parquet            因子值（默认）
    data\cache\px_*.parquet                        行情
    data\cache\trade_cal.parquet                   交易日历

收益定义：
    用 Tushare daily 的 pct_chg 逐日复利，等价于前复权收益率。
    原因：close 是不复权原始价，而 pre_close 已做除权除息调整（实测
    000001.SZ 2024-06-14 的 pre_close=10.08 与前一交易日 close=10.80 不等），
    因此 close 比值会在除权日产生偏差，pct_chg 才是真实日收益。
    下期收益 = prod(1 + pct_chg/100) over (T, T+21]，T+21 取全局交易日历第 21 个交易日。

输出：
    output\factor_values\fscore_ic.parquet   每行一个 rebalance 日

用法：
    python compute_ic.py --input output\factor_values\fscore_sample.parquet --output output\factor_values\fscore_ic_sample.parquet
    python compute_ic.py
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
FORWARD_DAYS = 21
MIN_SYMBOLS = 20
LAYERS = 5


def log(message: str) -> None:
    print(message, flush=True)


def norm_date(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    digits = text.replace("-", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return "{}-{}-{}".format(digits[:4], digits[4:6], digits[6:8])
    return text[:10]


def trading_calendar() -> list[str]:
    cal = pd.read_parquet(CACHE / "trade_cal.parquet")
    cal = cal[cal["is_open"].astype(int) == 1]
    days = sorted({norm_date(d) for d in cal["cal_date"].astype(str)})
    return [d for d in days if d]


def forward_target(dates: list[str], rebalance_dates: list[str], horizon: int) -> dict:
    pos = {d: i for i, d in enumerate(dates)}
    out = {}
    for T in rebalance_dates:
        i = pos.get(T)
        if i is None or i + horizon >= len(dates):
            continue
        out[T] = dates[i + horizon]
    return out


def symbol_returns(prices: pd.DataFrame, tmap: dict) -> dict:
    prices = prices.sort_values("trade_date")
    dates = prices["trade_date"].tolist()
    pct = prices["pct_chg"].to_numpy(dtype=float)
    if len(dates) == 0:
        return {}
    valid = ~np.isnan(pct)
    dates = [d for d, ok in zip(dates, valid) if ok]
    pct = pct[valid]
    if len(dates) == 0:
        return {}
    cum = np.cumprod(1.0 + pct / 100.0)
    pos = {d: i for i, d in enumerate(dates)}
    out = {}
    for T, target in tmap.items():
        i = pos.get(T)
        if i is None:
            continue
        j = np.searchsorted(dates, target, side="right") - 1
        if j <= i:
            continue
        out[T] = float(cum[j] / cum[i] - 1.0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="F-score IC 分析")
    parser.add_argument("--input", default=str(OUT_DIR / "fscore.parquet"))
    parser.add_argument("--output", default=str(OUT_DIR / "fscore_ic.parquet"))
    parser.add_argument("--chunk-symbols", type=int, default=500)
    parser.add_argument("--horizon", type=int, default=FORWARD_DAYS)
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    factor = pd.read_parquet(src, columns=["symbol", "rebalance_date", "f_score", "complete"])
    factor = factor[factor["complete"] & factor["f_score"].notna()].copy()
    factor["f_score"] = factor["f_score"].astype(float)
    rebalance_dates = sorted(factor["rebalance_date"].unique())
    log("因子行数: {:,}（complete=True）  rebalance 日: {}  股票: {}".format(
        len(factor), len(rebalance_dates), factor["symbol"].nunique()))

    cal = trading_calendar()
    tmap = forward_target(cal, rebalance_dates, args.horizon)
    log("可计算下期收益的 rebalance 日: {}/{}（horizon={} 个交易日）".format(len(tmap), len(rebalance_dates), args.horizon))
    if not tmap:
        log("没有任何 rebalance 日具备完整的下期窗口。")
        return 1

    symbols = sorted(factor["symbol"].unique())
    px_files = sorted(CACHE.glob("px_*.parquet"))
    rows = []
    for start in range(0, len(symbols), args.chunk_symbols):
        batch = symbols[start:start + args.chunk_symbols]
        parts = []
        for path in px_files:
            frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "pct_chg"],
                                    filters=[("ts_code", "in", batch)])
            if not frame.empty:
                parts.append(frame)
        if not parts:
            continue
        prices = pd.concat(parts, ignore_index=True)
        prices["trade_date"] = prices["trade_date"].map(norm_date)
        for code, group in prices.groupby("ts_code"):
            for T, ret in symbol_returns(group, tmap).items():
                rows.append((T, code, ret))
        log("  批次 {}-{}: 完成".format(start + 1, start + len(batch)))

    if not rows:
        log("没有产出任何收益样本。")
        return 1
    returns = pd.DataFrame(rows, columns=["rebalance_date", "symbol", "forward_return"])
    log("收益样本: {:,} 行".format(len(returns)))

    merged = factor.merge(returns, on=["symbol", "rebalance_date"], how="inner")
    log("与因子合并后样本: {:,} 行".format(len(merged)))

    ic_rows = []
    for T, group in merged.groupby("rebalance_date"):
        if len(group) < MIN_SYMBOLS:
            continue
        rank_ic = group["f_score"].rank().corr(group["forward_return"].rank())  # Spearman = Pearson on ranks（免 scipy 依赖）
        if pd.isna(rank_ic):
            continue
        try:
            layer = pd.qcut(group["f_score"].rank(method="first"), LAYERS, labels=False)
        except ValueError:
            continue
        layer_ret = group.groupby(layer)["forward_return"].mean()
        record = {"rebalance_date": T, "n_symbols": int(len(group)), "rank_ic": float(rank_ic),
                  "mean_forward_return": float(group["forward_return"].mean())}
        for q in range(LAYERS):
            record["q{}_return".format(q + 1)] = float(layer_ret.get(q, np.nan))
        record["q5_minus_q1"] = record["q5_return"] - record["q1_return"]
        ic_rows.append(record)

    ic = pd.DataFrame(ic_rows).sort_values("rebalance_date")
    if ic.empty:
        log("没有可用的 IC 记录。")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    ic.to_parquet(dst, index=False)

    mean_ic = ic["rank_ic"].mean()
    std_ic = ic["rank_ic"].std(ddof=1)
    icir = mean_ic / std_ic if std_ic and not pd.isna(std_ic) else float("nan")
    log("")
    log("=== IC 汇总 ===")
    log("IC 记录数（月度）: {}".format(len(ic)))
    log("RankIC 均值: {:.4f}   标准差: {:.4f}   ICIR: {:.3f}".format(mean_ic, std_ic, icir))
    log("IC > 0 比例（胜率）: {:.2%}".format((ic["rank_ic"] > 0).mean()))
    log("平均持仓股票数: {:.0f}".format(ic["n_symbols"].mean()))
    log("")
    log("=== 分层平均收益（Q1 最低分 → Q5 最高分）===")
    for q in range(LAYERS):
        col = "q{}_return".format(q + 1)
        log("  Q{}: 平均 {:.3%}   胜率 {:.1%}".format(q + 1, ic[col].mean(), (ic[col] > 0).mean()))
    log("  Q5-Q1 多空: 平均 {:.3%}   胜率 {:.1%}".format(ic["q5_minus_q1"].mean(), (ic["q5_minus_q1"] > 0).mean()))
    log("")
    log("输出: {} rows={} bytes={}".format(dst.name, len(ic), dst.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())