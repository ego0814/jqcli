# -*- coding: utf-8 -*-
r"""对比 ANNUAL 选择逻辑修复前后的 F-score 覆盖情况。

A. 修复前：适配层取"任意期别最新可见"，ANNUAL 只在窄窗口被选中
B. 修复后：适配层取"每个 rebalance 日最新可见的 ANNUAL"
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

from factors.f_score import fscore_aggregator_v2, fscore_components_v2  # noqa: E402
from factors.f_score import f7_proxy_calculator_v2  # noqa: E402
from factors.step5_6_fscore.prior_period_resolver import resolve_prior_period  # noqa: E402

OUT = LAB / "output" / "factor_values"
SCORING = list(fscore_components_v2.SCORING_COMPONENTS)
CURRENT_ONLY = {"F1", "F2", "F4"}


def as_list(value) -> list:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


def unflatten(flat: dict) -> dict:
    financial = {}
    for key, value in flat.items():
        if key.startswith("financial."):
            _, field, sub = key.split(".", 2)
            financial.setdefault(field, {})[sub] = None if (isinstance(value, float) and pd.isna(value)) else value
    return {
        "symbol": flat["symbol"], "rebalance_date": flat["rebalance_date"],
        "period_kind": flat["period_kind"], "period_end_date": flat["period_end_date"],
        "financial": financial,
        "price": {"close_raw": flat.get("price.close_raw")},
        "asof": {k.split(".", 1)[1]: flat.get(k) for k in flat if k.startswith("asof.")},
        "row_status": flat["row_status"], "drop_reasons": as_list(flat.get("drop_reasons")),
    }


def compute(rows: list[dict]) -> list[dict]:
    index = {}
    for row in rows:
        index.setdefault((row["symbol"], str(row["period_end_date"])[:10]), []).append(row)
    out = []
    for current in rows:
        prior = resolve_prior_period(current, index)
        date = current["rebalance_date"]
        components = {}
        for name in SCORING:
            fn = fscore_components_v2.COMPONENT_FUNCTIONS[name]
            components[name] = fn(current, date) if name in CURRENT_ONLY else fn(current, prior.row, date)
        proxy = f7_proxy_calculator_v2.calculate_f7_proxy(current, prior.row, date)
        aggregate = fscore_aggregator_v2.aggregate_components(components)
        out.append({"symbol": current["symbol"], "rebalance_date": date,
                    "complete": aggregate["complete"], "score": aggregate["total_score_8"],
                    "reasons": aggregate["incomplete_reasons"]})
    return out


def report(label: str, frame: pd.DataFrame, rows: list[dict], results: list[dict]) -> dict:
    complete = [r for r in results if r["complete"]]
    scores = Counter(r["score"] for r in complete)
    months = Counter(r["rebalance_date"] for r in results)
    months_c = Counter(r["rebalance_date"] for r in complete)
    print("=" * 78)
    print(label)
    print("  panel 行数: {}  period_kind 分布: {}".format(len(rows), dict(Counter(r["period_kind"] for r in rows))))
    print("  row_status: {}".format(dict(Counter(r["row_status"] for r in rows))))
    drop_reasons = Counter(reason for r in rows for reason in r["drop_reasons"])
    print("  drop_reasons: {}".format(dict(drop_reasons)))
    if "period_end_date" in frame.columns:
        years = Counter(str(y)[:4] for y in frame["period_end_date"].dropna())
        print("  period_end_date 年份分布:", dict(sorted(years.items())))
    print("  参与 F-score 行数: {}".format(len(results)))
    print("  complete=True: {} ({:.2%})".format(len(complete), len(complete) / len(results) if results else 0))
    print("  total_score_8 分布:", dict(sorted(scores.items())))
    print("  平均分:", round(sum(scores.elements()) / len(complete), 3) if complete else None)
    print("  月度覆盖: 有 F-score 的 rebalance 日 {} 个，平均每日 {:.1f} 只，最少 {} 只，最多 {} 只".format(
        len(months_c), (sum(months_c.values()) / len(months_c)) if months_c else 0,
        min(months_c.values()) if months_c else 0, max(months_c.values()) if months_c else 0))
    print("  未完成原因 Top:", dict(Counter(r for x in results for r in x["reasons"]).most_common(6)))
    return {"rows": len(rows), "scored": len(results), "complete": len(complete),
            "ratio": len(complete) / len(results) if results else 0, "months": len(months_c)}


def main() -> int:
    old_file = OUT / "fscore_panel_sample_allperiods.parquet"
    new_file = OUT / "fscore_panel_sample.parquet"

    old_rows = [unflatten(r) for r in pd.read_parquet(old_file).to_dict("records")]
    old_annual = [r for r in old_rows if r["period_kind"] == "ANNUAL"]
    old_res = compute(old_annual)
    a = report("A. 修复前（任意期别最新）→ 仅取其中 ANNUAL 行", pd.DataFrame([{"period_end_date": r["period_end_date"]} for r in old_annual]), old_annual, old_res)

    new_frame = pd.read_parquet(new_file)
    new_rows = [unflatten(r) for r in new_frame.to_dict("records")]
    ok_rows = [r for r in new_rows if r["row_status"] == "OK"]
    new_res = compute(ok_rows)
    b = report("B. 修复后（每月取最新可见 ANNUAL）", new_frame, ok_rows, new_res)

    print("")
    print("=" * 78)
    print("对比汇总")
    print("  panel ANNUAL 行数: {} -> {}（{:.1f}x）".format(a["rows"], b["rows"], b["rows"] / a["rows"]))
    print("  F-score 可算行数: {} -> {}（{:.1f}x）".format(a["scored"], b["scored"], b["scored"] / a["scored"]))
    print("  complete=True: {} -> {}（比例 {:.2%} -> {:.2%}）".format(a["complete"], b["complete"], a["ratio"], b["ratio"]))
    print("  有 F-score 的月份数: {} -> {}".format(a["months"], b["months"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())