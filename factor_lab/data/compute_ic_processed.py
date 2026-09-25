# -*- coding: utf-8 -*-
r"""带处理管道的 IC 分析：支持多种处理配置与分行业 IC。

设计：下期收益只推导一次，所有配置复用同一份 (symbol, rebalance_date, forward_return)，
     因此 6 种配置的总耗时 ≈ 1 次收益推导 + 6 次横截面处理。

用法：
    python compute_ic_processed.py --configs all
    python compute_ic_processed.py --by-industry --cache-returns output/factor_values/ic_returns_cache.parquet
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
ANALYSIS = LAB_DIR / "analysis"
sys.path.insert(0, str(DATA_DIR))
sys.path.insert(0, str(ANALYSIS))

from compute_ic import forward_target, norm_date, symbol_returns, trading_calendar  # noqa: E402
from factor_processing import standardize, winsorize  # noqa: E402
from neutralize import neutralize_both, neutralize_industry, neutralize_market_cap  # noqa: E402
from tradability import filter_tradable  # noqa: E402

MIN_SYMBOLS = 20
MIN_INDUSTRY_SYMBOLS = 10
LAYERS = 5
TRADABLE_RETURN_BOUND = 0.5

CONFIGS = {
    "1_baseline": [],
    "2_winsor+std": ["winsorize", "standardize"],
    "3_mv_neutral": ["neutralize_market_cap"],
    "4_industry_neutral": ["neutralize_industry"],
    "5_mv+industry_neutral": ["neutralize_both"],
    "6_mv+industry+tradable": ["neutralize_both", "filter_tradable"],
}


def log(message: str) -> None:
    print(message, flush=True)


def load_px(cache_dir: Path, columns: list[str] | None = None, symbols: list[str] | None = None):
    """优先读 px_merged.parquet，缺失时回退 12 个分片；symbols 为 None 表示不过滤。"""
    cols = columns or ["ts_code", "trade_date", "pct_chg", "vol", "amount"]
    merged = cache_dir / "px_merged.parquet"
    if False and merged.exists():  # 回退：px 合并未提速，保留代码与文件但不走优先分支
        frame = pd.read_parquet(merged, columns=cols)
        if symbols is not None:
            frame = frame[frame["ts_code"].isin(symbols)]
        return frame
    frames = []
    for path in sorted(cache_dir.glob("px_*.parquet")):
        if path.name == "px_merged.parquet":
            continue
        frame = pd.read_parquet(path, columns=cols)
        if symbols is not None:
            frame = frame[frame["ts_code"].isin(symbols)]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)


def build_returns(factor: pd.DataFrame, chunk_symbols: int, horizon: int) -> pd.DataFrame:
    cal = trading_calendar()
    dates = sorted(factor["rebalance_date"].unique())
    tmap = forward_target(cal, dates, horizon)
    symbols = sorted(factor["symbol"].unique())
    rows = []
    t_px = time.time()
    px_all = load_px(CACHE, columns=["ts_code", "trade_date", "pct_chg"], symbols=symbols)
    px_all["trade_date"] = px_all["trade_date"].map(norm_date)
    print("[timing] build_returns px 一次性加载: {:.2f}s rows={:,}".format(
        time.time() - t_px, len(px_all)), flush=True)
    for start in range(0, len(symbols), chunk_symbols):
        batch = symbols[start:start + chunk_symbols]
        frame = px_all[px_all["ts_code"].isin(batch)]
        if not frame.empty:
            for code, group in frame.groupby("ts_code"):
                for T, ret in symbol_returns(group, tmap).items():
                    rows.append((T, code, ret))
        log("  收益推导批次 {}-{} 完成".format(start + 1, start + len(batch)))
    return pd.DataFrame(rows, columns=["rebalance_date", "symbol", "forward_return"])


def load_aux(symbols: list[str], rebalance_dates: set) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts = []
    for path in sorted(CACHE.glob("basic_*.parquet")):
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "total_mv"],
                                filters=[("ts_code", "in", symbols)])
        if frame.empty:
            continue
        text = frame["trade_date"].astype(str)
        frame["trade_date"] = text.str[:4] + "-" + text.str[4:6] + "-" + text.str[6:8]
        parts.append(frame[frame["trade_date"].isin(rebalance_dates)])
    mv = pd.concat(parts, ignore_index=True).rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"}) \
        if parts else pd.DataFrame(columns=["symbol", "rebalance_date", "total_mv"])
    industry = pd.read_parquet(CACHE / "stock_basic.parquet", columns=["ts_code", "industry"]) \
        .rename(columns={"ts_code": "symbol"})
    return mv, industry


def apply_pipeline(factor: pd.DataFrame, steps: list[str], ctx: dict) -> pd.DataFrame:
    mv, industry = ctx["mv"], ctx["industry"]
    frame = factor.copy()
    if ctx.get("asset") == "cb":
        # 可转债：不做市值/行业中性化（无对应口径）；可交易性 = 过去 20 交易日日均成交额 >= 门槛
        if "filter_tradable" in steps:
            frame = frame.merge(ctx["liq"], on=["symbol", "rebalance_date"], how="left")
            frame = frame[frame["avg_amount"].fillna(0.0) >= ctx["min_avg_amount"]].copy()
        return frame
    if "winsorize" in steps:
        frame["value"] = frame.groupby("rebalance_date")["value"].transform(lambda s: winsorize(s, method="mad", n=5))
    if "standardize" in steps:
        frame["value"] = frame.groupby("rebalance_date")["value"].transform(lambda s: standardize(s, method="rank"))
    if "neutralize_market_cap" in steps:
        frame = neutralize_market_cap(frame, mv)
    elif "neutralize_industry" in steps:
        frame = neutralize_industry(frame, industry)
    elif "neutralize_both" in steps:
        frame = neutralize_both(frame, mv, industry)
    if "filter_tradable" in steps:
        frame = filter_tradable(frame, ctx["px"], ctx["cal"], ctx["sb"],
                                min_avg_amount=ctx["min_avg_amount"],
                                max_suspend_days=ctx["max_suspend_days"],
                                lookback_days=ctx["horizon"])
        frame = frame[frame["tradable"]].copy()
    return frame


def evaluate(name: str, frame: pd.DataFrame, returns: pd.DataFrame) -> dict:
    """向量化评估：一次 groupby 完成所有月份的 RankIC 与分层收益（不再逐月循环）。"""
    merged = frame.merge(returns, on=["symbol", "rebalance_date"], how="inner")
    merged = merged[merged["value"].notna()].copy()
    if merged.empty:
        return {"config": name, "months": 0}
    grp = merged.groupby("rebalance_date")
    merged["cf"] = grp["value"].transform("count")
    merged["sf"] = grp["value"].transform("std")
    merged["rank_value"] = grp["value"].rank()
    merged["rank_ret"] = grp["forward_return"].rank()
    valid = merged[(merged["cf"] >= MIN_SYMBOLS) & (merged["sf"] > 0)].copy()
    if valid.empty:
        return {"config": name, "months": 0}

    ic_series = valid.groupby("rebalance_date").apply(
        lambda g: g["rank_value"].corr(g["rank_ret"]), include_groups=False).dropna()
    n_series = valid.groupby("rebalance_date").size()
    ic_series = ic_series[ic_series.index.isin(n_series.index)]

    def layer_of(s: pd.Series) -> pd.Series:
        try:
            return pd.qcut(s.rank(method="first"), LAYERS, labels=False)
        except ValueError:
            return pd.Series(np.nan, index=s.index)

    valid["layer"] = valid.groupby("rebalance_date")["value"].transform(layer_of)
    layer_ret = (valid.dropna(subset=["layer"])
                 .groupby(["rebalance_date", "layer"])["forward_return"].mean().unstack())

    ic = pd.DataFrame({"rebalance_date": ic_series.index, "rank_ic": ic_series.to_numpy()})
    ic["n"] = ic["rebalance_date"].map(n_series).to_numpy()
    for q in range(LAYERS):
        col = q if q in layer_ret.columns else None
        ic["q{}".format(q + 1)] = layer_ret[col].reindex(ic["rebalance_date"]).to_numpy() if col is not None else np.nan
    ic = ic.dropna(subset=["rank_ic"]).reset_index(drop=True)
    if ic.empty:
        return {"config": name, "months": 0}
    ic["q5_minus_q1"] = ic["q5"] - ic["q1"]
    m, s = ic["rank_ic"].mean(), ic["rank_ic"].std(ddof=1)
    return {"config": name, "months": len(ic), "n_avg": float(ic["n"].mean()),
            "ic_mean": float(m), "ic_std": float(s), "icir": float(m / s) if s else float("nan"),
            "t": float(m / (s / np.sqrt(len(ic)))) if s else float("nan"),
            "win": float((ic["rank_ic"] > 0).mean()),
            "q1": float(ic["q1"].mean()), "q5": float(ic["q5"].mean()),
            "q5_minus_q1": float(ic["q5_minus_q1"].mean()), "detail": ic}


def main() -> int:
    parser = argparse.ArgumentParser(description="带处理管道的 IC 分析")
    parser.add_argument("--input", default=str(OUT_DIR / "fscore.parquet"))
    parser.add_argument("--factor-col", default="f_score", help="因子值列名")
    parser.add_argument("--configs", default="all", help="all 或逗号分隔的配置名")
    parser.add_argument("--by-industry", action="store_true")
    parser.add_argument("--cache-returns", default=None)
    parser.add_argument("--chunk-symbols", type=int, default=500)
    parser.add_argument("--horizon", type=int, default=21)
    parser.add_argument("--output-configs", default=str(OUT_DIR / "fscore_ic_configs.parquet"))
    parser.add_argument("--min-avg-amount", type=float, default=5e7)
    parser.add_argument("--by-market-cap-groups", action="store_true")
    parser.add_argument("--ic-decay", action="store_true")
    parser.add_argument("--oos-split", default=None)
    parser.add_argument("--multi-cache", default=None)
    parser.add_argument("--audit-tradability", action="store_true")
    parser.add_argument("--max-suspend-days", type=int, default=5)
    parser.add_argument("--asset-class", choices=["stock", "cb"], default="stock",
                        help="stock=现有 A 股行为；cb=可转债（跳过市值/行业中性化，改用 cb_daily 可投池）")
    parser.add_argument("--cb-liq-file", default=str(CACHE / "cb_daily.parquet"),
                        help="asset-class=cb 的行情来源（ts_code/trade_date/amount/vol/pct_chg）")
    args = parser.parse_args()
    if args.asset_class == "cb":
        log("asset-class=cb：配置 3/4/5（市值/行业中性化）对可转债不适用，记为 NA")
        if args.by_market_cap_groups:
            log("asset-class=cb：--by-market-cap-groups 禁用（可转债无市值），已忽略")
            args.by_market_cap_groups = False

    t_start = time.time()
    factor = pd.read_parquet(Path(args.input), columns=["symbol", "rebalance_date", args.factor_col, "complete"])
    factor = factor[factor["complete"] & factor[args.factor_col].notna()][["symbol", "rebalance_date", args.factor_col]]
    factor = factor.rename(columns={args.factor_col: "value"})
    factor["value"] = factor["value"].astype(float)
    print("[timing] 读 factor 表: {:.2f}s".format(time.time() - t_start), flush=True)
    log("因子行数: {:,}  股票 {}  调仓日 {}".format(len(factor), factor["symbol"].nunique(),
                                                    factor["rebalance_date"].nunique()))

    cache_path = Path(args.cache_returns) if args.cache_returns else None
    if cache_path and cache_path.exists():
        returns = pd.read_parquet(cache_path)
        log("复用收益缓存: {} 行".format(len(returns)))
    else:
        returns = build_returns(factor, args.chunk_symbols, args.horizon)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            returns.to_parquet(cache_path, index=False)
            log("收益缓存已写出: {}".format(cache_path.name))
    print("[timing] 收益获取（缓存或推导）: {:.2f}s".format(time.time() - t_start), flush=True)
    log("收益样本: {:,} 行".format(len(returns)))

    symbols = sorted(factor["symbol"].unique())
    t_aux = time.time()
    mv, industry = load_aux(symbols, set(factor["rebalance_date"].unique()))
    industry_sb = pd.read_parquet(CACHE / "stock_basic.parquet", columns=["ts_code", "market"])
    print("[timing] load_aux(市值+行业+股票基础): {:.2f}s".format(time.time() - t_aux), flush=True)
    t_px = time.time()
    px = load_px(CACHE, columns=["ts_code", "trade_date", "pct_chg", "vol", "amount"], symbols=symbols)
    print("[timing] 加载 px: {:.2f}s rows={:,}".format(time.time() - t_px, len(px)), flush=True)
    liq = pd.DataFrame(columns=["symbol", "rebalance_date", "avg_amount"])
    if args.asset_class == "cb":
        cb = pd.read_parquet(args.cb_liq_file,
                             columns=["ts_code", "trade_date", "amount", "vol"])
        cb["trade_date"] = cb["trade_date"].map(norm_date)
        cb = cb.sort_values(["ts_code", "trade_date"])
        cb["avg_amount"] = cb.groupby("ts_code")["amount"].transform(
            lambda s: s.rolling(20, min_periods=1).mean()) * 1000.0   # 千元 -> 元
        liq = cb[cb["trade_date"].isin(set(factor["rebalance_date"]))][
            ["ts_code", "trade_date", "avg_amount"]].rename(
            columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
        log("可转债可投池口径：过去 20 交易日日均成交额 >= {:.0f} 万元（{} 行）".format(
            args.min_avg_amount / 1e4, len(liq)))
    text = px["trade_date"].astype(str)
    px["trade_date"] = text.str[:4] + "-" + text.str[4:6] + "-" + text.str[6:8]
    ctx = {"mv": mv, "industry": industry, "px": px, "cal": trading_calendar(), "sb": industry_sb,
           "min_avg_amount": args.min_avg_amount, "max_suspend_days": args.max_suspend_days,
           "horizon": args.horizon, "asset": args.asset_class, "liq": liq}

    t_cfg = time.time()
    names = list(CONFIGS) if args.configs == "all" else [n for n in args.configs.split(",") if n]
    summaries = []
    cb_skip = {"3_mv_neutral", "4_industry_neutral", "5_mv+industry_neutral"}
    for name in names:
        steps = CONFIGS.get(name)
        if steps is None:
            log("未知配置: {}".format(name))
            continue
        if args.asset_class == "cb" and name in cb_skip:
            log("  {:<26} 跳过（可转债无市值/行业中性化口径）".format(name))
            continue
        try:
            processed = apply_pipeline(factor, steps, ctx)
            result = evaluate(name, processed, returns)
        except Exception as exc:
            log("  {:<26} 失败，记为 NA：{}".format(name, str(exc)[:110]))
            continue
        summaries.append(result)
        log("  {:<26} 月数 {:>4}  IC {:+.4f}  ICIR {:+.3f}  t {:+.2f}  Q5-Q1 {:+.3%}".format(
            name, result["months"], result["ic_mean"], result["icir"], result["t"], result["q5_minus_q1"]))

    print("[timing] IC 计算({} 个配置): {:.2f}s".format(len(summaries), time.time() - t_cfg), flush=True)
    table = pd.DataFrame([{k: v for k, v in s.items() if k != "detail"} for s in summaries])
    table.to_parquet(args.output_configs, index=False)
    log("配置汇总已写出: {}".format(Path(args.output_configs).name))

    if args.by_industry:
        merged = factor.merge(returns, on=["symbol", "rebalance_date"], how="inner") \
            .merge(industry, on="symbol", how="left")
        merged["industry"] = merged["industry"].fillna("UNKNOWN")
        rows = []
        for (T, ind), group in merged.groupby(["rebalance_date", "industry"]):
            g = group[group["value"].notna()]
            if len(g) < MIN_INDUSTRY_SYMBOLS or g["value"].std() == 0:
                continue
            ic = g["value"].rank().corr(g["forward_return"].rank())
            if not pd.isna(ic):
                rows.append({"rebalance_date": T, "industry": ind, "n": len(g), "rank_ic": float(ic)})
        detail = pd.DataFrame(rows)
        agg = []
        for ind, group in detail.groupby("industry"):
            m, s = group["rank_ic"].mean(), group["rank_ic"].std(ddof=1)
            agg.append({"industry": ind, "months": len(group), "n_avg": float(group["n"].mean()),
                        "ic_mean": float(m), "ic_std": float(s),
                        "icir": float(m / s) if s else float("nan"),
                        "t": float(m / (s / np.sqrt(len(group)))) if s else float("nan"),
                        "win": float((group["rank_ic"] > 0).mean())})
        by_ind = pd.DataFrame(agg).sort_values("ic_mean", ascending=False)
        by_ind.to_parquet(OUT_DIR / "fscore_ic_by_industry.parquet", index=False)
        detail.to_parquet(OUT_DIR / "fscore_ic_by_industry_detail.parquet", index=False)
        log("")
        log("分行业 IC（{} 个行业）: 显著(|t|>2) {} 个，IC 为负 {} 个，IC 中位 {:.4f}，跨行业标准差 {:.4f}".format(
            len(by_ind), int((by_ind["t"].abs() > 2).sum()), int((by_ind["ic_mean"] < 0).sum()),
            by_ind["ic_mean"].median(), by_ind["ic_mean"].std(ddof=1)))
        log(by_ind.head(8).to_string(index=False))
        log("...")
        log(by_ind.tail(5).to_string(index=False))

    need_multi = bool(args.by_market_cap_groups or args.ic_decay or args.oos_split)
    if need_multi:
        cache_multi = Path(args.multi_cache) if args.multi_cache else None
        if cache_multi is not None and cache_multi.exists():
            multi = pd.read_parquet(cache_multi)
            log("复用多周期收益缓存: {} 行".format(len(multi)))
        else:
            multi = build_multi_returns(factor, args.chunk_symbols)
            if cache_multi is not None:
                multi.to_parquet(cache_multi, index=False)
                log("多周期收益缓存已写出: {}".format(cache_multi.name))
    if args.by_market_cap_groups:
        table_mv = analyse_mv_groups(factor, multi, mv)
        table_mv.to_parquet(OUT_DIR / "fscore_ic_by_mv_group.parquet", index=False)
        log("")
        log("分市值组 IC（组1=最小市值）:")
        log(table_mv.to_string(index=False))
    if args.ic_decay:
        table_decay = analyse_decay(factor, multi)
        table_decay.to_parquet(OUT_DIR / "fscore_ic_decay.parquet", index=False)
        log("")
        log("IC 衰减:")
        log(table_decay.to_string(index=False))
    if args.oos_split:
        table_oos = analyse_oos(factor, multi, args.oos_split)
        table_oos.to_parquet(OUT_DIR / "fscore_ic_oos.parquet", index=False)
        log("")
        log("样本内外对比（分割点 {}）:".format(args.oos_split))
        log(table_oos.to_string(index=False))
    if args.audit_tradability:
        t_audit = time.time()
        audit = tradability_audit(factor, ctx)
        print("[timing] 审计逻辑: {:.2f}s".format(time.time() - t_audit), flush=True)
        log("")
        log("可交易性审计: 总 {:,} 行，可交易 {:,}（{:.2%}）".format(len(audit), int(audit["tradable"].sum()), audit["tradable"].mean()))
        log(audit.groupby("market")["tradable"].agg(["mean", "size"]).to_string())
        reasons = Counter(r for v in audit.loc[~audit["tradable"], "drop_reason"] for r in str(v).split("|") if r)
        log("剔除原因 Top10: {}".format(dict(reasons.most_common(10))))
        log("ST 行数 {:,} | ST 涨停命中 {:,} | ST 跌停命中 {:,} | 默认阈值会漏判 {:,}".format(
            audit.attrs.get("st_rows", 0), audit.attrs.get("st_limit_up_hits", 0),
            audit.attrs.get("st_limit_down_hits", 0), audit.attrs.get("default_threshold_missed", 0)))
        audit.to_parquet(OUT_DIR / "fscore_tradability_audit.parquet", index=False)
        print("[timing] 审计落盘+总耗时: {:.2f}s".format(time.time() - t_start), flush=True)
    return 0



# ---------------- 扩展分析：多周期收益 + 分市值组 / IC 衰减 / 样本外 ----------------

DECAY_HORIZONS = (1, 5, 21, 63)
MV_GROUPS = 5


def build_multi_returns(factor: pd.DataFrame, chunk_symbols: int, horizons=DECAY_HORIZONS) -> pd.DataFrame:
    """一次扫描 px，同时推导多个下期窗口的收益，避免按周期重复读取。"""
    cal = trading_calendar()
    dates = sorted(factor["rebalance_date"].unique())
    tmaps = {h: forward_target(cal, dates, h) for h in horizons}
    symbols = sorted(factor["symbol"].unique())
    rows = []
    t_px = time.time()
    px_all = load_px(CACHE, columns=["ts_code", "trade_date", "pct_chg"], symbols=symbols)
    px_all["trade_date"] = px_all["trade_date"].map(norm_date)
    print("[timing] build_multi_returns px 一次性加载: {:.2f}s rows={:,}".format(
        time.time() - t_px, len(px_all)), flush=True)
    for start in range(0, len(symbols), chunk_symbols):
        batch = symbols[start:start + chunk_symbols]
        frame = px_all[px_all["ts_code"].isin(batch)]
        frame["trade_date"] = frame["trade_date"].map(norm_date)
        if not frame.empty:
            for code, group in frame.groupby("ts_code"):
                g = group.sort_values("trade_date")
                d = g["trade_date"].tolist()
                p = g["pct_chg"].to_numpy(dtype=float)
                ok = ~np.isnan(p)
                d = [x for x, keep in zip(d, ok) if keep]
                p = p[ok]
                if not d:
                    continue
                cum = np.cumprod(1.0 + p / 100.0)
                pos = {x: i for i, x in enumerate(d)}
                per_date = {}
                for h, tmap in tmaps.items():
                    for T, tgt in tmap.items():
                        i = pos.get(T)
                        if i is None:
                            continue
                        j = np.searchsorted(d, tgt, side="right") - 1
                        if j > i:
                            per_date.setdefault(T, {})[h] = float(cum[j] / cum[i] - 1.0)
                for T, values in per_date.items():
                    rows.append((T, code) + tuple(values.get(h, np.nan) for h in horizons))
        log("  多周期收益批次 {}-{} 完成".format(start + 1, start + len(batch)))
    columns = ["rebalance_date", "symbol"] + ["ret_{}".format(h) for h in horizons]
    return pd.DataFrame(rows, columns=columns)


def _ic_stats(frame: pd.DataFrame, value_col: str, ret_col: str) -> dict:
    rows = []
    for T, group in frame.groupby("rebalance_date"):
        g = group[[value_col, ret_col]].dropna()
        if len(g) < MIN_SYMBOLS or g[value_col].std() == 0:
            continue
        ic = g[value_col].rank().corr(g[ret_col].rank())
        if not pd.isna(ic):
            rows.append({"rebalance_date": T, "n": len(g), "rank_ic": float(ic)})
    ic = pd.DataFrame(rows)
    if ic.empty:
        return {"months": 0, "ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "t": np.nan, "win": np.nan}
    m, s = ic["rank_ic"].mean(), ic["rank_ic"].std(ddof=1)
    return {"months": len(ic), "n_avg": float(ic["n"].mean()), "ic_mean": float(m), "ic_std": float(s),
            "icir": float(m / s) if s else np.nan,
            "t": float(m / (s / np.sqrt(len(ic)))) if s else np.nan,
            "win": float((ic["rank_ic"] > 0).mean())}


def analyse_mv_groups(factor, multi, mv, groups=MV_GROUPS):
    merged = factor.merge(multi[["rebalance_date", "symbol", "ret_21"]], on=["symbol", "rebalance_date"], how="inner") \
        .merge(mv, on=["symbol", "rebalance_date"], how="inner")
    merged = merged[merged["total_mv"] > 0].copy()
    merged["log_mv"] = np.log(merged["total_mv"].astype(float))
    rows = []
    for T, group in merged.groupby("rebalance_date"):
        if len(group) < MIN_SYMBOLS * groups:
            continue
        try:
            label = pd.qcut(group["log_mv"].rank(method="first"), groups, labels=False)
        except ValueError:
            continue
        for gid in range(groups):
            sub = group[label == gid][["value", "ret_21"]].dropna()
            if len(sub) < MIN_SYMBOLS or sub["value"].std() == 0:
                continue
            ic = sub["value"].rank().corr(sub["ret_21"].rank())
            if not pd.isna(ic):
                rows.append({"rebalance_date": T, "group": gid, "n": len(sub), "rank_ic": float(ic)})
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail
    agg = []
    for gid, group in detail.groupby("group"):
        m, s = group["rank_ic"].mean(), group["rank_ic"].std(ddof=1)
        agg.append({"mv_group": int(gid) + 1, "months": len(group), "n_avg": float(group["n"].mean()),
                    "ic_mean": float(m), "ic_std": float(s),
                    "icir": float(m / s) if s else np.nan,
                    "t": float(m / (s / np.sqrt(len(group)))) if s else np.nan})
    return pd.DataFrame(agg)


def analyse_decay(factor, multi, horizons=DECAY_HORIZONS):
    rows = []
    for h in horizons:
        col = "ret_{}".format(h)
        if col not in multi.columns:
            continue
        merged = factor.merge(multi[["rebalance_date", "symbol", col]], on=["symbol", "rebalance_date"], how="inner")
        stats = _ic_stats(merged, "value", col)
        stats["horizon"] = h
        rows.append(stats)
    return pd.DataFrame(rows)[["horizon", "months", "ic_mean", "ic_std", "icir", "t", "win"]]


def analyse_oos(factor, multi, split: str):
    merged = factor.merge(multi[["rebalance_date", "symbol", "ret_21"]], on=["symbol", "rebalance_date"], how="inner")
    merged["segment"] = np.where(merged["rebalance_date"] < split, "in_sample", "out_of_sample")
    rows = []
    for name, group in merged.groupby("segment"):
        stats = _ic_stats(group, "value", "ret_21")
        stats["segment"] = name
        rows.append(stats)
    return pd.DataFrame(rows)[["segment", "months", "n_avg", "ic_mean", "ic_std", "icir", "t", "win"]]


def tradability_audit(panel, ctx):
    """向量化可交易性审计（含 ST 5% 分支）。"""
    from tradability import BOARD_LIMITS, future_windows
    cal, look = ctx["cal"], ctx["horizon"]
    dates = sorted(panel["rebalance_date"].unique())
    # 执行诊断必须向前看：T+1 是否涨停买不进 / 期末是否跌停卖不出。
    # 该窗口只用于诊断，不得用于筛 IC 样本（筛样本见 filter_tradable）。
    win = future_windows(cal, dates, look)
    keep_cols = [c for c in ("symbol", "rebalance_date", "value") if c in panel.columns]
    base = panel[keep_cols].copy()
    base["d1"] = base["rebalance_date"].map({T: v[0] for T, v in win.items()})
    base["d21"] = base["rebalance_date"].map({T: v[-1] for T, v in win.items()})

    px = ctx["px"].rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
    px = px[["symbol", "rebalance_date", "pct_chg", "vol", "amount"]]
    p1 = px.rename(columns={"rebalance_date": "d1", "pct_chg": "pct_1"})[["symbol", "d1", "pct_1"]]
    p21 = px.rename(columns={"rebalance_date": "d21", "pct_chg": "pct_21"})[["symbol", "d21", "pct_21"]]
    frame = base.merge(p1, on=["symbol", "d1"], how="left").merge(p21, on=["symbol", "d21"], how="left")

    date_to_T = {}
    for T in dates:
        for day in win.get(T, []):
            date_to_T[day] = T
    px2 = px[px["rebalance_date"].isin(date_to_T)].copy()
    px2["T"] = px2["rebalance_date"].map(date_to_T)
    px2["is_susp"] = (px2["vol"].isna()) | (px2["vol"] == 0)
    agg = px2.groupby(["symbol", "T"]).agg(avg_amount=("amount", "mean"), suspend=("is_susp", "sum"))
    agg = agg.reset_index().rename(columns={"T": "rebalance_date"})
    frame = frame.merge(agg, on=["symbol", "rebalance_date"], how="left")
    frame["avg_amount"] = frame["avg_amount"] * 1000.0
    frame["suspend"] = frame["suspend"].fillna(999)

    meta = ctx["sb"].rename(columns={"ts_code": "symbol"})[["symbol", "market"]]
    meta = meta.merge(ctx["industry"], on="symbol", how="left")
    frame = frame.merge(meta, on="symbol", how="left")
    frame["market"] = frame["market"].fillna("主板")
    frame["industry"] = frame["industry"].fillna("UNKNOWN")
    frame["board"] = frame["market"].map(
        lambda m: next((k for k in ("科创板", "创业板", "北交所") if k in str(m)), "主板"))

    import time
    frame["is_st"] = False
    mask_path = CACHE / "st_mask.parquet"
    t0 = time.time()
    if mask_path.exists():
        mask = pd.read_parquet(mask_path)
        print("[audit] 加载 st_mask: {:.2f}s shape={}".format(time.time() - t0, mask.shape), flush=True)
        t0 = time.time()
        pos_by_date = {str(i).replace("-", "")[:8]: k for k, i in enumerate(mask.index)}
        pos_by_sym = {str(c): k for k, c in enumerate(mask.columns)}
        di = frame["rebalance_date"].map(lambda d: pos_by_date.get(str(d).replace("-", "")[:8], -1)).to_numpy()
        sj = frame["symbol"].map(lambda s: pos_by_sym.get(str(s), -1)).to_numpy()
        valid = (di >= 0) & (sj >= 0)
        values = mask.values
        is_st = np.zeros(len(frame), dtype=bool)
        if valid.any():
            is_st[valid] = values[di[valid], sj[valid]]
        frame["is_st"] = is_st
        print("[audit] ST 判定: {:.2f}s 可查 {:,}/{:,} 行".format(time.time() - t0, int(valid.sum()), len(frame)), flush=True)
    else:
        print("[audit] 未找到 st_mask.parquet", flush=True)
    t0 = time.time()

    limits = dict(BOARD_LIMITS)
    th = frame["board"].map(limits).fillna(0.10)
    th = th.where(~frame["is_st"], 0.05)

    frame["limit_up_t1"] = (frame["pct_1"] / 100.0 - th).abs() <= 0.002
    frame["limit_down_t21"] = (frame["pct_21"] / 100.0 + th).abs() <= 0.002
    up5 = (frame["pct_1"] / 100.0 - 0.05).abs() <= 0.002
    up10 = (frame["pct_1"] / 100.0 - 0.10).abs() <= 0.002
    dn5 = (frame["pct_21"] / 100.0 + 0.05).abs() <= 0.002
    dn10 = (frame["pct_21"] / 100.0 + 0.10).abs() <= 0.002
    missed = frame["is_st"] & ((up5 & ~up10) | (dn5 & ~dn10))

    frame["illiquid"] = frame["avg_amount"].fillna(0) < ctx["min_avg_amount"]
    frame["over_suspend"] = frame["suspend"] > ctx["max_suspend_days"]
    frame["no_price"] = frame["pct_1"].isna() | frame["pct_21"].isna()
    parts = []
    for col, name in (("no_price", "NO_PRICE"), ("limit_up_t1", "LIMIT_UP_T1"),
                      ("limit_down_t21", "LIMIT_DOWN_T21"), ("over_suspend", "SUSPEND"),
                      ("illiquid", "ILLIQUID")):
        parts.append(frame[col].fillna(False).map({True: name, False: ""}))
    reason = parts[0]
    for extra in parts[1:]:
        reason = reason + np.where((reason != "") & (extra != ""), "|", "") + extra
    frame["drop_reason"] = reason
    frame["tradable"] = frame["drop_reason"] == ""
    frame.attrs["st_rows"] = int(frame["is_st"].sum())
    frame.attrs["st_limit_up_hits"] = int((frame["is_st"] & frame["limit_up_t1"]).sum())
    frame.attrs["st_limit_down_hits"] = int((frame["is_st"] & frame["limit_down_t21"]).sum())
    frame.attrs["default_threshold_missed"] = int(missed.sum())
    print("[audit] 其它审计: {:.2f}s".format(time.time() - t0), flush=True)
    return frame


if __name__ == "__main__":
    sys.exit(main())
