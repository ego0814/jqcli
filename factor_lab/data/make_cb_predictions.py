# -*- coding: utf-8 -*-
r"""生成 CB 双低策略的预测值表（供聚宽策略 read_file 使用）。

输出：output/predictions/cb_predictions_v1.csv
列：
    date            调仓日（YYYY-MM-DD）
    symbol          聚宽格式转债代码（113050.XSHG / 128044.XSHE）
    double_low_value 双低值（越小越好）
    premium_rate    转股溢价率（%，聚宽当日值）
    cb_close        转债收盘价
    pool_ok         是否在可投池（20 交易日日均成交额 >= 500 万）
    redeem_exclude  是否被 AkShare 强赎快照剔除（1/0）
    days_to_stop    距 conv_stop_date/delist_date 的天数（无则 9999）
    put_risk        是否命中回售风险（最后两计息年度 + 正股价<转股价×75% + 转债价>103）
    credit_ok       信用排雷「极轻档」：正股非 ST 且 评级 >= BBB（评级缺失 = 1 不剔除）
    credit_ok_strict 加严一档：正股非 ST 且 评级 >= BBB+（即剔除 BBB 及以下）
    credit_watch    观察名单（R7 评级下调 / R8 展望负面或列入观察）—— 只标注，不剔除

说明：策略端只做"读表 + 7 条规则 + 取 40% 分位 + 下单"，
      所有需要外部数据（AkShare 快照、cb_basic 条款日期、流动性）的判断都在本表预先算好。
      信用排雷（2026-09-22 接入）同样在本表算好，回测端只用 --credit-filter 开关切换。
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
PRED_DIR = LAB_DIR / "output" / "predictions"
JQ_SUFFIX = {".SH": ".XSHG", ".SZ": ".XSHE", ".BJ": ".XBEI"}

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB",
                "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C"]
RANK = {grade: i for i, grade in enumerate(RATING_ORDER)}


def log(message: str) -> None:
    print(message, flush=True)


def to_jq(code: str) -> str:
    text = str(code)
    for tushare, jq in JQ_SUFFIX.items():
        if text.endswith(tushare):
            return text[: -len(tushare)] + jq
    return text


def parse_count(text) -> float:
    import re
    if not isinstance(text, str):
        return np.nan
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", text)
    return float(m.group(1)) if m else np.nan


def credit_columns(pairs: pd.DataFrame) -> pd.DataFrame:
    """按 (rebalance_date, ts_code) 生成信用排雷列（全部 PIT）。

    - ST：取 st_mask 中 <= 调仓日 的最近一行
    - 评级：取 cb_rating 中 ann_date <= 调仓日 的最近一条（前一条用于判断下调）
    - 评级缺失 -> credit_ok = 1（不剔除，保持历史行为）
    - ST 状态未知（正股不在 cb_basic、正股不在掩码列、无掩码行、掩码值缺失）
      -> st_flag = None，credit_ok / credit_ok_strict 均为 0（fail-closed，剔除）
    """
    rating = pd.read_parquet(CACHE / "cb_rating.parquet")
    rating["ann_date"] = rating["ann_date"].astype(str)
    rating = rating.sort_values(["ts_code", "ann_date", "rating_date"])
    basic = pd.read_parquet(CACHE / "cb_basic.parquet")[["ts_code", "stk_code"]].drop_duplicates("ts_code")
    stk_of = dict(zip(basic["ts_code"], basic["stk_code"]))
    st = pd.read_parquet(CACHE / "st_mask.parquet")
    st_index = sorted([str(x) for x in st.index])

    rows = []
    for day in sorted(pairs["rebalance_date"].astype(str).unique()):
        d8 = day.replace("-", "")[:8]
        sub = rating[rating["ann_date"] <= d8]
        last = sub.groupby("ts_code").tail(1).set_index("ts_code")
        prev = sub.groupby("ts_code").nth(-2).set_index("ts_code")
        days = [x for x in st_index if x <= d8]
        st_row = None
        if days:
            picked = st.loc[days[-1]]
            st_row = picked.iloc[-1] if isinstance(picked, pd.DataFrame) else picked
        for _, pair in pairs[pairs["rebalance_date"].astype(str) == day].iterrows():
            cb = pair["ts_code"]
            # fail-closed：无法确认正股 ST 状态时记为 None（未知），下方按不合格处理
            stk = stk_of.get(cb)
            st_flag = None
            if st_row is not None and stk is not None and str(stk) not in ("nan", "None"):
                st_value = st_row.get(str(stk), None)
                if st_value is not None and not pd.isna(st_value):
                    st_flag = bool(st_value)
            grade = last["rating"].get(cb) if cb in last.index else None
            grade_prev = prev["rating"].get(cb) if (len(prev) and cb in prev.index) else None
            outlook = str(last["rating_outlook"].get(cb)) if cb in last.index else ""
            rank = RANK.get(grade) if isinstance(grade, str) else None
            downgraded = (isinstance(grade, str) and isinstance(grade_prev, str)
                          and RANK.get(grade, 99) > RANK.get(grade_prev, 99))
            # 只有"确认非 ST"才通过 ST 这一关；未知（None）与确认 ST 都不通过
            st_pass = st_flag is False
            rows.append({
                "rebalance_date": day,
                "ts_code": cb,
                "st_flag_pit": None if st_flag is None else int(st_flag),
                "rating_pit": grade,
                "rating_prev_pit": grade_prev,
                "rating_outlook_pit": outlook,
                "credit_ok": int(st_pass and (rank is None or rank <= RANK["BBB"])),
                "credit_ok_strict": int(st_pass and (rank is None or rank <= RANK["BBB+"])),
                "credit_watch": int(downgraded or ("负面" in outlook) or ("观察" in outlook)),
            })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 CB 历史 PIT 预测值表")
    ap.add_argument("--output", type=Path, default=PRED_DIR / "cb_predictions_v1.csv")
    args = ap.parse_args()
    factor = pd.read_parquet(OUT_DIR / "cb_double_low_pit_soft.parquet")
    # Rebuild liquidity from the original daily cache with a complete 20-day window.
    liq = pd.read_parquet(CACHE / "cb_daily.parquet", columns=["ts_code", "trade_date", "amount"])
    liq["trade_date"] = pd.to_datetime(liq["trade_date"].astype(str)).dt.strftime("%Y-%m-%d")
    liq = liq.sort_values(["ts_code", "trade_date"])
    liq["avg_amount"] = liq.groupby("ts_code")["amount"].transform(
        lambda s: pd.to_numeric(s, errors="coerce").rolling(20, min_periods=20).mean()) * 1000.0
    liq = liq.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})[
        ["symbol", "rebalance_date", "avg_amount"]]
    basic = pd.read_parquet(CACHE / "cb_basic.parquet")[
        ["ts_code", "maturity_date", "value_date", "conv_stop_date", "delist_date"]].drop_duplicates("ts_code")

    frame = factor.reset_index(drop=True)
    frame["ts_code"] = frame["symbol"].astype(str)
    frame["code6"] = frame["ts_code"].str.split(".").str[0]

    # 可投池
    frame = frame.merge(liq.rename(columns={"symbol": "ts_code"}),
                        on=["ts_code", "rebalance_date"], how="left")
    frame["pool_ok"] = (frame["avg_amount"] >= 5e6).astype(int)

    # 当前强赎快照没有历史公告时间，不能回填到历史调仓日。
    frame["redeem_exclude"] = 0

    # 距转股停止/退市天数
    frame = frame.merge(basic.rename(columns={"ts_code": "ts_code"}), on="ts_code", how="left")
    # 当前停止/退市状态没有历史 ASOF；历史仅用合同到期日。
    stop = frame["maturity_date"]
    days = (pd.to_datetime(stop, errors="coerce") - pd.to_datetime(frame["rebalance_date"])).dt.days
    frame["days_to_stop"] = days.fillna(9999).clip(lower=-9999).astype(int)

    # 回售风险：最后两个计息年度 + 正股价 < 转股价×75% + 转债价 > 103
    years_left = (pd.to_datetime(frame["maturity_date"], errors="coerce")
                  - pd.to_datetime(frame["rebalance_date"])).dt.days / 365.25
    stk_close = frame["cb_close"] / (1.0 + frame["premium_rate"] / 100.0) * frame["conv_price"] / 100.0
    frame["put_risk"] = (((years_left <= 2) & (years_left > 0)
                          & (stk_close < frame["conv_price"] * 0.75)
                          & (frame["cb_close"] > 103))).astype(int)

    # 信用排雷（2026-09-22）：credit_ok / credit_ok_strict / credit_watch（全部 PIT）
    credit = credit_columns(frame[["rebalance_date", "ts_code"]].drop_duplicates())
    frame = frame.merge(credit, on=["rebalance_date", "ts_code"], how="left")
    # fail-closed：merge 未命中时不得默认通过，缺信用判定信息的行按不合格处理
    credit_missing = int(frame[["credit_ok", "credit_ok_strict"]].isna().any(axis=1).sum())
    if credit_missing:
        log("[warn] 信用判定缺失 {:,} 行，按不合格（credit_ok=0 / credit_ok_strict=0）处理".format(credit_missing))
    for col in ("credit_ok", "credit_ok_strict", "credit_watch"):
        frame[col] = frame[col].fillna(0).astype(int)
    log("信用排雷：ST 状态未知 {:,} 行（fail-closed，已按不合格处理）".format(
        int(frame["st_flag_pit"].isna().sum())))
    log("信用排雷：credit_ok=0 {:,} 行（其中 pool_ok=1 的 {:,} 行）；credit_ok_strict=0 {:,} 行；credit_watch=1 {:,} 行".format(
        int((frame["credit_ok"] == 0).sum()), int(((frame["credit_ok"] == 0) & (frame["pool_ok"] == 1)).sum()),
        int((frame["credit_ok_strict"] == 0).sum()), int((frame["credit_watch"] == 1).sum())))
    log("信用排雷：评级覆盖 {:,}/{:,} 行（{:.1f}%）".format(
        int(frame["rating_pit"].notna().sum()), len(frame), 100 * frame["rating_pit"].notna().mean()))

    out = pd.DataFrame({
        "date": frame["rebalance_date"],
        "symbol": frame["ts_code"].map(to_jq),
        "double_low_value": frame["cb_double_low_value"].round(4),
        "premium_rate": frame["premium_rate"].round(4),
        "cb_close": frame["cb_close"].round(4),
        "pool_ok": frame["pool_ok"],
        "redeem_exclude": frame["redeem_exclude"],
        "days_to_stop": frame["days_to_stop"],
        "put_risk": frame["put_risk"],
        "credit_ok": frame["credit_ok"],
        "credit_ok_strict": frame["credit_ok_strict"],
        "credit_watch": frame["credit_watch"],
    }).sort_values(["date", "double_low_value"])
    dst = args.output
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False, encoding="utf-8")
    log("输出 {} 行={:,} 期={:,} 转债={:,} 大小={:.2f} MB".format(
        dst.name, len(out), out["date"].nunique(), out["symbol"].nunique(), dst.stat().st_size / 1e6))
    log("可投池行数 {:,}（{:.1f}%）；强赎剔除 {:,} 行；回售风险 {:,} 行；停止日 <30 天 {:,} 行".format(
        int(out["pool_ok"].sum()), out["pool_ok"].mean() * 100, int(out["redeem_exclude"].sum()),
        int(out["put_risk"].sum()), int((out["days_to_stop"] < 30).sum())))
    log("前 3 行:\n{}".format(out.head(3).to_string(index=False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
