# -*- coding: utf-8 -*-
r"""分位-超额扫描：把因子按分位（以及固定只数）分组，计算相对全市场等权的超额。

用途：在把因子接成策略之前，先确认"策略实际会持有的那个分位"历史上是否有正超额。
IC/ICIR 高不等于极端分位有 alpha（EP 策略 v1/v2 的教训）。

输出：
    - 控制台分位表
    - CSV：<因子表名>_quantile_alpha.csv

用法：
    python quantile_alpha_scan.py --input ...reversal.parquet --factor-col reversal_value --cache-returns ...ic_returns_cache.parquet
    python quantile_alpha_scan.py --input ...predictions_ep_v1.csv --factor-col value --cache-returns ...ep_returns_63d.parquet --jq-symbol
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
OUT_DIR = LAB_DIR / "output" / "factor_values"
CACHE = DATA_DIR / "cache"

DEFAULT_QUANTILES = (0.01, 0.02, 0.05, 0.10, 0.20, 0.40)
DEFAULT_TOP_COUNTS = (10, 20, 30, 50)
JQ_SUFFIX = {".XSHE": ".SZ", ".XSHG": ".SH", ".XBEI": ".BJ"}
DEFAULT_MIN_AMOUNT = 5e7      # 5000 万元
DEFAULT_LOOKBACK_DAYS = 20    # 日均成交额窗口（交易日）


def log(message: str) -> None:
    print(message, flush=True)


def load_factor(path: Path, factor_col: str, jq_symbol: bool) -> tuple[pd.DataFrame, int, int]:
    """读因子表，返回 (symbol, rebalance_date, value)、去重行数、原始行数。"""
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype={"date": str, "symbol": str})
        frame = frame.rename(columns={"date": "rebalance_date", "value": factor_col})
    else:
        frame = pd.read_parquet(path, columns=["symbol", "rebalance_date", factor_col])
    frame = frame.rename(columns={factor_col: "value"})[["symbol", "rebalance_date", "value"]]
    frame["symbol"] = frame["symbol"].astype(str)
    if jq_symbol or frame["symbol"].str.endswith((".XSHE", ".XSHG", ".XBEI")).any():
        frame["symbol"] = frame["symbol"].str.replace(
            r"\.(XSHE|XSHG|XBEI)$", lambda m: JQ_SUFFIX[m.group(0)], regex=True)
    frame["rebalance_date"] = frame["rebalance_date"].astype(str)
    raw_rows = len(frame)
    frame = frame[frame["value"].notna()].copy()
    frame["value"] = frame["value"].astype(float)
    frame = frame[frame["value"].notna()]
    deduped = frame.drop_duplicates(subset=["symbol", "rebalance_date"], keep="last")
    dup_rows = len(frame) - len(deduped)
    return deduped.reset_index(drop=True), dup_rows, raw_rows


def periods_per_year(dates) -> tuple[float, float]:
    """由调仓日的中位**日历**间隔推断每年调仓期数（月频≈12，季频≈4）。"""
    stamps = pd.to_datetime(sorted(set(dates)))
    if len(stamps) < 2:
        return 12.0, float("nan")
    gaps = np.diff(stamps.values).astype("timedelta64[D]").astype(float)
    median_gap = float(np.median(gaps))
    return (365.25 / median_gap if median_gap > 0 else 12.0), median_gap


def summarize(excess: pd.Series, group_return: pd.Series, ppy: float) -> dict:
    excess = excess.dropna()
    group_return = group_return.dropna()
    n = len(excess)
    if n == 0:
        return {"期数": 0, "组合期均收益": float("nan"), "期均超额": float("nan"),
                "年化超额": float("nan"), "t": float("nan"), "胜率": float("nan")}
    mean_excess = float(excess.mean())
    std_excess = float(excess.std(ddof=1)) if n > 1 else float("nan")
    t_stat = mean_excess / std_excess * np.sqrt(n) if std_excess and std_excess > 0 else float("nan")
    return {
        "期数": n,
        "组合期均收益": float(group_return.mean()),
        "期均超额": mean_excess,
        "年化超额": (1.0 + mean_excess) ** ppy - 1.0,
        "t": t_stat,
        "胜率": float((excess > 0).mean()),
    }


def build_liquidity_table(dates: set, lookback: int, cache_path: Path,
                          source_file: Path | None = None) -> pd.DataFrame:
    """算每个调仓日"过去 lookback 个交易日日均成交额"（元）。

    数据源：默认 factor_lab/data/cache/px_*.parquet（A 股）；
    source_file 给定时改读该 parquet（需含 ts_code / trade_date / amount），
    例如可转债行情 cache/cb_daily.parquet。tushare amount 单位均为千元。
    返回 (symbol, rebalance_date, avg_amount)；结果缓存到 cache_path，
    缓存已覆盖所需调仓日时直接复用。
    """
    if cache_path.exists():
        cached = pd.read_parquet(cache_path)
        if set(cached["rebalance_date"].astype(str).unique()) >= set(dates):
            log("复用日均成交额缓存: {}（{} 行）".format(cache_path.name, len(cached)))
            return cached
    source = "{}".format(source_file.name) if source_file else "cache/px_*.parquet（A 股）"
    log("构建日均成交额缓存（lookback={} 交易日，调仓日 {} 个，数据源 {}）…".format(lookback, len(dates), source))
    # 若给定的流动性文件本身就是"预计算好的日均成交额"，直接使用
    if source_file is not None:
        import pyarrow.parquet as pq
        cols = pq.read_schema(source_file).names
        if "avg_amount" in cols:
            table = pd.read_parquet(source_file)
            table = table.rename(columns={"symbol": "symbol", "ts_code": "symbol"})
            table["rebalance_date"] = table["rebalance_date"].astype(str)
            table = table[table["rebalance_date"].isin(dates)]
            log("{} 已含 avg_amount，直接作为流动性口径使用（{} 行）".format(
                source_file.name, len(table)))
            return table[["symbol", "rebalance_date", "avg_amount"]]
    parts, tail = [], None
    if source_file is not None:
        paths = [source_file]
    else:
        paths = [p for p in sorted(CACHE.glob("px_*.parquet")) if p.name != "px_merged.parquet"]
    for path in paths:
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "amount"])
        if frame.empty:
            continue
        if tail is not None and not tail.empty:
            frame = pd.concat([tail, frame], ignore_index=True)
        digits = frame["trade_date"].astype(str).str.replace(r"\D", "", regex=True)
        frame["trade_date"] = digits.str[:4] + "-" + digits.str[4:6] + "-" + digits.str[6:8]
        frame = frame.sort_values(["ts_code", "trade_date"])
        frame["avg_amount"] = frame.groupby("ts_code")["amount"].transform(
            lambda s: s.rolling(lookback, min_periods=1).mean())
        keep = frame[frame["trade_date"].isin(dates)]
        if not keep.empty:
            parts.append(keep[["ts_code", "trade_date", "avg_amount"]])
        tail = frame.groupby("ts_code").tail(lookback)[["ts_code", "trade_date", "amount"]]
        log("  已处理 {}".format(path.name))
    if not parts:
        return pd.DataFrame(columns=["symbol", "rebalance_date", "avg_amount"])
    table = pd.concat(parts, ignore_index=True)
    table["avg_amount"] = table["avg_amount"] * 1000.0   # 千元 -> 元
    table = table.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
    table = table.drop_duplicates(subset=["symbol", "rebalance_date"], keep="last")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(cache_path, index=False)
    log("日均成交额缓存已写出: {}（{} 行）".format(cache_path.name, len(table)))
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description="分位-超额扫描")
    ap.add_argument("--input", default=str(OUT_DIR / "reversal.parquet"))
    ap.add_argument("--factor-col", default="reversal_value")
    ap.add_argument("--cache-returns", default=str(OUT_DIR / "ic_returns_cache.parquet"))
    ap.add_argument("--output", default=None, help="CSV 输出路径，默认 <因子表名>_quantile_alpha.csv")
    ap.add_argument("--quantiles", default=",".join(str(q) for q in DEFAULT_QUANTILES))
    ap.add_argument("--top-counts", default=",".join(str(k) for k in DEFAULT_TOP_COUNTS))
    ap.add_argument("--jq-symbol", action="store_true", help="把 .XSHE/.XSHG/.XBEI 转成 .SZ/.SH/.BJ 后再与收益合并")
    ap.add_argument("--return-col", default="forward_return")
    ap.add_argument("--pre-filter", action="store_true",
                    help="先过滤后选：先在可投池内筛掉不达门槛的标的，再在池内排序取 TopN；基准改为可投池等权")
    ap.add_argument("--min-amount", type=float, default=DEFAULT_MIN_AMOUNT,
                    help="流动性门槛（元），默认 5000 万")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help="日均成交额窗口（交易日），默认 20")
    ap.add_argument("--liq-cache", default=None,
                    help="日均成交额缓存路径，默认 output/factor_values/liq_avg_amount_<lookback>d.parquet")
    ap.add_argument("--liq-file", default=None,
                    help="自定义流动性来源 parquet（含 ts_code/trade_date/amount），默认用 cache/px_*.parquet")
    ap.add_argument("--ascending", action="store_true",
                    help="因子值越小越好（如可转债双低值）；默认越大越好")
    ap.add_argument("--annualize-periods", type=float, default=None,
                    help="年化折算期数；默认按调仓日中位间隔推断。收益口径长度与调仓间隔不一致时（如 63 日收益 + 月频调仓）必须显式指定 365.25/63")
    args = ap.parse_args()

    factor_path = Path(args.input)
    factor, dup_rows, raw_rows = load_factor(factor_path, args.factor_col, args.jq_symbol)
    returns = pd.read_parquet(Path(args.cache_returns), columns=["rebalance_date", "symbol", args.return_col])
    returns["rebalance_date"] = returns["rebalance_date"].astype(str)
    log("因子表: {} 行={:,}（读入后去重 {:,} 行）股票={:,} 调仓日={:,}".format(
        factor_path.name, raw_rows, dup_rows, factor["symbol"].nunique(), factor["rebalance_date"].nunique()))
    log("收益缓存: {} 行={:,} 调仓日={:,}".format(
        Path(args.cache_returns).name, len(returns), returns["rebalance_date"].nunique()))

    merged = factor.merge(returns, on=["rebalance_date", "symbol"], how="inner")
    if merged.empty:
        log("合并结果为空：检查 symbol 格式（.SZ/.SH vs .XSHE/.XSHG）或调仓日是否一致")
        return 1
    log("合并后: 行={:,} 调仓日={:,}（{} ~ {}）".format(
        len(merged), merged["rebalance_date"].nunique(),
        merged["rebalance_date"].min(), merged["rebalance_date"].max()))

    ascending = bool(args.ascending)
    market_all = merged.groupby("rebalance_date")[args.return_col].mean()
    if args.pre_filter:
        cache_path = Path(args.liq_cache) if args.liq_cache else \
            OUT_DIR / "liq_avg_amount_{}d_{}.parquet".format(
                args.lookback_days,
                Path(args.liq_file).stem if args.liq_file else "stock")
        source_file = Path(args.liq_file) if args.liq_file else None
        liq = build_liquidity_table(set(merged["rebalance_date"].unique()),
                                    args.lookback_days, cache_path, source_file)
        merged = merged.merge(liq, on=["rebalance_date", "symbol"], how="left")
        before = len(merged)
        merged = merged[merged["avg_amount"].fillna(0.0) >= args.min_amount].copy()
        log("先过滤后选：门槛 {:.0f} 万元，口径 = 过去 {} 交易日日均成交额".format(
            args.min_amount / 1e4, args.lookback_days))
        log("过滤前 {:,} 行 -> 可投池 {:,} 行（剔除 {:.1%}）；基准 = 可投池等权".format(
            before, len(merged), 1.0 - len(merged) / before if before else 0.0))
        if merged.empty:
            log("可投池为空：门槛过高或成交额数据缺失")
            return 1
        pool_sizes = merged.groupby("rebalance_date")[args.return_col].size()
        log("可投池规模: 均值 {:.0f} 只/期（最小 {} / 最大 {}）".format(
            pool_sizes.mean(), pool_sizes.min(), pool_sizes.max()))
        log("参考：全市场等权期均收益 {:.4f}，可投池等权期均收益 {:.4f}".format(
            market_all.mean(), merged.groupby("rebalance_date")[args.return_col].mean().mean()))

    market = merged.groupby("rebalance_date")[args.return_col].mean()
    sizes = merged.groupby("rebalance_date")[args.return_col].size()
    # 方向：默认因子值越大越好；--ascending 时越小越好（如双低值）
    merged["pct_rank"] = merged.groupby("rebalance_date")["value"].rank(pct=True, ascending=not ascending)
    merged["rank_desc"] = merged.groupby("rebalance_date")["value"].rank(ascending=ascending, method="first")
    log("因子方向: {}".format("越小越好（--ascending）" if ascending else "越大越好（默认）"))

    ppy, median_gap = periods_per_year(merged["rebalance_date"])
    if args.annualize_periods:
        ppy = float(args.annualize_periods)
        log("调仓频率: 中位间隔 {:.1f} 天；年化折算按参数指定 {:.2f} 期/年".format(median_gap, ppy))
    else:
        log("调仓频率: 中位间隔 {:.1f} 天 -> 年化折算 {:.2f} 期/年".format(median_gap, ppy))
    log("每期股票数: 均值 {:.0f}（最小 {} / 最大 {}）".format(sizes.mean(), sizes.min(), sizes.max()))

    rows = []
    for q in [float(x) for x in args.quantiles.split(",") if x.strip()]:
        sel = merged[merged["pct_rank"] > 1.0 - q]
        if sel.empty:
            continue
        group_ret = sel.groupby("rebalance_date")[args.return_col].mean().reindex(market.index)
        stats = summarize(group_ret - market, group_ret, ppy)
        stats.update({"分组": "Top {:.0%}".format(q), "每期只数": len(sel) / sel["rebalance_date"].nunique()})
        rows.append(stats)
    for k in [int(x) for x in args.top_counts.split(",") if x.strip()]:
        sel = merged[merged["rank_desc"] <= k]
        if sel.empty:
            continue
        group_ret = sel.groupby("rebalance_date")[args.return_col].mean().reindex(market.index)
        stats = summarize(group_ret - market, group_ret, ppy)
        stats.update({"分组": "Top {} 只".format(k), "每期只数": len(sel) / sel["rebalance_date"].nunique()})
        rows.append(stats)
    bottom = merged[merged["pct_rank"] <= 0.2]
    group_ret = bottom.groupby("rebalance_date")[args.return_col].mean().reindex(market.index)
    stats = summarize(group_ret - market, group_ret, ppy)
    stats.update({"分组": "Bottom 20%", "每期只数": len(bottom) / bottom["rebalance_date"].nunique()})
    rows.append(stats)
    stats = summarize(market - market, market, ppy)
    stats.update({"分组": "全市场等权（基准）", "每期只数": float(sizes.mean())})
    rows.append(stats)

    table = pd.DataFrame(rows)[["分组", "每期只数", "组合期均收益", "期均超额", "年化超额", "t", "胜率", "期数"]]
    pretty = table.copy()
    pretty["每期只数"] = pretty["每期只数"].map(lambda v: "{:.0f}".format(v))
    for col in ("组合期均收益", "期均超额", "年化超额"):
        pretty[col] = pretty[col].map(lambda v: "{:+.2%}".format(v) if pd.notna(v) else "NA")
    pretty["t"] = pretty["t"].map(lambda v: "{:+.2f}".format(v) if pd.notna(v) else "NA")
    pretty["胜率"] = pretty["胜率"].map(lambda v: "{:.1%}".format(v) if pd.notna(v) else "NA")
    log("")
    log("=== {} / {} 分位-超额扫描（收益口径 {}，{:.2f} 期/年） ===".format(
        factor_path.name, args.factor_col, Path(args.cache_returns).name, ppy))
    log(pretty.to_string(index=False))

    dst = Path(args.output) if args.output else OUT_DIR / "{}_quantile_alpha.csv".format(factor_path.stem)
    table.to_csv(dst, index=False, encoding="utf-8-sig")
    log("")
    log("CSV 输出: {} 行={}".format(dst, len(table)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
