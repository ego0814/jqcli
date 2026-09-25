# -*- coding: utf-8 -*-
r"""ETF 池等权 · 季度再平衡信号脚本

策略（已通过六阶段的资产配置型策略）：
    7 只 ETF 等权，季度再平衡（1/4/7/10 月首个交易日），单只上限 20%。
    再平衡贡献 -0.18pp/年，几乎为 0 —— 即"长期持有 + 定期拉回等权"。

本脚本只产出**买卖清单 CSV**，不连接券商、不下单。

⚠️ 价格口径（已用 Tushare 不复权价逐只验证）：
    etf_daily.parquet 的 close **不是可成交价**，etf_extended_daily.parquet 的 close 是
    **前复权**价（qfq）。两者在 2026-09-01 的实测对比（Tushare fund_daily 不复权为基准）：
        510500 不复权 7.881 ｜ extended 7.8810 ✅ ｜ etf_daily 2.6803 ✗（×0.3401）
        513100 不复权 2.233 ｜ extended 2.2330 ✅ ｜ etf_daily 11.1692 ✗（×5.0019）
        512800 不复权 0.846 ｜ extended 0.8460 ✅ ｜ etf_daily 1.6911 ✗（×1.9989）
        511260 不复权 135.783｜ extended 134.5046（qfq）｜ etf_daily 140.0059 ✗
    因此本脚本从 extended 读价，并按 raw(t) = close(t) × adj_last / adj(t) 还原**可成交价**。
    在文件最后一个交易日上 adj(t) == adj_last，还原式退化为 raw = close（与 Tushare 完全一致）。

用法：
    python factor_lab/data/quarterly_etf_signal.py --capital 100000 --date 2026-09-30
    python factor_lab/data/quarterly_etf_signal.py --capital 100000 --current-holdings local/data/etf_holdings.csv
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT = LAB_DIR / "output" / "monthly_signals"

SRC_EXT = CACHE / "etf_extended_daily.parquet"
SRC_DAILY = CACHE / "etf_daily.parquet"

# (聚宽代码, 名称, Tushare 代码) —— 与 spec 7 只池一致
POOL = [
    ("510500.XSHG", "南方中证500ETF", "510500.SH"),
    ("512800.XSHG", "华宝中证银行ETF", "512800.SH"),
    ("513100.XSHG", "国泰纳斯达克100ETF", "513100.SH"),
    ("513050.XSHG", "易方达中证海外中国互联网50ETF", "513050.SH"),
    ("518880.XSHG", "华安易富黄金ETF", "518880.SH"),
    ("162411.XSHE", "华宝标普石油天然气上游股票指数(QDII-LOF)-A", "162411.SZ"),
    ("511260.XSHG", "国泰上证10年期国债ETF", "511260.SH"),
]
LOT = 100
MAX_WEIGHT = 0.20
TOP_QUANTILE_NOTE = "等权（1/N），非因子选股"


def log(m):
    print(m, flush=True)


def load_prices(as_of: str | None):
    """返回 (价格表 DataFrame index=code, 使用的交易日, 说明)。价格已还原为可成交价。"""
    ext = pd.read_parquet(SRC_EXT)
    ext["date"] = ext["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    codes = [c for c, _, _ in POOL]
    ext = ext[ext["code"].isin(codes)].copy()
    # 还原可成交价：raw = qfq × adj_last / adj(t)
    ext["adj_last"] = ext.groupby("code")["adj_factor"].transform("last")
    ext["raw_close"] = ext["close"] * ext["adj_last"] / ext["adj_factor"]

    # 选日期：≤ as_of 且 7 只齐全的最近交易日；as_of 为空则用文件最后一日
    wide = ext.pivot_table(index="date", columns="code", values="raw_close", aggfunc="last").sort_index()
    if as_of:
        cand = wide.index[wide.index <= as_of]
        if not len(cand):
            return None, None, "在 %s 之前没有任何交易日" % as_of
        wide = wide.loc[:as_of]
    full = wide.dropna(how="any")
    if full.empty:
        return None, None, "不存在 7 只齐全的交易日"
    day = full.index.max()
    px = full.loc[day]
    note = "已用 7 只全部有数据的最近交易日 %s" % day
    if as_of and day != as_of:
        note += "（请求 %s，该日数据尚未覆盖或非交易日）" % as_of
    return px, day, note


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 池等权季度再平衡信号")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--date", default=None, help="信号日 YYYY-MM-DD（默认取最新交易日）")
    ap.add_argument("--current-holdings", default=None, help="当前持仓 CSV（列 code,shares；省略=从零建仓）")
    ap.add_argument("--redistribute", action="store_true",
                    help="把整手取整后的剩余现金按比例补回其他标的（默认只报告偏差）")
    ap.add_argument("--out-dir", default=str(OUT))
    args = ap.parse_args()

    as_of = args.date.replace("-", "") if args.date else None
    px, day, note = load_prices(as_of)
    if px is None:
        log("ERROR: %s" % note)
        return 2
    log("信号日 = %s（%s）" % (day, note))

    codes = [c for c, _, _ in POOL]
    names = {c: n for c, n, _ in POOL}
    px = px.reindex(codes)
    if px.isna().any():
        log("ERROR: 以下标的缺价格：%s" % px[px.isna()].index.tolist())
        return 2

    # 当前持仓
    cur = {c: 0 for c in codes}
    if args.current_holdings:
        h = pd.read_csv(args.current_holdings, dtype={"code": str})
        h.columns = [c.strip().lower() for c in h.columns]
        if "code" not in h.columns or "shares" not in h.columns:
            log("ERROR: 持仓文件需要 code,shares 两列；实际 %s" % list(h.columns))
            return 2
        for r in h.itertuples():
            key = str(getattr(r, "code")).strip()
            if key in cur:
                cur[key] = int(float(getattr(r, "shares")))
        log("已读当前持仓：%s" % args.current_holdings)
    else:
        log("未提供当前持仓 —— 按**从零建仓**处理")

    # 目标权重：等权 + 20% 上限（等权下上限不触发，代码保留）
    n = len(codes)
    base = 1.0 / n
    target_w = {c: min(base, MAX_WEIGHT) for c in codes}
    excess = sum(base - target_w[c] for c in codes)
    if excess > 1e-12:
        free = [c for c in codes if target_w[c] < MAX_WEIGHT]
        for c in free:
            target_w[c] += excess / len(free)

    rows = []
    for c in codes:
        price = float(px[c])
        target_value = args.capital * target_w[c]
        tgt_shares = int(math.floor(target_value / price / LOT)) * LOT
        rows.append({"code": c, "name": names[c], "price": price,
                     "current_shares": cur[c], "target_shares": tgt_shares,
                     "delta_shares": tgt_shares - cur[c], "target_value": tgt_shares * price,
                     "target_weight": target_w[c]})

    if args.redistribute:
        # 把整手取整浪费的现金按"偏差最大优先"补回其他标的（不突破 20% 上限）
        cash_used = sum(r["target_value"] for r in rows)
        left = args.capital - cash_used
        changed = True
        while changed and left > 0:
            changed = False
            for r in sorted(rows, key=lambda x: x["target_value"] / args.capital):
                one_lot = r["price"] * LOT
                new_w = (r["target_value"] + one_lot) / args.capital
                if one_lot <= left + 1e-9 and new_w <= MAX_WEIGHT:
                    r["target_shares"] += LOT
                    r["target_value"] += one_lot
                    r["delta_shares"] = r["target_shares"] - r["current_shares"]
                    left -= one_lot
                    changed = True

    df = pd.DataFrame(rows)
    df["actual_weight"] = df["target_value"] / args.capital
    df["deviation_pp"] = (df["actual_weight"] - df["target_weight"]) * 100.0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / ("etf_pool_%s.csv" % day)
    cols = ["code", "name", "current_shares", "target_shares", "delta_shares",
            "price", "target_value", "target_weight"]
    out = df[cols].copy()
    out["price"] = out["price"].round(4)
    out["target_value"] = out["target_value"].round(2)
    out["target_weight"] = out["target_weight"].round(4)
    if dst.exists():
        log("ERROR: 已有同日信号，禁止覆盖：%s" % dst)
        return 2
    out.to_csv(dst, index=False, encoding="utf-8-sig")

    log("\n=== 逐标的（%s，本金 %.0f 元，%s）===" % (day, args.capital, TOP_QUANTILE_NOTE))
    log("%-14s %-34s %8s %10s %10s %10s %9s %9s %8s" % (
        "code", "name", "price", "cur_shr", "tgt_shr", "delta", "tgt_val", "tgt_w", "dev_pp"))
    for r in df.itertuples():
        log("%-14s %-34s %8.4f %10d %10d %10d %9.0f %8.2f%% %+8.2f" % (
            r.code, r.name[:32], r.price, r.current_shares, r.target_shares, r.delta_shares,
            r.target_value, r.target_weight * 100, r.deviation_pp))

    buy = df.loc[df["delta_shares"] > 0, "delta_shares"].mul(df["price"]).sum()
    sell = df.loc[df["delta_shares"] < 0, "delta_shares"].mul(df["price"]).sum()
    invested = df["target_value"].sum()
    log("\n总买入 = %+.0f 元；总卖出 = %.0f 元；持仓市值 = %.0f 元；剩余现金 = %.0f 元（%.2f%%）" % (
        buy, sell, invested, args.capital - invested,
        (args.capital - invested) / args.capital * 100))
    worst = df.loc[df["deviation_pp"].abs().idxmax()]
    log("最大权重偏差：%s %s %+.2fpp（目标 %.2f%%，实际 %.2f%%）" % (
        worst["code"], worst["name"][:20], worst["deviation_pp"],
        worst["target_weight"] * 100, worst["actual_weight"] * 100))
    gran = df[df["code"] == "511260.XSHG"]
    if len(gran):
        g = gran.iloc[0]
        log("511260 粒度检查：单价 %.4f 元，1 手 = %.0f 元；目标金额 %.0f 元 -> 只能买 %d 股（%.0f 元）"
            % (g["price"], g["price"] * LOT, args.capital * g["target_weight"],
               g["target_shares"], g["target_value"]))
        log("           权重 %.2f%% vs 目标 %.2f%%，偏差 %+.2fpp（阈值 10pp）" % (
            g["actual_weight"] * 100, g["target_weight"] * 100, g["deviation_pp"]))
    log("\n输出：%s" % dst)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
