# -*- coding: utf-8 -*-
r"""验证 F-score 计算层只处理 ANNUAL 期，并对比 ANNUAL-only 与全期混算的结果。

用法：
    python verify_annual_filter.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))

from factors.f_score import fscore_aggregator_v2, fscore_components_v2  # noqa: E402
from factors.f_score import panel_schema  # noqa: E402
from factors.f_score import f7_proxy_calculator_v2  # noqa: E402
from factors.f_score import asof_filter  # noqa: E402
from factors.step5_6_fscore.fscore_panel_builder import FScorePanelBuilder  # noqa: E402
from factors.step5_6_fscore.prior_period_resolver import resolve_prior_period  # noqa: E402

PANEL = LAB / "output" / "factor_values" / "fscore_panel_sample.parquet"
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
        if not key.startswith("financial."):
            continue
        _, field, sub = key.split(".", 2)
        financial.setdefault(field, {})[sub] = value
    row = {
        "symbol": flat["symbol"],
        "rebalance_date": flat["rebalance_date"],
        "period_kind": flat["period_kind"],
        "period_end_date": flat["period_end_date"],
        "financial": financial,
        "price": {"close_raw": flat.get("price.close_raw"), "close_raw_date": flat.get("price.close_raw_date")},
        "asof": {k.split(".", 1)[1]: flat.get(k) for k in flat if k.startswith("asof.")},
        "row_status": flat["row_status"],
        "drop_reasons": as_list(flat.get("drop_reasons")),
        "fail_closed_conditions": as_list(flat.get("fail_closed_conditions")),
        "pit_status": flat.get("pit_status"),
        "partial_score": flat.get("partial_score"),
    }
    for entry in row["financial"].values():
        for k, v in list(entry.items()):
            if isinstance(v, float) and pd.isna(v):
                entry[k] = None
    return row


def compute_fscores(rows: list[dict]) -> list[dict]:
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
        available = [int(components[n]["value"]) for n in SCORING
                     if components[n]["status"] == "AVAILABLE" and components[n]["value"] is not None]
        partial = (sum(available) / len(available)) if (not aggregate["complete"] and available) else None
        out.append({"symbol": current["symbol"], "rebalance_date": date, "period_kind": current["period_kind"],
                    "prior_period": prior.period, "prior_reason": prior.reason,
                    "components": components, "aggregate": aggregate, "proxy": proxy,
                    "partial_score": partial})
    return out


def summarize(label: str, results: list[dict]) -> dict:
    agg = [r["aggregate"] for r in results]
    complete = [a for a in agg if a["complete"]]
    scores = Counter(a["total_score_8"] for a in complete)
    return {
        "label": label,
        "rows": len(results),
        "complete": len(complete),
        "complete_ratio": (len(complete) / len(results)) if results else 0.0,
        "score_counts": dict(sorted(scores.items())),
        "mean": (sum(scores.elements()) / len(complete)) if complete else None,
        "unavailable_reasons": dict(Counter(reason for a in agg for reason in a["incomplete_reasons"]).most_common(6)),
    }


def main() -> int:
    frame = pd.read_parquet(PANEL)
    rows = [unflatten(r) for r in frame.to_dict("records")]
    print("panel 行数: {}  period_kind 分布: {}".format(len(rows), dict(Counter(r["period_kind"] for r in rows))))

    annual = [r for r in rows if r["period_kind"] == "ANNUAL"]
    print("ANNUAL 行数: {}（{:.2%}）".format(len(annual), len(annual) / len(rows)))
    print("")

    direct_annual = compute_fscores(annual)
    direct_all = compute_fscores(rows)
    for label, results in (("ANNUAL-only（修复后口径）", direct_annual), ("全期混算（修复前口径）", direct_all)):
        s = summarize(label, results)
        print("=" * 78)
        print(s["label"])
        print("  参与计算行数:", s["rows"], " complete:", s["complete"], " 比例: {:.2%}".format(s["complete_ratio"]))
        print("  total_score_8 分布:", s["score_counts"])
        print("  平均分:", None if s["mean"] is None else round(s["mean"], 3))
        print("  未完成原因 Top:", s["unavailable_reasons"])

    print("")
    print("=" * 78)
    print("FScorePanelBuilder 过滤验证（输入混合期别，看输出是否只剩 ANNUAL）")
    tmpdir = LAB / "output" / "_tmp_verify"
    tmpdir.mkdir(parents=True, exist_ok=True)
    payload = {"rows": [dict(r, derived_row_status="OK") for r in rows]}
    panel_json = tmpdir / "panel.json"
    panel_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stats_json = tmpdir / "stats.json"
    stats_json.write_text(json.dumps({"drop_reasons_breakdown": {"SAME_KEY_DIFFERENT_VALUE": 0}}), encoding="utf-8")
    builder = FScorePanelBuilder(derived_panel_path=panel_json, step5_4_stats_path=stats_json)
    output, stats, _, _ = builder.build()
    print("  输入行数:", stats["input_derived_rows"], " 其中 ANNUAL:", stats["scoring_rows"],
          " 被排除的非 ANNUAL:", stats["excluded_non_annual_rows"])
    print("  输出行数:", len(output), " 输出期别集合:", sorted({r["period_kind"] for r in output}))
    print("  过滤正确:", len(output) == len(annual) and {r["period_kind"] for r in output} == {"ANNUAL"})
    print("  pit_status 分布:", dict(Counter(r["pit_status"] for r in output)))
    complete_out = [r for r in output if r["aggregated"]["complete"]]
    print("  complete=True:", len(complete_out), " 比例: {:.2%}".format(len(complete_out) / len(output)))
    partial = [r for r in output if r["partial_score"] is not None]
    print("  partial_score 非空行数:", len(partial),
          " 样例:", (round(partial[0]["partial_score"], 3) if partial else None))
    print("  样例行:", json.dumps({k: output[0][k] for k in ("symbol", "rebalance_date", "period_kind",
                                                             "period_end_date", "prior_period_end_date")},
                                  ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())