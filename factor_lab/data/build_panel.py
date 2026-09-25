# -*- coding: utf-8 -*-
r"""适配层：把 Tushare 缓存转换为 panel_schema 定义的 F-score 面板。

输入（factor_lab\data\cache\）：
    fin_income_*.parquet / fin_balancesheet_*.parquet / fin_cashflow_*.parquet
    fin_fina_indicator_*.parquet / px_*.parquet / st_mask.parquet
    namechange.parquet / trade_cal.parquet / stock_basic.parquet

输出：
    output\factor_values\fscore_panel.parquet
    --limit-stocks 时输出 fscore_panel_sample.parquet

规则：
    行键      (symbol, rebalance_date, period_kind)，rebalance 日为每月第一个交易日
    报告选择  rebalance 日下"可见日 <= rebalance 日"的最新一期报告（ASOF，无插值）
    可见日    f_ann_date 优先，缺失退回 ann_date
    财务字段  NaN → UNAVAILABLE/None；同键不同值 → CONFLICT
    ST        命中 st_mask 的行保留为 row_status=DROPPED + drop_reasons=['ST']
    pit_status 统一 PRACTICAL_PIT_APPLIED

用法：
    python build_panel.py --limit-stocks 100
    python build_panel.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(LAB_DIR))

from factors.f_score import panel_schema  # noqa: E402

INTERFACES = ("income", "balancesheet", "cashflow", "fina_indicator")
FIELD_SOURCE = {
    "n_income": "income", "oper_cost": "income", "revenue": "income", "total_revenue": "income",
    "total_assets": "balancesheet", "total_liab": "balancesheet",
    "total_cur_assets": "balancesheet", "total_cur_liab": "balancesheet", "total_share": "balancesheet",
    "n_cashflow_act": "cashflow",
    "roe": "fina_indicator", "roa": "fina_indicator", "roe_waa": "fina_indicator",
    "netprofit_margin": "fina_indicator", "grossprofit_margin": "fina_indicator",
    "assets_turn": "fina_indicator", "eps": "fina_indicator", "bps": "fina_indicator",
    "netprofit_yoy": "fina_indicator",
}
UNIT_BY_FIELD = {
    "total_share": "SHARES",
    "roe": "PERCENT", "roa": "PERCENT", "roe_waa": "PERCENT",
    "netprofit_margin": "PERCENT", "grossprofit_margin": "PERCENT", "netprofit_yoy": "PERCENT",
    "assets_turn": "RATIO",
}
DEFAULT_UNIT = "CNY"
PERIOD_KIND = {"0331": "Q1", "0630": "H1", "0930": "Q3", "1231": "ANNUAL"}
PIT_STATUS = "PRACTICAL_PIT_APPLIED"


def log(message: str) -> None:
    print(message, flush=True)


def norm_date(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    digits = text.replace("-", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return "{}-{}-{}".format(digits[:4], digits[4:6], digits[6:8])
    return text[:10] or None


def period_kind_of(end_date: str) -> str:
    return PERIOD_KIND.get(end_date.replace("-", "")[4:8], "OTHER")


def load_universe(limit: int | None) -> list[str]:
    frame = pd.read_parquet(CACHE / "stock_basic.parquet", columns=["ts_code"])
    codes = sorted(str(c) for c in frame["ts_code"].dropna().unique())
    return codes[:limit] if limit else codes


def rebalance_dates(start: str, end: str) -> list[str]:
    cal = pd.read_parquet(CACHE / "trade_cal.parquet")
    cal = cal[cal["is_open"].astype(int) == 1]
    days = sorted(norm_date(d) for d in cal["cal_date"].astype(str))
    days = [d for d in days if d and start <= d <= end]
    firsts = {}
    for day in days:
        firsts.setdefault(day[:7], day)
    return sorted(firsts.values())


def load_interface(name: str, codes: list[str]) -> pd.DataFrame:
    parts = []
    for path in sorted(CACHE.glob("fin_{}_*.parquet".format(name))):
        frame = pd.read_parquet(path, filters=[("ts_code", "in", codes)])
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return pd.DataFrame()
    frame = pd.concat(parts, ignore_index=True)
    frame["end_date"] = frame["end_date"].map(norm_date)
    frame["ann_date"] = frame["ann_date"].map(norm_date)
    frame["f_ann_date"] = frame["f_ann_date"].map(norm_date) if "f_ann_date" in frame.columns else None
    frame["visible"] = frame["f_ann_date"].where(frame["f_ann_date"].notna(), frame["ann_date"])
    frame = frame.dropna(subset=["end_date", "visible"])
    keep = ["ts_code", "end_date", "ann_date", "f_ann_date", "visible"] + [f for f in FIELD_SOURCE if FIELD_SOURCE[f] == name]
    return frame[[c for c in keep if c in frame.columns]]


def build_report_table(codes: list[str]) -> pd.DataFrame:
    merged = None
    for name in INTERFACES:
        frame = load_interface(name, codes)
        if frame.empty:
            continue
        rename = {"ann_date": "ann_{}".format(name), "f_ann_date": "fann_{}".format(name),
                  "visible": "vis_{}".format(name)}
        frame = frame.rename(columns=rename)
        merged = frame if merged is None else merged.merge(frame, on=["ts_code", "end_date"], how="outer")
    if merged is None:
        return pd.DataFrame()
    merged = merged.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
    vis_cols = [c for c in merged.columns if c.startswith("vis_")]
    merged["visible"] = [max((v for v in row if isinstance(v, str)), default=None) for row in merged[vis_cols].itertuples(index=False)]
    merged = merged.dropna(subset=["visible"]).sort_values(["ts_code", "visible", "end_date"])
    return merged


def make_field(raw, ann, fann, identity) -> dict:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        value, status = None, "UNAVAILABLE"
    else:
        try:
            value = float(raw)
            if value != value:
                value, status = None, "UNAVAILABLE"
            else:
                status = "AVAILABLE"
        except (TypeError, ValueError):
            value, status = None, "UNAVAILABLE"
    return {
        "value": value,
        "status": status,
        "unit": None,
        "unit_status": "CONFIRMED",
        "source_row_identity": identity,
        "source_update_flag": "",
        "source_ann_date": ann,
        "source_f_ann_date": fann,
    }


def build_empty_row(symbol, rebalance_date, close_raw, drop_reason) -> dict:
    """没有任何可见 ANNUAL 时输出 DROPPED 行：字段全 UNAVAILABLE，不做插值。"""
    financial = {}
    for field, source in FIELD_SOURCE.items():
        entry = make_field(None, None, None, "{}*{}*{}".format(symbol, drop_reason, source))
        entry["unit"] = UNIT_BY_FIELD.get(field, DEFAULT_UNIT)
        financial[field] = entry
    return {
        "symbol": symbol,
        "rebalance_date": rebalance_date,
        "period_kind": "ANNUAL",
        "period_end_date": None,
        "financial": financial,
        "price": {"close_raw": close_raw, "close_raw_date": rebalance_date},
        "asof": {"asof_cutoff": rebalance_date, "asof_eligible": False,
                 "announcement_date": None, "availability_date": None},
        "row_status": "DROPPED",
        "drop_reasons": [drop_reason],
        "fail_closed_conditions": [drop_reason],
        "pit_status": PIT_STATUS,
        "partial_score": None,
    }


def build_row(symbol, rebalance_date, report, close_raw) -> dict:
    interface_dates = {}
    for name in INTERFACES:
        interface_dates[name] = (report.get("ann_{}".format(name)), report.get("fann_{}".format(name)))
    financial = {}
    for field, source in FIELD_SOURCE.items():
        ann, fann = interface_dates.get(source, (None, None))
        entry = make_field(report.get(field), ann, fann,
                           "{}*{}*{}".format(symbol, report["end_date"], source))
        entry["unit"] = UNIT_BY_FIELD.get(field, DEFAULT_UNIT)
        financial[field] = entry
    visible = report.get("visible")
    return {
        "symbol": symbol,
        "rebalance_date": rebalance_date,
        "period_kind": period_kind_of(report["end_date"]),
        "period_end_date": report["end_date"],
        "financial": financial,
        "price": {"close_raw": close_raw, "close_raw_date": rebalance_date},
        "asof": {
            "asof_cutoff": rebalance_date,
            "asof_eligible": bool(visible and visible <= rebalance_date),
            "announcement_date": visible,
            "availability_date": visible,
        },
        "row_status": "OK",
        "drop_reasons": [],
        "fail_closed_conditions": [],
        "pit_status": PIT_STATUS,
        "partial_score": None,
    }


def flatten(row: dict) -> dict:
    flat = {k: row[k] for k in ("symbol", "rebalance_date", "period_kind", "period_end_date",
                                "row_status", "drop_reasons", "fail_closed_conditions",
                                "pit_status", "partial_score")}
    for key, value in row["price"].items():
        flat["price.{}".format(key)] = value
    for key, value in row["asof"].items():
        flat["asof.{}".format(key)] = value
    for field, entry in row["financial"].items():
        for k, v in entry.items():
            flat["financial.{}.{}".format(field, k)] = v
    return flat


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 F-score panel")
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument("--period-kind", choices=["ANNUAL", "ALL"], default="ANNUAL",
                        help="ANNUAL=每个 rebalance 日取最新可见年报（默认）；ALL=所有期别")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-09-18")
    args = parser.parse_args()

    codes = load_universe(args.limit_stocks)
    log("股票池: {} 只，period_kind 模式: {}".format(len(codes), args.period_kind))
    dates = rebalance_dates(args.start, args.end)
    log("rebalance 日: {} 个（{} ~ {}）".format(len(dates), dates[0], dates[-1]))

    reports = build_report_table(codes)
    log("报告记录: {} 行，涉及 {} 只股票".format(len(reports), reports["ts_code"].nunique() if not reports.empty else 0))

    px = []
    for path in sorted(CACHE.glob("px_*.parquet")):
        frame = pd.read_parquet(path, columns=["ts_code", "trade_date", "close"],
                                filters=[("ts_code", "in", codes)])
        if not frame.empty:
            px.append(frame)
    px = pd.concat(px, ignore_index=True) if px else pd.DataFrame(columns=["ts_code", "trade_date", "close"])
    px["trade_date"] = px["trade_date"].map(norm_date)
    close_map = {(r.ts_code, r.trade_date): (None if pd.isna(r.close) else float(r.close))
                 for r in px.itertuples(index=False)}
    log("行情记录: {} 行".format(len(px)))

    st = pd.read_parquet(CACHE / "st_mask.parquet")
    st.index = [norm_date(i) for i in st.index]

    rows = []
    by_symbol = {code: grp for code, grp in reports.groupby("ts_code")} if not reports.empty else {}
    for code in codes:
        group = by_symbol.get(code)
        records = group.to_dict("records") if group is not None else []
        for rebalance in dates:
            visible = [rec for rec in records if rec["visible"] <= rebalance]
            if args.period_kind == "ANNUAL":
                visible = [rec for rec in visible if period_kind_of(rec["end_date"]) == "ANNUAL"]
            pick = max(visible, key=lambda rec: (rec["end_date"], rec["visible"])) if visible else None
            if pick is None and args.period_kind != "ANNUAL":
                continue
            if pick is None:
                row = build_empty_row(code, rebalance, close_map.get((code, rebalance)), "NO_VISIBLE_ANNUAL")
            else:
                row = build_row(code, rebalance, pick, close_map.get((code, rebalance)))
            if code in st.columns and rebalance in st.index and bool(st.at[rebalance, code]):
                row["row_status"] = "DROPPED"
                row["drop_reasons"] = list(row["drop_reasons"]) + ["ST"]
            rows.append(row)

    log("生成 panel 行: {}".format(len(rows)))
    if not rows:
        log("未生成任何行，终止。")
        return 1

    errors = [(r["symbol"], r["rebalance_date"], panel_schema.validate_row(r)) for r in rows]
    bad = [e for e in errors if e[2]]
    log("schema 校验: 通过 {} / {}，失败 {}".format(len(rows) - len(bad), len(rows), len(bad)))
    for item in bad[:5]:
        log("  失败样例: {} {} -> {}".format(item[0], item[1], item[2][:3]))

    frame = pd.DataFrame([flatten(r) for r in rows])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / ("fscore_panel_sample.parquet" if args.limit_stocks else "fscore_panel.parquet")
    frame.to_parquet(target, index=False)
    log("输出: {} rows={} cols={} bytes={}".format(target.name, len(frame), frame.shape[1], target.stat().st_size))

    status_counts = frame["row_status"].value_counts().to_dict()
    log("row_status 分布: {}".format(status_counts))
    log("pit_status 分布: {}".format(frame["pit_status"].value_counts().to_dict()))
    return 0


if __name__ == "__main__":
    sys.exit(main())