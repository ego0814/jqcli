# -*- coding: utf-8 -*-
r"""ETF 池等权 · 本地回测（与聚宽策略同规则，用于双引擎交叉验证）。

规则（specs/etf_pool_v1.md v1.3）：
    7 只 ETF 等权；季度再平衡（每年 1/4/7/10 月首个交易日）；单只上限 20%；
    目标权重 × 0.995 留成本缓冲；季度内不调仓。

成本（specs/costs/standard.md ETF 段）：
    佣金 0.0000854、最低 5 元/笔、印花税 0、滑点 0.0005（与聚宽策略一致）
    另提供乐观/中性/保守/极端四情景。

数据：factor_lab/data/cache/etf_daily.parquet（后复权，AkShare/Tushare）
输出：factor_lab/output/etf_backtest/equity_curves.parquet、metrics_{tag}.parquet
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT = LAB_DIR / "output" / "etf_backtest"
OUT.mkdir(parents=True, exist_ok=True)

POOL = ["510500.XSHG", "512800.XSHG", "513100.XSHG", "513050.XSHG",
        "518880.XSHG", "162411.XSHE", "511260.XSHG"]
QUARTER_MONTHS = (1, 4, 7, 10)

COMMISSION = 0.0000854
MIN_COMMISSION = 5.0
SLIPPAGE = 0.0005
COST_BUFFER = 0.995
MAX_WEIGHT = 0.20
CASH_ANNUAL = 0.0383

SEGMENTS = [("main", "2017-10-09", "2026-09-01"),
            ("in_sample", "2017-10-09", "2022-12-31"),
            ("out_sample", "2023-01-01", "2026-09-01")]


def log(msg: str) -> None:
    print(msg, flush=True)


def load_prices() -> pd.DataFrame:
    d = pd.read_parquet(CACHE / "etf_daily.parquet")
    d["date"] = d["date"].astype(str)
    return d


def quarter_rebalance_dates(dates: list[str]) -> list[str]:
    """每年 1/4/7/10 月的首个交易日。"""
    out, seen = [], set()
    for day in dates:
        y, m = day[:4], int(day[5:7])
        if m in QUARTER_MONTHS and (y, m) not in seen:
            seen.add((y, m))
            out.append(day)
    return out


def run_one(ret: dict, dates: list[str], reb_set: set, mode: str) -> tuple[pd.Series, dict]:
    """mode: strategy=季度再平衡；pool=等权买入持有（只建仓一次）。"""
    equity = 100000.0
    curve, weights = [], {}
    turnover_rows = []
    for day in dates:
        if weights:
            day_r = 0.0
            for code, w in weights.items():
                r = ret.get((day, code))
                if r is not None and not np.isnan(r):
                    day_r += w * r
            equity *= (1.0 + day_r)
            if day_r != 0:
                weights = {c: w * (1.0 + (ret.get((day, c)) or 0.0)) / (1.0 + day_r)
                           for c, w in weights.items()}
        curve.append((day, equity))

        do_rebalance = (day in reb_set) if mode == "strategy" else (not weights and day in reb_set)
        if not do_rebalance or day == dates[-1]:
            continue
        available = [c for c in POOL if ret.get((day, c)) is not None]
        if not available:
            continue
        weight = min(1.0 / len(available), MAX_WEIGHT) * COST_BUFFER
        target = {c: weight for c in available}
        all_codes = set(weights) | set(target)
        commission_cost = 0.0
        traded = 0.0
        n_orders = 0
        for code in all_codes:
            amount = abs(target.get(code, 0.0) - weights.get(code, 0.0)) * equity
            if amount <= 1e-9:
                continue
            traded += amount
            n_orders += 1
            commission_cost += max(amount * COMMISSION, MIN_COMMISSION)
        cost = commission_cost + traded * SLIPPAGE
        equity -= cost
        delta = sum(abs(target.get(c, 0.0) - weights.get(c, 0.0)) for c in all_codes)
        turnover_rows.append({"rebalance_date": day, "turnover": delta / 2.0, "n_orders": n_orders,
                              "n_target": len(target), "equity_before_cost": equity + cost})
        weights = target
    stats = {"turnover_avg": float(np.mean([r["turnover"] for r in turnover_rows])) if turnover_rows else np.nan,
             "turnover_cum": float(np.sum([r["turnover"] for r in turnover_rows])),
             "rebalance_count": len(turnover_rows), "hold_avg": float(len(POOL)),
             "turnover_detail": pd.DataFrame(turnover_rows)}
    return pd.Series(dict(curve)), stats


def metrics(curve: pd.Series) -> dict:
    curve = curve.dropna()
    n = len(curve)
    years = n / 252.0
    total = curve.iloc[-1] / curve.iloc[0] - 1.0
    ann = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    r = curve.pct_change().dropna()
    vol = r.std() * np.sqrt(252)
    dd = (curve / curve.cummax() - 1.0).min()
    monthly = curve.resample("ME").last().pct_change().dropna()
    return {"days": n, "total_return": total, "annual_return": ann, "vol": vol,
            "sharpe": (ann - CASH_ANNUAL) / vol if vol and vol > 1e-9 else np.nan,
            "sharpe_raw": ann / vol if vol and vol > 1e-9 else np.nan,
            "max_drawdown": dd, "calmar": ann / abs(dd) if dd < 0 else np.nan,
            "month_win_rate": float((monthly > 0).mean()) if len(monthly) else np.nan}


def main() -> int:
    global COMMISSION, MIN_COMMISSION, SLIPPAGE
    ap = argparse.ArgumentParser(description="ETF 池等权本地回测")
    ap.add_argument("--commission", type=float, default=COMMISSION)
    ap.add_argument("--slippage", type=float, default=SLIPPAGE)
    ap.add_argument("--min-commission", type=float, default=MIN_COMMISSION)
    ap.add_argument("--tag", default="conservative")
    ap.add_argument("--execution-timing", default="same_close", choices=["same_close", "next_open"],
                    help="执行时点：same_close=信号与成交同为 T 日收盘（历史默认）；next_open=信号 T 日收盘、T+1 开盘执行（仅标签）")
    args = ap.parse_args()
    COMMISSION, SLIPPAGE, MIN_COMMISSION = args.commission, args.slippage, args.min_commission
    log("情景 {}: 佣金={:.7f} 滑点={:.4f} 最低佣金={:.1f} 元".format(
        args.tag, COMMISSION, SLIPPAGE, MIN_COMMISSION))

    px = load_prices()
    ret = {}
    for day, code, close in zip(px["date"], px["code"], px["close"]):
        ret.setdefault(code, {})[day] = close
    pct = {}
    for code, series in ret.items():
        s = pd.Series(series).sort_index()
        p = s.pct_change()
        for d, v in p.items():
            pct[(d, code)] = v
    all_days = sorted(px["date"].unique())
    reb_all = set(quarter_rebalance_dates(all_days))
    log("行情 {:,} 行 / {} 只 / {} ~ {}；季度调仓日 {} 个".format(
        len(px), px["code"].nunique(), all_days[0], all_days[-1], len(reb_all)))

    curves, rows, details = {}, [], {}
    for seg, start, end in SEGMENTS:
        dates = [d for d in all_days if start <= d <= end]
        if len(dates) < 30:
            continue
        for mode in ("strategy", "pool"):
            curve, stats = run_one(pct, dates, reb_all, mode)
            curve.index = pd.to_datetime(curve.index)
            curves["{}_{}".format(seg, mode)] = curve
            m = metrics(curve)
            m.update({"segment": seg, "mode": mode, **stats})
            if mode == "strategy":
                details[seg] = stats.get("turnover_detail")
            if mode == "strategy":
                cash = pd.Series(100000.0 * (1.0 + CASH_ANNUAL) ** (np.arange(len(dates)) / 252.0),
                                 index=pd.to_datetime(dates))
                curves["{}_cash".format(seg)] = cash
                rows.append({"segment": seg, "mode": "cash", **metrics(cash)})
            rows.append({"segment": seg, "mode": mode, "days": m["days"], "total_return": m["total_return"],
                         "annual_return": m["annual_return"], "vol": m["vol"], "sharpe": m["sharpe"],
                         "sharpe_raw": m["sharpe_raw"], "max_drawdown": m["max_drawdown"],
                         "calmar": m["calmar"], "month_win_rate": m["month_win_rate"],
                         "turnover_avg": m.get("turnover_avg"), "turnover_cum": m.get("turnover_cum"),
                         "rebalance_count": m.get("rebalance_count"), "hold_avg": m.get("hold_avg")})

    eq = pd.concat(curves, axis=1)
    eq = eq.apply(lambda col: col / col.dropna().iloc[0] if col.notna().any() else col)
    eq.to_parquet(OUT / "equity_curves.parquet")
    metrics_df = pd.DataFrame(rows)
    metrics_df.to_parquet(OUT / "metrics_{}.parquet".format(args.tag), index=False)
    metrics_df.to_parquet(OUT / "metrics.parquet", index=False)
    for seg, det in details.items():
        if isinstance(det, pd.DataFrame) and not det.empty:
            det.to_parquet(OUT / "turnover_{}_{}.parquet".format(args.tag, seg), index=False)
    import hashlib
    import json
    from datetime import datetime, timezone
    def _digest(p):
        h = hashlib.sha256()
        with Path(p).open("rb") as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    _inp = CACHE / "etf_daily.parquet"
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(), "tag": args.tag,
        "input_files": {"etf_daily.parquet": {"path": str(_inp.resolve()), "sha256": _digest(_inp)}},
        "code_sha256": _digest(Path(__file__)),
        "execution_timing": args.execution_timing,
        "execution_note": "引擎只支持收盘成交（信号日=执行日）；next_open 仅为标签/位移近似，未实现开盘成交逻辑",
        "commission": COMMISSION, "slippage": SLIPPAGE, "min_commission": MIN_COMMISSION,
        "max_weight": MAX_WEIGHT, "cost_buffer": COST_BUFFER,
        "pool": POOL, "quarter_months": list(QUARTER_MONTHS),
        "segments": [[s, a, b] for s, a, b in SEGMENTS],
    }
    (OUT / "manifest_{}.json".format(args.tag)).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log("净值曲线写出: {} 列={}".format("equity_curves.parquet", list(eq.columns)))
    for seg, _, _ in SEGMENTS:
        block = metrics_df[metrics_df["segment"] == seg]
        if block.empty:
            continue
        log("\n=== {} ===".format(seg))
        show = block[["mode", "days", "total_return", "annual_return", "vol", "sharpe_raw",
                      "max_drawdown", "calmar", "turnover_avg", "rebalance_count"]].copy()
        for c in ("total_return", "annual_return", "vol", "max_drawdown", "turnover_avg"):
            show[c] = show[c].map(lambda v: "{:.2%}".format(v) if pd.notna(v) else "NA")
        for c in ("sharpe_raw", "calmar"):
            show[c] = show[c].map(lambda v: "{:.3f}".format(v) if pd.notna(v) else "NA")
        log(show.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
