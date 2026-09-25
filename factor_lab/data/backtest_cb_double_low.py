# -*- coding: utf-8 -*-
r"""CB 双低策略 · 本地回测（聚宽回测环境不支持 CB 交易，改用本地模拟）。

输入：
    output/predictions/cb_predictions_v1.csv      预测值表（规则所需全部字段）
    cache/cb_daily.parquet                        转债日行情（pct_chg 复权收益率）
    cache/index_000985.parquet                    中证全指（自动用 Tushare 拉取并缓存）
    cache/cb_basic.parquet                        转债基础信息（退市日，用于强赎退市处理）

规则（与规格书 v1.2 一致）：
    每月首个交易日调仓；可投池（pool_ok=1）；7 条剔除规则；双低最小 40% 分位；
    等权、单只上限 5%；月度内不调仓。

成本（standard.md v1.2 CB 段，保守口径）：
    佣金 0.0002（每笔最低 1 元）+ 滑点 0.002（单边），按双边换手计。

输出：
    output/cb_backtest/equity_curves.parquet
    output/cb_backtest/metrics.parquet
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
OUT = LAB_DIR / "output" / "cb_backtest"
OUT.mkdir(parents=True, exist_ok=True)

COMMISSION = 0.0002
MIN_COMMISSION = 1.0
MIN_COMMISSION_SH = 1.0            # 沪市 CB（11 开头）最低佣金
MIN_COMMISSION_SZ = 0.0            # 深市 CB（12 开头）无最低佣金
SLIPPAGE = 0.002
CAPITAL = 100000.0
MAX_WEIGHT = 0.05
TOP_QUANTILE = 0.50
HYSTERESIS_QUANTILE = 0.60         # 卖出阈值：已持仓仍在该分位内则不换出
WEIGHT_BAND = 0.02                 # 权重无交易带：漂移在带内不交易（v3）
CASH_ANNUAL = 0.0383
MIN_PRICE, HIGH_PRICE, HIGH_PRICE_PREMIUM = 80.0, 130.0, 2.0
MIN_DAYS_TO_STOP = 30

# 信用排雷（2026-09-22）：**默认开启**（用户 2026-09-22 采纳极轻档）
# 传 --credit-filter off 可回到历史行为（用于复现 v1.3 及更早的归档结论）
#   light  = 正股 ST 或 评级 <= BB+（保留 BBB 及以上）-> 预测值表列 credit_ok
#   strict = 正股 ST 或 评级 <= BBB（保留 BBB+ 及以上）-> 预测值表列 credit_ok_strict
CREDIT_FILTER = True
CREDIT_TIER = "light"
CREDIT_COLUMN = {"light": "credit_ok", "strict": "credit_ok_strict"}

SEGMENTS = [("main", "2018-01-01", "2026-09-01"),
            ("in_sample", "2018-01-01", "2022-12-31"),
            ("out_sample", "2023-01-01", "2026-09-01")]


def log(message: str) -> None:
    print(message, flush=True)


def load_index() -> pd.DataFrame | None:
    path = CACHE / "index_000985.parquet"
    if not path.exists():
        try:
            import tushare as ts
            token = os.environ.get("TUSHARE_TOKEN")
            if not token:
                return None
            pro = ts.pro_api(token)
            df = pro.index_daily(ts_code="000985.SH", start_date="20171201", end_date="20260930")
            if df is None or df.empty:
                return None
            df = df[["trade_date", "pct_chg"]].copy()
            df["trade_date"] = df["trade_date"].astype(str)
            df["trade_date"] = df["trade_date"].str[:4] + "-" + df["trade_date"].str[4:6] + "-" + df["trade_date"].str[6:8]
            df = df.sort_values("trade_date")
            df.to_parquet(path, index=False)
            log("中证全指已缓存：{} 行".format(len(df)))
        except Exception as exc:
            log("中证全指获取失败（将跳过该基准）：{}".format(str(exc)[:120]))
            return None
    return pd.read_parquet(path)


def eligible(frame: pd.DataFrame, use_credit: bool = True) -> pd.DataFrame:
    """规格书 7 条剔除规则（+ 可选的信用排雷极轻档）。"""
    mask = (frame["pool_ok"] == 1) & (frame["redeem_exclude"] == 0) & (frame["put_risk"] == 0)
    mask &= frame["premium_rate"] > 0
    mask &= ~((frame["cb_close"] > HIGH_PRICE) & (frame["premium_rate"] < HIGH_PRICE_PREMIUM))
    mask &= frame["days_to_stop"] >= MIN_DAYS_TO_STOP
    mask &= frame["cb_close"] >= MIN_PRICE
    if CREDIT_FILTER and use_credit:
        mask &= frame[CREDIT_COLUMN[CREDIT_TIER]] == 1
    return frame[mask]


def min_commission_for(code: str) -> float:
    """按市场返回最低佣金：沪市（11 开头）用 SH 值，深市（12 开头）用 SZ 值，其它兜底。"""
    text = str(code)
    if text.startswith("11"):
        return MIN_COMMISSION_SH
    if text.startswith("12"):
        return MIN_COMMISSION_SZ
    return MIN_COMMISSION


def run_one(pred: pd.DataFrame, ret: pd.DataFrame, dates: list[str], codes: set[str],
            mode: str) -> tuple[pd.Series, dict]:
    """mode: 'strategy' 按 7 规则 + 40% 分位；'pool' 用全部可投池等权。"""
    reb_dates = sorted(pred["date"].unique())
    reb_set = set(reb_dates)
    daily_ret = ret  # index: (trade_date, code) -> pct
    equity = CAPITAL  # 以元为单位，避免最低佣金（元）与净值（1.0 起算）单位不一致
    curve = []
    weights: dict[str, float] = {}
    turnover_list, rebal_count, hold_counts, turnover_rows = [], 0, [], []
    credit_rows, credit_drops = [], []

    grouped = {d: g for d, g in pred.groupby("date")}
    for day in dates:
        # 当日收益
        if weights:
            day_r = 0.0
            for code, w in weights.items():
                r = daily_ret.get((day, code))
                if r is not None and not np.isnan(r):
                    day_r += w * r
            equity *= (1.0 + day_r)
            # 权重随收益漂移
            if day_r != 0:
                weights = {c: w * (1.0 + (daily_ret.get((day, c)) or 0.0)) / (1.0 + day_r)
                           for c, w in weights.items()}
        curve.append((day, equity))

        if day in reb_set and day != dates[-1]:
            board = grouped[day]
            if mode == "strategy":
                sel = eligible(board).sort_values("double_low_value")
                if CREDIT_FILTER:
                    base = eligible(board, use_credit=False).sort_values("double_low_value")
                    n_base = max(1, int(len(base) * TOP_QUANTILE)) if len(base) else 0
                    base_buy = set(base.head(n_base)["symbol"].tolist())
                    dropped_buy = len(base_buy - set(sel["symbol"].tolist()))
                    credit_drops.append(dropped_buy)
                    credit_rows.append({"rebalance_date": day, "eligible_base": len(base),
                                        "eligible_credit": len(sel),
                                        "dropped_eligible": len(base) - len(sel),
                                        "dropped_buy": dropped_buy})
                if sel.empty:
                    target_codes = []
                else:
                    n_buy = max(1, int(len(sel) * TOP_QUANTILE))
                    buy_codes = sel.head(n_buy)["symbol"].tolist()
                    n_keep = max(1, int(len(sel) * HYSTERESIS_QUANTILE))
                    keep_set = set(sel.head(n_keep)["symbol"].tolist())
                    # 换手缓冲带：已持仓且仍在 keep 区间内（但不在买入区）-> 保留不卖
                    held_in = [c for c in weights if c in keep_set and c not in buy_codes]
                    target_codes = buy_codes + held_in
            else:
                target_codes = board[board["pool_ok"] == 1]["symbol"].tolist()
            if not target_codes:
                continue
            weight = min(1.0 / len(target_codes), MAX_WEIGHT) * 0.995
            target = {c: weight for c in target_codes}
            # v3：权重无交易带（仅策略模式）——已持仓且与目标权重差在带内 -> 保持原权重不动
            if mode == "strategy" and WEIGHT_BAND > 0:
                for code in list(target):
                    current = weights.get(code)
                    if current is not None and abs(current - target[code]) <= WEIGHT_BAND:
                        target[code] = current
            # 换手（单边）与成本
            all_codes = set(weights) | set(target)
            delta = sum(abs(target.get(c, 0.0) - weights.get(c, 0.0)) for c in all_codes)
            traded = delta * equity
            n_orders = len([c for c in all_codes
                            if abs(target.get(c, 0.0) - weights.get(c, 0.0)) > 1e-6])
            # 佣金按笔计：每笔 max(成交额 × 佣金率, 该市场最低佣金)；滑点按总成交额
            commission_cost = 0.0
            for code in all_codes:
                amount = abs(target.get(code, 0.0) - weights.get(code, 0.0)) * equity
                if amount <= 1e-9:
                    continue
                commission_cost += max(amount * COMMISSION, min_commission_for(code))
            cost = commission_cost + traded * SLIPPAGE
            equity -= cost
            turnover_list.append(delta / 2.0)
            turnover_rows.append({"rebalance_date": day, "turnover": delta / 2.0,
                                  "n_orders": n_orders, "n_target": len(target_codes),
                                  "equity_before_cost": equity + cost})
            rebal_count += 1
            hold_counts.append(len(target_codes))
            weights = target

    curve = pd.Series(dict(curve))
    stats = {"turnover_avg": float(np.mean(turnover_list)) if turnover_list else np.nan,
             "rebalance_count": rebal_count,
             "hold_avg": float(np.mean(hold_counts)) if hold_counts else np.nan,
             "turnover_cum": float(np.sum(turnover_list)) if turnover_list else np.nan,
             "cost_total_pct": float((curve.iloc[-1] and 0.0))}
    stats["turnover_detail"] = pd.DataFrame(turnover_rows)
    if CREDIT_FILTER:
        stats["credit_drop_avg"] = float(np.mean(credit_drops)) if credit_drops else np.nan
        stats["credit_drop_total"] = int(np.sum(credit_drops)) if credit_drops else 0
        stats["credit_periods_with_drop"] = int(np.sum([1 for x in credit_drops if x > 0])) if credit_drops else 0
        stats["credit_detail"] = pd.DataFrame(credit_rows)
    return curve, stats


def metrics(curve: pd.Series, cash_annual: float = CASH_ANNUAL) -> dict:
    curve = curve.dropna()
    n = len(curve)
    years = n / 252.0
    total = curve.iloc[-1] / curve.iloc[0] - 1.0
    ann = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    r = curve.pct_change().dropna()
    vol = r.std() * np.sqrt(252)
    sharpe = (ann - cash_annual) / vol if vol and vol > 1e-9 else np.nan
    dd = (curve / curve.cummax() - 1.0).min()
    calmar = ann / abs(dd) if dd < 0 else np.nan
    monthly = curve.resample("ME").last().pct_change().dropna() if isinstance(curve.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    return {"days": n, "total_return": total, "annual_return": ann, "vol": vol,
            "sharpe": sharpe, "sharpe_raw": (ann / vol if vol and vol > 1e-9 else np.nan),
            "max_drawdown": dd, "calmar": calmar,
            "month_win_rate": float((monthly > 0).mean()) if len(monthly) else np.nan}


def main() -> int:
    global COMMISSION, SLIPPAGE, MIN_COMMISSION, MIN_COMMISSION_SH, MIN_COMMISSION_SZ
    global TOP_QUANTILE, HYSTERESIS_QUANTILE, WEIGHT_BAND
    global CREDIT_FILTER, CREDIT_TIER, OUT
    ap = argparse.ArgumentParser(description="CB 双低本地回测")
    ap.add_argument("--commission", type=float, default=COMMISSION)
    ap.add_argument("--slippage", type=float, default=SLIPPAGE)
    ap.add_argument("--min-commission", type=float, default=MIN_COMMISSION)
    ap.add_argument("--min-commission-sh", type=float, default=MIN_COMMISSION_SH,
                    help="沪市 CB（11 开头）最低佣金，默认 1 元")
    ap.add_argument("--min-commission-sz", type=float, default=MIN_COMMISSION_SZ,
                    help="深市 CB（12 开头）最低佣金，默认 0（无最低）")
    ap.add_argument("--top-quantile", type=float, default=TOP_QUANTILE, help="买入分位（默认 0.50）")
    ap.add_argument("--hysteresis-quantile", type=float, default=0.60, help="卖出阈值分位（默认 0.60）")
    ap.add_argument("--weight-band", type=float, default=WEIGHT_BAND,
                    help="权重无交易带（默认 0.02；0 表示关闭，回到 v2 行为）")
    ap.add_argument("--credit-filter", default="on", choices=["on", "off"],
                    help="信用排雷开关（默认 on；传 off 可复现 v1.3 历史行为）")
    ap.add_argument("--credit-tier", default="light", choices=["light", "strict"],
                    help="light=保留 BBB 及以上；strict=保留 BBB+ 及以上")
    ap.add_argument("--tag", default="conservative")
    ap.add_argument("--predictions", type=Path, default=LAB_DIR / "output" / "predictions" / "cb_predictions_v1.csv")
    ap.add_argument("--output-dir", type=Path, default=OUT)
    ap.add_argument("--execution-timing", default="same_close", choices=["same_close", "next_open"],
                    help="执行时点：same_close=信号与成交同为 T 日收盘（历史默认）；next_open=信号 T 日收盘、T+1 开盘执行（仅标签，未实现开盘成交）")
    args = ap.parse_args()
    OUT = args.output_dir
    OUT.mkdir(parents=True, exist_ok=True)
    COMMISSION, SLIPPAGE, MIN_COMMISSION = args.commission, args.slippage, args.min_commission
    MIN_COMMISSION_SH, MIN_COMMISSION_SZ = args.min_commission_sh, args.min_commission_sz
    TOP_QUANTILE, HYSTERESIS_QUANTILE = args.top_quantile, args.hysteresis_quantile
    WEIGHT_BAND = args.weight_band
    CREDIT_FILTER = (args.credit_filter == "on")
    CREDIT_TIER = args.credit_tier
    log("情景 {}: 佣金={:.5f} 滑点={:.4f} 最低佣金={:.1f} 元".format(
        args.tag, COMMISSION, SLIPPAGE, MIN_COMMISSION))
    log("最低佣金（按市场）: 沪市(11) {:.1f} 元 / 深市(12) {:.1f} 元 / 其它兜底 {:.1f} 元".format(
        MIN_COMMISSION_SH, MIN_COMMISSION_SZ, MIN_COMMISSION))
    log("参数: 买入分位 {:.0%}，卖出阈值分位 {:.0%}（缓冲带 = 两者之差）".format(
        TOP_QUANTILE, HYSTERESIS_QUANTILE))
    log("参数: 权重无交易带 {:.2%}".format(WEIGHT_BAND))
    log("参数: 信用排雷 {}（档位 {}，列 {}）".format(
        "ON" if CREDIT_FILTER else "OFF", CREDIT_TIER, CREDIT_COLUMN[CREDIT_TIER]))
    pred = pd.read_csv(args.predictions, dtype={"date": str, "symbol": str})
    if CREDIT_FILTER and CREDIT_COLUMN[CREDIT_TIER] not in pred.columns:
        log("ERROR: 预测值表缺少列 {}，请先重跑 make_cb_predictions.py".format(CREDIT_COLUMN[CREDIT_TIER]))
        return 2
    if CREDIT_FILTER:
        col = CREDIT_COLUMN[CREDIT_TIER]
        log("信用排雷列 {}: 全场 {} 行中 0 值 {:,} 行；可投池内 0 值 {:,} 行".format(
            col, len(pred), int((pred[col] == 0).sum()),
            int(((pred[col] == 0) & (pred["pool_ok"] == 1)).sum())))
    daily = pd.read_parquet(CACHE / "cb_daily.parquet", columns=["ts_code", "trade_date", "pct_chg"])
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily["trade_date"] = daily["trade_date"].str[:4] + "-" + daily["trade_date"].str[4:6] + "-" + daily["trade_date"].str[6:8]
    daily["pct"] = pd.to_numeric(daily["pct_chg"], errors="coerce") / 100.0
    # 行情是 Tushare 代码（113050.SH），预测值表是聚宽代码（113050.XSHG）-> 统一成聚宽格式
    jq_map = {".SH": ".XSHG", ".SZ": ".XSHE", ".BJ": ".XBEI"}
    daily["jq_code"] = daily["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", lambda m: jq_map[m.group(0)], regex=True)
    ret = daily.set_index(["trade_date", "jq_code"])["pct"].to_dict()
    log("行情代码格式已转聚宽口径，样例: {}".format(daily["jq_code"].iloc[0]))
    all_days = sorted(daily["trade_date"].unique())
    log("预测值表 {:,} 行 / {} 期；行情 {:,} 行 / {} 个交易日".format(
        len(pred), pred["date"].nunique(), len(daily), len(all_days)))

    index_df = load_index()
    idx_ret = {}
    if index_df is not None:
        index_df["pct"] = pd.to_numeric(index_df["pct_chg"], errors="coerce") / 100.0
        idx_ret = dict(zip(index_df["trade_date"], index_df["pct"]))

    curves, rows = {}, []
    details = {}
    credit_details = {}
    for seg, start, end in SEGMENTS:
        dates = [d for d in all_days if start <= d <= end]
        if len(dates) < 30:
            continue
        for mode in ("strategy", "pool"):
            sub_pred = pred[(pred["date"] >= start) & (pred["date"] <= end)]
            curve, stats = run_one(sub_pred, ret, dates, set(sub_pred["symbol"]), mode)
            curve.index = pd.to_datetime(curve.index)
            curves["{}_{}".format(seg, mode)] = curve
            m = metrics(curve)
            m.update({"segment": seg, "mode": mode, **stats})
            if mode == "strategy":
                details[seg] = stats.get("turnover_detail")
                if CREDIT_FILTER:
                    credit_details[seg] = stats.get("credit_detail")
            # 现金曲线
            cash = pd.Series(1.0 * (1.0 + CASH_ANNUAL) ** (np.arange(len(dates)) / 252.0), index=pd.to_datetime(dates))
            if mode == "strategy":
                curves["{}_cash".format(seg)] = cash
                rows.append({"segment": seg, "mode": "cash", **metrics(cash)})
                if idx_ret:
                    idx_pct = pd.Series({pd.Timestamp(d): idx_ret.get(d, 0.0) for d in dates})
                    idx_curve = (1.0 + idx_pct.fillna(0.0)).cumprod()
                    curves["{}_index".format(seg)] = idx_curve
                    rows.append({"segment": seg, "mode": "index_000985", **metrics(idx_curve)})
            rows.append({"segment": seg, "mode": mode, "days": m["days"], "total_return": m["total_return"],
                         "annual_return": m["annual_return"], "vol": m["vol"], "sharpe": m["sharpe"],
                         "sharpe_raw": m["sharpe_raw"],
                         "max_drawdown": m["max_drawdown"], "calmar": m["calmar"],
                         "month_win_rate": m["month_win_rate"], "turnover_avg": m.get("turnover_avg"),
                         "turnover_cum": m.get("turnover_cum"), "turnover_detail": m.get("turnover_detail"),
                         "rebalance_count": m.get("rebalance_count"), "hold_avg": m.get("hold_avg")})
            if mode == "strategy" and CREDIT_FILTER:
                rows[-1]["credit_drop_avg"] = m.get("credit_drop_avg")
                rows[-1]["credit_drop_total"] = m.get("credit_drop_total")
                rows[-1]["credit_periods_with_drop"] = m.get("credit_periods_with_drop")

    eq = pd.concat(curves, axis=1)
    # 每列按各自的第一个有效值归一化为 1.0（分段列在其它区间为 NaN）
    eq = eq.apply(lambda col: col / col.dropna().iloc[0] if col.notna().any() else col)
    eq.to_parquet(OUT / "equity_curves_{}.parquet".format(args.tag))
    metrics_df = pd.DataFrame(rows).drop(columns=["turnover_detail"], errors="ignore")
    metrics_df.to_parquet(OUT / "metrics_{}.parquet".format(args.tag), index=False)
    # 换手率明细（策略情景，每期一行）
    for seg, det in details.items():
        if isinstance(det, pd.DataFrame) and not det.empty:
            det.to_parquet(OUT / "turnover_{}_{}.parquet".format(args.tag, seg), index=False)
    if CREDIT_FILTER:
        for seg, det in credit_details.items():
            if isinstance(det, pd.DataFrame) and not det.empty:
                det.to_parquet(OUT / "credit_drop_{}_{}.parquet".format(args.tag, seg), index=False)
    import hashlib
    import json
    from datetime import datetime, timezone
    def digest(path):
        h = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(), "tag": args.tag,
        "predictions": str(args.predictions.resolve()), "predictions_sha256": digest(args.predictions),
        "daily_sha256": digest(CACHE / "cb_daily.parquet"),
        "code_sha256": digest(Path(__file__)),
        "credit_filter": args.credit_filter, "credit_tier": args.credit_tier,
        "top_quantile": TOP_QUANTILE, "hysteresis_quantile": HYSTERESIS_QUANTILE,
        "weight_band": WEIGHT_BAND, "max_weight": MAX_WEIGHT,
        "commission": COMMISSION, "slippage": SLIPPAGE,
        "min_commission_sh": MIN_COMMISSION_SH, "min_commission_sz": MIN_COMMISSION_SZ,
        "execution_timing": args.execution_timing,
        "execution_note": "引擎只支持收盘成交（信号日=执行日）；next_open 仅为标签/位移近似，未实现开盘成交逻辑",
    }
    (OUT / "manifest_{}.json".format(args.tag)).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log("净值曲线写出: {} 行={:,} 列={}".format("equity_curves_{}.parquet".format(args.tag), len(eq), list(eq.columns)))
    log("指标表写出: metrics_{}.parquet 行={}".format(args.tag, len(metrics_df)))

    for seg, _, _ in SEGMENTS:
        block = metrics_df[metrics_df["segment"] == seg]
        if block.empty:
            continue
        log("\n=== {} ===".format(seg))
        show = block[["mode", "days", "total_return", "annual_return", "vol", "sharpe", "sharpe_raw",
                      "max_drawdown", "calmar", "turnover_avg", "hold_avg", "rebalance_count", "month_win_rate"]].copy()
        for c in ("total_return", "annual_return", "vol", "max_drawdown", "turnover_avg", "month_win_rate"):
            show[c] = show[c].map(lambda v: "{:.2%}".format(v) if pd.notna(v) else "NA")
        for c in ("sharpe", "sharpe_raw", "calmar", "hold_avg"):
            show[c] = show[c].map(lambda v: "{:.3f}".format(v) if pd.notna(v) else "NA")
        log(show.to_string(index=False))
        st = block[block["mode"] == "strategy"]
        pl = block[block["mode"] == "pool"]
        if not st.empty and not pl.empty:
            log("  年化超额（策略 - 可投池等权）= {:+.2%}".format(
                st.iloc[0]["annual_return"] - pl.iloc[0]["annual_return"]))
        if CREDIT_FILTER and not st.empty and "credit_drop_avg" in st.columns:
            log("  信用排雷: 平均剔除 {:.2f} 只/期（合计 {} 只，有剔除的期数 {}）".format(
                st.iloc[0]["credit_drop_avg"], st.iloc[0]["credit_drop_total"],
                st.iloc[0]["credit_periods_with_drop"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
