# -*- coding: utf-8 -*-
r"""CB 双低 · 月度实盘模拟信号

每月末（或任意时点）跑一次，输出下一期调仓的目标清单与买卖清单，
作为"实盘模拟"的输入；不连接券商、不下单。

规则与规格书 v1.4 完全一致：
    可投池（20 交易日日均成交额 >= 500 万）+ 7 条剔除规则 + 第 8 条信用排雷（极轻档）
    → 双低值（转债价 + 转股溢价率×100）升序取 Top 50%
    → 等权、单只上限 5%、×0.995 成本缓冲

信用排雷（2026-09-22 接入，与本地回测同源：直接复用 make_cb_predictions.credit_columns）：
    credit_ok    = 正股非 ST 且主体评级 >= BBB（PIT；评级缺失 = 1 不剔除）
                   -> credit_ok = 0 的从目标清单剔除
    credit_watch = R7 最近一次评级被下调 / R8 展望负面或列入观察
                   -> 只标注，不剔除

输入：
    cache/cb_daily.parquet        转债行情（close/amount/pct_chg/vol）
    cache/cb_convert_*.parquet    聚宽 PIT 转股价与转股溢价率
    cache/cb_redeem_jsl.parquet   集思录强赎快照
    cache/cb_basic.parquet        转债基础信息（名称/转股停止日/退市日/到期日）
    cache/cb_rating.parquet       主体评级历史（PIT：ann_date <= 信号日）
    cache/st_mask.parquet         正股 ST 掩码（取 <= 信号日最近一行）
    output/monthly_signals/*.csv  上一期信号（用于生成卖出清单）

输出：
    output/monthly_signals/<信号日>.csv       本期目标清单
    output/monthly_signals/<信号日>_sell.csv  本期卖出清单

用法：
    python monthly_signal.py [--date 2026-09-21] [--capital 100000] [--top-quantile 0.50]
"""
from __future__ import annotations
import argparse
import os
import tempfile
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))
from make_cb_predictions import credit_columns  # noqa: E402  信用排雷（与回测同源）

LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT = LAB_DIR / "output" / "monthly_signals"
OUT.mkdir(parents=True, exist_ok=True)

MIN_AMOUNT = 5e6          # 可投池门槛：20 交易日日均成交额 500 万
LOOKBACK_DAYS = 20
MIN_PRICE, HIGH_PRICE, HIGH_PRICE_PREMIUM = 80.0, 130.0, 2.0
MIN_DAYS_TO_STOP = 30
MAX_WEIGHT, COST_BUFFER, TOP_QUANTILE = 0.05, 0.995, 0.50
JQ_SUFFIX = {".SH": ".XSHG", ".SZ": ".XSHE", ".BJ": ".XBEI"}


def log(msg: str) -> None:
    print(msg, flush=True)


def norm_date(v) -> str:
    t = str(v).strip()
    d = t.replace("-", "")[:8]
    return "{}-{}-{}".format(d[:4], d[4:6], d[6:8]) if len(d) == 8 and d.isdigit() else t[:10]


def main() -> int:
    ap = argparse.ArgumentParser(description="CB 双低月度实盘模拟信号")
    ap.add_argument("--date", default=None, help="信号日（默认取行情最新交易日）")
    ap.add_argument("--capital", type=float, default=100000.0)
    ap.add_argument("--top-quantile", type=float, default=TOP_QUANTILE)
    ap.add_argument("--min-amount", type=float, default=MIN_AMOUNT)
    args = ap.parse_args()

    # 1. 行情与流动性
    daily = pd.read_parquet(CACHE / "cb_daily.parquet",
                            columns=["ts_code", "trade_date", "close", "amount", "vol"])
    daily["trade_date"] = daily["trade_date"].map(norm_date)
    daily = daily.sort_values(["ts_code", "trade_date"])
    daily["avg_amount"] = daily.groupby("ts_code")["amount"].transform(
        lambda s: s.rolling(LOOKBACK_DAYS, min_periods=LOOKBACK_DAYS).mean()) * 1000.0
    signal_date = args.date or daily["trade_date"].max()
    log("信号日: {}".format(signal_date))
    today = daily[daily["trade_date"] == signal_date].drop_duplicates("ts_code", keep="last")
    log("当日有行情的转债: {} 只".format(len(today)))

    # 2. 聚宽 PIT 溢价率（必须是信号当日）
    parts = []
    for path in sorted(CACHE.glob("cb_convert_*.parquet")):
        parts.append(pd.read_parquet(path))
    conv = pd.concat(parts, ignore_index=True)
    conv["date"] = conv["date"].map(norm_date)
    conv = conv.drop_duplicates(subset=["code", "date"], keep="last")
    conv_today = conv[conv["date"] == signal_date]
    if conv_today.empty:
        log("ERROR: {} 无当日 PIT 溢价率".format(signal_date))
        return 2
    log("当日有 PIT 溢价率的转债: {} 只".format(len(conv_today)))
    # 评级是事件数据：按 ann_date ASOF 取值，而非要求每天有公告。
    # 但刷新后的缓存文件与 ST 掩码必须覆盖目标信号日。
    rating_path = CACHE / "cb_rating.parquet"
    st_path = CACHE / "st_mask.parquet"
    redeem_path = CACHE / "cb_redeem_jsl.parquet"
    for path, label in ((rating_path, "评级刷新"), (redeem_path, "强赎快照"),
                        (CACHE / "cb_basic.parquet", "基础信息")):
        if not path.exists() or pd.Timestamp(path.stat().st_mtime, unit="s").date() < pd.Timestamp(signal_date).date():
            log("ERROR: {} 未在信号日刷新：{}".format(label, path.name))
            return 2
    if not st_path.exists():
        log("ERROR: 缺少 ST 掩码")
        return 2
    st = pd.read_parquet(st_path)
    st_dates = [norm_date(x) for x in st.index]
    if not st_dates or max(st_dates) < signal_date:
        log("ERROR: ST 掩码最新日期 {} < 信号日 {}".format(max(st_dates) if st_dates else "NA", signal_date))
        return 2

    # 3. 基础信息 + 强赎快照
    basic = pd.read_parquet(CACHE / "cb_basic.parquet")[
        ["ts_code", "bond_short_name", "maturity_date", "conv_stop_date", "delist_date"]
    ].drop_duplicates("ts_code", keep="last")
    redeem = pd.read_parquet(CACHE / "cb_redeem_jsl.parquet")

    def parse_count(text):
        import re
        m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(text))
        return float(m.group(1)) if m else np.nan

    status = redeem["强赎状态"].fillna("")
    announced = set(redeem.loc[status.str.contains("已公告强赎", na=False), "代码"].astype(str))
    redeem["cnt"] = redeem["强赎天计数"].map(parse_count)
    counting = set(redeem.loc[redeem["cnt"].notna() & (redeem["cnt"] >= 12), "代码"].astype(str))
    excl_redeem = announced | counting

    # 4. 合并
    frame = today.copy()
    frame["code6"] = frame["ts_code"].str.split(".").str[0]   # 6 位代码，用于对聚宽转股价表
    frame = frame.merge(conv_today[["code", "convert_price", "convert_premium_rate"]],
                        left_on="code6", right_on="code", how="left")
    frame = frame.merge(basic, on="ts_code", how="left")
    frame["name"] = frame["bond_short_name"]
    frame["premium_rate"] = pd.to_numeric(frame["convert_premium_rate"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["double_low_value"] = frame["close"] + frame["premium_rate"]
    stop = frame["conv_stop_date"].where(frame["conv_stop_date"].notna(), frame["delist_date"])
    frame["days_to_stop"] = (pd.to_datetime(stop, errors="coerce")
                             - pd.to_datetime(signal_date)).dt.days.fillna(9999).astype(int)
    years_left = (pd.to_datetime(frame["maturity_date"], errors="coerce")
                  - pd.to_datetime(signal_date)).dt.days / 365.25
    stk_close = frame["close"] / (1.0 + frame["premium_rate"] / 100.0) * frame["convert_price"] / 100.0
    frame["put_risk"] = ((years_left <= 2) & (years_left > 0)
                         & (stk_close < frame["convert_price"] * 0.75) & (frame["close"] > 103))
    frame["redeem_exclude"] = frame["code6"].isin(excl_redeem)

    # 4b. 信用排雷（第 8 条）：正股 ST + 主体评级 <= BB+ -> credit_ok = 0
    credit = credit_columns(pd.DataFrame({"rebalance_date": signal_date,
                                          "ts_code": frame["ts_code"].astype(str).values}))
    frame = frame.merge(credit.drop(columns=["rebalance_date"]), on="ts_code", how="left")
    frame["credit_ok"] = frame["credit_ok"].fillna(0).astype(int)
    frame["credit_watch"] = frame["credit_watch"].fillna(0).astype(int)
    log("信用排雷：评级覆盖 {} / {} 只（评级缺失按不剔除处理）".format(
        int(frame["rating_pit"].notna().sum()), len(frame)))

    # 5. 逐条剔除（统计口径与归档一致）
    stats = {}
    def apply(mask, label):
        before = len(frame[mask[0]])
        return label, before
    m = pd.Series(True, index=frame.index)
    stats["当日有行情"] = int(m.sum())
    m &= frame["premium_rate"].notna(); stats["有 PIT 溢价率"] = int(m.sum())
    m &= frame["close"] >= MIN_PRICE; stats["价格 >= 80"] = int(m.sum())
    m &= ~frame["redeem_exclude"]; stats["剔除强赎"] = int(m.sum())
    m &= frame["premium_rate"] > 0; stats["剔除折价债"] = int(m.sum())
    m &= ~((frame["close"] > HIGH_PRICE) & (frame["premium_rate"] < HIGH_PRICE_PREMIUM)); stats["剔除高价高溢价"] = int(m.sum())
    m &= frame["days_to_stop"] >= MIN_DAYS_TO_STOP; stats["距停止日 >= 30"] = int(m.sum())
    m &= ~frame["put_risk"].fillna(False); stats["剔除回售风险"] = int(m.sum())
    pool = m & (frame["avg_amount"] >= args.min_amount); stats["可投池（>=500万）"] = int(pool.sum())
    pool_before_credit = int(pool.sum())
    pool = pool & (frame["credit_ok"] == 1); stats["信用排雷（第8条）"] = int(pool.sum())
    log("\n=== 剔除过程（逐条累加）===")
    for k, v in stats.items():
        log("  {:<18} {} 只".format(k, v))
    log("信用剔除 {} 只（可投池 {} -> {}）".format(
        pool_before_credit - int(pool.sum()), pool_before_credit, int(pool.sum())))

    eligible = frame[pool].dropna(subset=["double_low_value"]).sort_values("double_low_value")
    if eligible.empty:
        log("本期无可投标的")
        return 1
    n_keep = max(1, int(len(eligible) * args.top_quantile))
    target = eligible.head(n_keep).copy()
    weight = min(1.0 / len(target), MAX_WEIGHT) * COST_BUFFER
    target["target_weight"] = weight
    target["target_amount"] = args.capital * weight

    # 6. 与上期对比
    prev_files = sorted(OUT.glob("*.csv"))
    prev_files = [p for p in prev_files if not p.stem.endswith("_sell") and p.stem < signal_date]
    prev_codes = set()
    if prev_files:
        prev = pd.read_csv(prev_files[-1], dtype={"code": str})
        prev_codes = set(prev["code"].astype(str))
        log("\n上期信号: {}（{} 只）".format(prev_files[-1].name, len(prev_codes)))
    else:
        log("\n未找到上期信号（首次运行，卖出清单为空）")

    target["code"] = target["code6"]
    target["action"] = np.where(target["code"].isin(prev_codes), "hold", "buy")
    cols_buy = ["code", "ts_code", "name", "double_low_value", "close", "premium_rate",
                "avg_amount", "days_to_stop", "credit_ok", "credit_watch",
                "target_weight", "target_amount", "action"]
    out = target[cols_buy].rename(columns={"close": "price"}).round(
        {"double_low_value": 4, "price": 4, "premium_rate": 4, "avg_amount": 0,
         "target_weight": 4, "target_amount": 2})
    dst = OUT / "{}.csv".format(signal_date)
    if dst.exists():
        log("ERROR: 已有同日信号，禁止覆盖：{}".format(dst))
        return 2

    sell_codes = sorted(prev_codes - set(out["code"].astype(str)))
    sell_df = pd.DataFrame({"code": sell_codes})
    if sell_codes:
        info = frame[frame["code6"].isin(sell_codes)][["code6", "name", "close", "premium_rate", "double_low_value"]]
        sell_df = sell_df.merge(info.rename(columns={"code6": "code"}), on="code", how="left")
    dst_sell = OUT / "{}_sell.csv".format(signal_date)
    if dst_sell.exists():
        log("ERROR: 已有同日卖出文件，禁止覆盖：{}".format(dst_sell))
        return 2
    # 两个临时文件准备好后才发布；写入失败时不留下半份正式清单。
    staged = []
    published = []
    try:
        for table, final in ((out, dst), (sell_df, dst_sell)):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv.tmp", dir=OUT,
                                             encoding="utf-8-sig", newline="", delete=False) as tmp:
                table.to_csv(tmp, index=False)
                staged.append(Path(tmp.name))
        for temporary, final in zip(staged, (dst, dst_sell)):
            if final.exists():
                raise FileExistsError(final)
            os.replace(temporary, final)
            published.append(final)
    except Exception:
        for final in published:
            final.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)

    log("\n=== 本期目标清单 ===")
    log("总持仓 {} 只；其中新买入 {} 只、继续持有 {} 只；单只权重 {:.2f}%（金额 {:.0f} 元）".format(
        len(out), int((out["action"] == "buy").sum()), int((out["action"] == "hold").sum()),
        weight * 100, args.capital * weight))
    watch = out.loc[out["credit_watch"] == 1, "name"].tolist()
    log("观察名单（credit_watch=1，未剔除）: {} 只{}".format(
        len(watch), ("：" + "、".join(watch[:12])) if watch else ""))
    log("\n前 10 只（双低最小）:")
    log(out.head(10)[["code", "name", "double_low_value", "price", "premium_rate", "target_amount", "action"]].to_string(index=False))
    log("\n=== 本期卖出清单（{} 只）===".format(len(sell_df)))
    if len(sell_df):
        log(sell_df.head(10).to_string(index=False))
    log("\n输出: {} / {}".format(dst.name, dst_sell.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
