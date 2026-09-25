# -*- coding: utf-8 -*-
r"""CB 信用排雷规则试算（离线，不接回测、不改策略）。

对指定调仓日，按 9 条信用规则逐条输出命中名单 + 三档组合，写出 Markdown 报告。

输入：
    cache/cb_daily.parquet              转债行情（确定当日在市样本）
    cache/cb_basic.parquet              转债基础信息（stk_code 正股映射）
    cache/cb_rating.parquet             主体评级历史（fetch_cb_rating.py 产出）
    cache/fin_income_*.parquet          净利润
    cache/fin_balancesheet_*.parquet    资产负债率（total_liab/total_assets）
    cache/fin_cashflow_*.parquet        经营现金流
    cache/st_mask.parquet               正股 ST 掩码（日 × 代码）
    cache/basic_*.parquet               正股日行情（daily_basic，含 close）
    cache/stock_basic.parquet           正股行业（用于区分金融）
    output/factor_values/cb_double_low_pit_soft.parquet + cb_liq_avg_amount_20d.parquet
                                        可投池（软过滤 + 20 日均额门槛）
    output/monthly_signals/<日期>.csv    当期目标持仓（若存在，用于标注是否已持仓）

输出：
    output/credit_rules/<日期>.md

用法：
    python analyze_cb_credit_rules.py                  # 默认最新交易日
    python analyze_cb_credit_rules.py --date 20260921
    python analyze_cb_credit_rules.py --min-amount 5000000
"""
from __future__ import annotations
import argparse
import glob
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
FV = LAB_DIR / "output" / "factor_values"
OUT_DIR = LAB_DIR / "output" / "credit_rules"

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB",
                "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C"]
RANK = {grade: i for i, grade in enumerate(RATING_ORDER)}
FIN_KEYWORDS = "银行|证券|保险|信托|多元金融|基金"
ANNUAL_YEARS = ("20251231", "20241231", "20231231")


def log(msg: str) -> None:
    print(msg, flush=True)


def norm(v) -> str:
    """统一成 YYYYMMDD。"""
    return str(v).replace("-", "").replace("/", "")[:8]


def load_fin(kind: str, asof: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(str(CACHE), kind + "_*.parquet")))
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    frame["end_date"] = frame["end_date"].astype(str)
    frame["ann_date"] = frame["ann_date"].astype(str)
    return frame[frame["ann_date"] <= asof]


def annual(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["end_date"].str.endswith("1231")]


def latest_annual(frame: pd.DataFrame, cols) -> pd.DataFrame:
    return (annual(frame).sort_values(["ts_code", "end_date"])
            .drop_duplicates("ts_code", keep="last")[["ts_code"] + list(cols)])


def neg_streak(frame: pd.DataFrame, value_col: str) -> pd.Series:
    piv = annual(frame).pivot_table(index="ts_code", columns="end_date",
                                    values=value_col, aggfunc="last")
    streak = pd.Series(0, index=piv.index, dtype=int)
    for year in [y for y in ANNUAL_YEARS if y in piv.columns]:
        streak = pd.Series(np.where((piv[year] < 0).fillna(False), streak + 1, 0),
                           index=piv.index)
    return streak


def load_price(asof: str) -> pd.Series:
    """正股最近可得 close（就近取当年/上年 daily_basic）。"""
    year = int(asof[:4])
    for y in (year, year - 1):
        path = CACHE / "basic_{}.parquet".format(y)
        if not path.exists():
            continue
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "close"])
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = frame[frame["trade_date"] <= asof]
        if len(frame):
            last = frame["trade_date"].max()
            return (frame[frame["trade_date"] == last]
                    .drop_duplicates("ts_code").set_index("ts_code")["close"])
    return pd.Series(dtype=float)


def main() -> int:
    ap = argparse.ArgumentParser(description="CB 信用排雷规则试算")
    ap.add_argument("--date", default=None, help="调仓日 YYYYMMDD（默认取 cb_daily 最新交易日）")
    ap.add_argument("--min-amount", type=float, default=5e6, help="可投池流动性门槛（默认 500 万）")
    args = ap.parse_args()

    daily = pd.read_parquet(CACHE / "cb_daily.parquet", columns=["ts_code", "trade_date"])
    daily["trade_date"] = daily["trade_date"].astype(str)
    asof = norm(args.date) if args.date else daily["trade_date"].max()
    dash = "{}-{}-{}".format(asof[:4], asof[4:6], asof[6:8])

    basic = pd.read_parquet(CACHE / "cb_basic.parquet")[
        ["ts_code", "bond_short_name", "stk_code", "stk_short_name", "remain_size"]]
    uni = daily[daily["trade_date"] == asof][["ts_code"]].merge(basic, on="ts_code", how="left")

    pool_syms = set()
    soft_path = FV / "cb_double_low_pit_soft.parquet"
    liq_path = FV / "cb_liq_avg_amount_20d.parquet"
    if soft_path.exists() and liq_path.exists():
        soft = pd.read_parquet(soft_path)
        liq = pd.read_parquet(liq_path)
        rd = [d for d in sorted(soft["rebalance_date"].astype(str).unique()) if norm(d) <= asof]
        if rd:
            rd = rd[-1]
            sub = liq[liq["rebalance_date"].astype(str) == rd]
            # 可投池 = 软过滤表内 ∩ 流动性达标（缺一不可，否则会把折价/低价债算进来）
            soft_syms = set(soft[soft["rebalance_date"].astype(str) == rd]["symbol"])
            pool_syms = set(sub[(sub["avg_amount"] >= args.min_amount)
                                & sub["symbol"].isin(soft_syms)]["symbol"])
            log("可投池口径：{}（软过滤 {} 只 ∩ 20 日均额 >= {:,.0f} -> {} 只）".format(
                rd, len(soft_syms), args.min_amount, len(pool_syms)))
    uni["in_pool"] = uni["ts_code"].isin(pool_syms)

    sig_path = LAB_DIR / "output" / "monthly_signals" / (dash + ".csv")
    targets = set()
    if sig_path.exists():
        targets = set(pd.read_csv(sig_path, dtype={"ts_code": str})["ts_code"])
        log("持仓清单：{}（{} 只）".format(sig_path.name, len(targets)))
    uni["in_target"] = uni["ts_code"].isin(targets)

    inc, bs, cf = (load_fin("fin_income", asof), load_fin("fin_balancesheet", asof),
                   load_fin("fin_cashflow", asof))
    fin = (latest_annual(bs, ["total_assets", "total_liab"]).set_index("ts_code")
           .join(latest_annual(inc, ["n_income"]).set_index("ts_code"), how="outer")
           .join(latest_annual(cf, ["n_cashflow_act"]).set_index("ts_code"), how="outer"))
    fin["debt_ratio"] = fin["total_liab"] / fin["total_assets"] * 100.0
    fin["neg_cf"] = neg_streak(cf, "n_cashflow_act")
    fin["neg_inc"] = neg_streak(inc, "n_income")

    st = pd.read_parquet(CACHE / "st_mask.parquet")
    st_days = sorted([d for d in st.index.astype(str) if d <= asof])
    st_true = set()
    if st_days:
        row = st.loc[st_days[-1]]
        st_true = set(row[row].index)
    price = load_price(asof)
    sb = pd.read_parquet(CACHE / "stock_basic.parquet")[["ts_code", "industry"]]
    industry = sb.drop_duplicates("ts_code").set_index("ts_code")["industry"]

    rating = pd.read_parquet(CACHE / "cb_rating.parquet")
    rating["ann_date"] = rating["ann_date"].astype(str)
    pit = rating[rating["ann_date"] <= asof].sort_values(["ts_code", "rating_date", "ann_date"])
    last = pit.groupby("ts_code").tail(1).set_index("ts_code")
    prev = pit.groupby("ts_code").nth(-2).set_index("ts_code") if len(pit) else pd.DataFrame()

    u = uni.merge(fin.reset_index(), left_on="stk_code", right_on="ts_code",
                  how="left", suffixes=("", "_f"))
    u["industry"] = u["stk_code"].map(industry)
    u["is_fin"] = u["industry"].fillna("").str.contains(FIN_KEYWORDS)
    u["st_flag"] = u["stk_code"].isin(st_true)
    u["stk_close"] = u["stk_code"].map(price)
    u["rating"] = u["ts_code"].map(last["rating"])
    u["rating_date"] = u["ts_code"].map(last["rating_date"])
    u["outlook"] = u["ts_code"].map(last["rating_outlook"])
    u["rating_prev"] = u["ts_code"].map(prev["rating"]) if len(prev) else np.nan
    u["rate_i"] = u["rating"].map(RANK)
    u["rate_prev_i"] = u["rating_prev"].map(RANK)
    # RANK 里 AAA=0（最好）、C=18（最差）：越差 index 越大
    # 下调 = 当前比前一次更差 -> rate_i > rate_prev_i（上调则相反）
    u["downgraded"] = u["rate_i"] > u["rate_prev_i"]

    rules = [
        ("R1 非金融+资产负债率>80%", (~u["is_fin"]) & (u["debt_ratio"] > 80)),
        ("R2 最近1年净利润为负", u["neg_inc"] >= 1),
        ("R2b 连续2年净利润为负", u["neg_inc"] >= 2),
        ("R3 连续2年经营现金流为负", u["neg_cf"] >= 2),
        ("R4 正股被 ST", u["st_flag"]),
        ("R5 正股价格<3元", u["stk_close"] < 3),
        ("R6b 评级<A (<=A-)", u["rate_i"] > RANK["A"]),
        ("R7 最近一次评级被下调", u["downgraded"]),
        ("R8 展望负面/列入观察", u["outlook"].astype(str).str.contains("负面|观察")),
    ]
    combos = [
        ("极轻：R4+评级<=BBB", u["st_flag"] | (u["rate_i"] >= RANK["BBB"])),
        ("轻：R1+R4+R6b+R3", (((~u["is_fin"]) & (u["debt_ratio"] > 80)) | u["st_flag"]
                             | (u["rate_i"] > RANK["A"]) | (u["neg_cf"] >= 2))),
        ("中等：轻+R2+R7", (((~u["is_fin"]) & (u["debt_ratio"] > 80)) | u["st_flag"]
                           | (u["rate_i"] > RANK["A"]) | (u["neg_cf"] >= 2)
                           | (u["neg_inc"] >= 1) | u["downgraded"])),
    ]
    for name, mask in rules + combos:
        u[name] = pd.Series(mask).fillna(False).values

    total = len(u)
    pool_n = int(u["in_pool"].sum())
    tgt_n = int(u["in_target"].sum())
    log("")
    log("=== CB 信用排雷规则试算 {} ===".format(dash))
    log("样本：当日在市 CB {} 只 | 可投池 {} 只 | 当期持仓 {} 只".format(total, pool_n, tgt_n))
    log("覆盖：正股映射 {} | 财务 {} | 评级 {} | 正股价格 {}".format(
        int(u["stk_code"].notna().sum()), int(u["debt_ratio"].notna().sum()),
        int(u["rating"].notna().sum()), int(u["stk_close"].notna().sum())))
    log("")
    log("{:<28}{:>14}{:>14}{:>14}".format("规则", "全市场", "可投池", "持仓"))
    summary = []
    for name, _ in rules:
        mask = u[name].values
        a, b, c = int(mask.sum()), int((mask & u["in_pool"].values).sum()), int((mask & u["in_target"].values).sum())
        summary.append((name, a, b, c))
        log("{:<28}{:>14}{:>14}{:>14}".format(
            name, "{} ({:.0f}%)".format(a, 100 * a / total),
            "{} ({:.0f}%)".format(b, 100 * b / max(pool_n, 1)),
            "{} ({:.0f}%)".format(c, 100 * c / max(tgt_n, 1))))
    log("")
    log("{:<28}{:>14}{:>14}{:>14}".format("组合", "全市场", "可投池", "持仓"))
    combo_summary = []
    for name, _ in combos:
        mask = u[name].values
        a, b, c = int(mask.sum()), int((mask & u["in_pool"].values).sum()), int((mask & u["in_target"].values).sum())
        combo_summary.append((name, a, b, c))
        log("{:<28}{:>14}{:>14}{:>14}".format(
            name, "{} ({:.0f}%)".format(a, 100 * a / total),
            "{} ({:.0f}%)".format(b, 100 * b / max(pool_n, 1)),
            "{} ({:.0f}%)".format(c, 100 * c / max(tgt_n, 1))))

    lines = ["# CB 信用排雷规则试算：{}".format(dash), "",
             "- 生成时间：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
             "- 样本：当日在市 CB **{}** 只；可投池 **{}** 只（软过滤 + 20 日均额 >= {:,.0f}）；当期持仓 **{}** 只".format(total, pool_n, args.min_amount, tgt_n),
             "- 数据覆盖：正股映射 {} / 财务 {} / 评级 {} / 正股价格 {}".format(
                 int(u["stk_code"].notna().sum()), int(u["debt_ratio"].notna().sum()),
                 int(u["rating"].notna().sum()), int(u["stk_close"].notna().sum())),
             "- PIT 口径：财务与评级均只使用 ann_date <= {}；ST 用最近可得日".format(asof),
             "- 说明：本脚本只做试算与出名单，**不接回测、不改策略代码**", "",
             "## 规则命中汇总", "", "| 规则 | 全市场 | 可投池 | 持仓 |", "|---|---|---|---|"]
    for name, a, b, c in summary:
        lines.append("| {} | {} ({:.0f}%) | {} ({:.0f}%) | {} ({:.0f}%) |".format(
            name, a, 100 * a / total, b, 100 * b / max(pool_n, 1), c, 100 * c / max(tgt_n, 1)))
    lines += ["", "## 三档组合", "", "| 组合 | 全市场 | 可投池 | 持仓 |", "|---|---|---|---|"]
    for name, a, b, c in combo_summary:
        lines.append("| {} | {} ({:.0f}%) | {} ({:.0f}%) | {} ({:.0f}%) |".format(
            name, a, 100 * a / total, b, 100 * b / max(pool_n, 1), c, 100 * c / max(tgt_n, 1)))
    lines += ["", "## 各规则命中名单", ""]
    show_cols = ["ts_code", "bond_short_name", "stk_code", "stk_short_name", "rating",
                 "rating_prev", "debt_ratio", "neg_inc", "neg_cf", "st_flag", "stk_close",
                 "in_pool", "in_target"]
    for name, _ in rules:
        hit = u[u[name]].sort_values("rate_i", ascending=False, na_position="last")
        lines += ["### {}（{} 只）".format(name, len(hit)), ""]
        if hit.empty:
            lines += ["（无命中）", ""]
            continue
        lines += ["| 转债 | 名称 | 正股 | 正股名 | 评级 | 前值 | 负债率% | 连亏年 | 负现金流年 | ST | 正股价 | 可投池 | 持仓 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, row in hit[show_cols].iterrows():
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row["ts_code"], row["bond_short_name"], row["stk_code"], row["stk_short_name"],
                row["rating"], row["rating_prev"],
                "" if pd.isna(row["debt_ratio"]) else round(row["debt_ratio"], 1),
                "" if pd.isna(row["neg_inc"]) else int(row["neg_inc"]),
                "" if pd.isna(row["neg_cf"]) else int(row["neg_cf"]),
                "是" if row["st_flag"] else "",
                "" if pd.isna(row["stk_close"]) else row["stk_close"],
                "是" if row["in_pool"] else "", "是" if row["in_target"] else ""))
        lines.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / (dash + ".md")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log("")
    log("已写出：{}".format(out_path))
    log("")
    log("=== 各规则命中前 5 只 ===")
    for name, _ in rules:
        hit = u[u[name]].sort_values("rate_i", ascending=False, na_position="last").head(5)
        names = ["{}|{}".format(r["ts_code"], r["stk_short_name"]) for _, r in hit.iterrows()]
        log("{:<28} {}".format(name, ", ".join(names) if names else "（无）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
