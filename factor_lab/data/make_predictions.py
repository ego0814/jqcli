# -*- coding: utf-8 -*-
r"""把因子表导出为聚宽可读的预测值表 CSV。

输出列：date, symbol, value
  - date 为调仓日（YYYY-MM-DD）
  - symbol 转为聚宽格式（.SZ→.XSHE、.SH→.XSHG、.BJ→.XBEI）
  - value 为该截面内 rank 标准化到 [0,1] 的因子值（越大越好）

默认对因子做市值 + 行业中性化：复用 analysis/neutralize.py 的 neutralize_both，
用横截面回归残差代替原始因子值再做 rank，避免组合收益被行业/市值暴露主导。
关闭中性化用 --no-neutralize（用于中性化前后的对照）。

用法：
    python make_predictions.py [--input ...ep.parquet] [--output ...predictions_ep_v1.csv]
                              [--factor-col ep_value] [--no-neutralize]
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
OUT_DIR = LAB_DIR / "output"
CACHE = DATA_DIR / "cache"
ANALYSIS = LAB_DIR / "analysis"
sys.path.insert(0, str(ANALYSIS))

from neutralize import neutralize_both  # noqa: E402

SUFFIX = {".SZ": ".XSHE", ".SH": ".XSHG", ".BJ": ".XBEI"}


def to_jq_symbol(code: str) -> str:
    text = str(code)
    for tushare, jq in SUFFIX.items():
        if text.endswith(tushare):
            return text[: -len(tushare)] + jq
    return text


def load_mv_industry(symbols: list[str], rebalance_dates: set) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读取 total_mv 与 industry，口径与 compute_ic_processed.load_aux 完全一致。"""
    parts = []
    for path in sorted(CACHE.glob("basic_*.parquet")):
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "total_mv"],
                                filters=[("ts_code", "in", symbols)])
        if frame.empty:
            continue
        text = frame["trade_date"].astype(str)
        frame["trade_date"] = text.str[:4] + "-" + text.str[4:6] + "-" + text.str[6:8]
        parts.append(frame[frame["trade_date"].isin(rebalance_dates)])
    mv = pd.concat(parts, ignore_index=True).rename(
        columns={"ts_code": "symbol", "trade_date": "rebalance_date"}) \
        if parts else pd.DataFrame(columns=["symbol", "rebalance_date", "total_mv"])
    industry = pd.read_parquet(CACHE / "stock_basic.parquet", columns=["ts_code", "industry"]) \
        .rename(columns={"ts_code": "symbol"})
    return mv, industry


def neutralize_values(df: pd.DataFrame, factor_col: str) -> pd.DataFrame:
    """对 factor_col 做市值 + 行业中性化，返回带 value=残差 的 (symbol, rebalance_date, value)。"""
    symbols = sorted(df["symbol"].unique())
    rebalance_dates = set(df["rebalance_date"].unique())
    mv, industry = load_mv_industry(symbols, rebalance_dates)
    print("[predictions] 辅助数据: mv={:,} 行（去重后 {:,} 个 (symbol, date)）industry={:,} 行".format(
        len(mv), mv.drop_duplicates(subset=["symbol", "rebalance_date"]).shape[0], len(industry)), flush=True)
    factor = df.rename(columns={factor_col: "value"})[["symbol", "rebalance_date", "value"]]
    before = len(factor)
    _keys = mv[["symbol", "rebalance_date"]].drop_duplicates()
    _miss = len(factor.merge(_keys, on=["symbol", "rebalance_date"], how="left", indicator=True)
                 .query("_merge == 'left_only'"))
    _dups = int(factor.duplicated(subset=["symbol", "rebalance_date"], keep=False).sum())
    print("[predictions] 上游诊断: 缺 total_mv 的因子行={:,}；重复 (symbol, date) 的因子行={:,}".format(
        _miss, _dups), flush=True)
    if _dups:
        _dup_dates = sorted(factor.loc[factor.duplicated(subset=["symbol", "rebalance_date"], keep=False),
                                       "rebalance_date"].unique())
        print("[predictions] 含重复键的调仓日: {} 个，范围 {} ~ {}".format(
            len(_dup_dates), _dup_dates[0], _dup_dates[-1]), flush=True)
    out = neutralize_both(factor, mv, industry)
    print("[predictions] 中性化: {:,} -> {:,} 行（-{:,} = 缺 mv {:,} + 重复键去重 {:,}）".format(
        before, len(out), before - len(out), _miss, _dups // 2), flush=True)
    fallback = out["fallback"].fillna("")
    if (fallback != "").any():
        print("[predictions] 中性化 fallback 行数分布: {}".format(
            dict(fallback[fallback != ""].value_counts())), flush=True)
    resid = out["value"].astype(float)
    print("[predictions] 残差分布: min={:.6f} p1={:.6f} 中位={:.6f} p99={:.6f} max={:.6f} std={:.6f}".format(
        resid.min(), resid.quantile(0.01), resid.median(), resid.quantile(0.99), resid.max(), resid.std()), flush=True)
    return out[["symbol", "rebalance_date", "value"]].copy()


def main() -> int:
    ap = argparse.ArgumentParser(description="生成预测值表")
    ap.add_argument("--input", default=str(OUT_DIR / "factor_values" / "ep.parquet"))
    ap.add_argument("--output", default=str(OUT_DIR / "predictions" / "predictions_ep_v1.csv"))
    ap.add_argument("--factor-col", default="ep_value")
    ap.add_argument("--no-neutralize", action="store_true",
                    help="跳过市值+行业中性化，直接用原始因子值做 rank")
    args = ap.parse_args()

    df = pd.read_parquet(Path(args.input), columns=["symbol", "rebalance_date", args.factor_col, "complete"])
    df = df[df["complete"] & df[args.factor_col].notna()].copy()
    print("[predictions] 输入 complete 行: {:,}".format(len(df)), flush=True)
    _before = len(df)
    df = df[~df["symbol"].str.endswith(".BJ")].copy()
    print("[predictions] 剔除北交所(.BJ): {:,} -> {:,}（-{:,}）".format(_before, len(df), _before - len(df)), flush=True)
    if args.no_neutralize:
        print("[predictions] 中性化：关闭（原始因子值）", flush=True)
        work = df.rename(columns={args.factor_col: "value"})[["symbol", "rebalance_date", "value"]]
    else:
        print("[predictions] 中性化：市值 + 行业（neutralize_both）", flush=True)
        work = neutralize_values(df, args.factor_col)
    work["value"] = work.groupby("rebalance_date")["value"].rank(pct=True)
    out = pd.DataFrame({
        "date": work["rebalance_date"],
        "symbol": work["symbol"].map(to_jq_symbol),
        "value": work["value"].round(6),
    }).sort_values(["date", "value"], ascending=[True, False])
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False, encoding="utf-8")
    print("输出: {} rows={:,} 唯一date={:,} 唯一symbol={:,} bytes={:,}".format(
        dst.name, len(out), out["date"].nunique(), out["symbol"].nunique(), dst.stat().st_size))
    print("date 范围: {} ~ {}".format(out["date"].min(), out["date"].max()))
    print("前 5 行:")
    print(out.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
