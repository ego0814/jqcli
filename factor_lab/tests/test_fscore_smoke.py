# -*- coding: utf-8 -*-
r"""F-score v2 迁移冒烟测试。

全部输入在内存构造，不读取磁盘 parquet / JSON 数据。

运行：
    D:\project\jqcli\factor_lab\.venv\Scripts\python.exe tests\test_fscore_smoke.py

覆盖：主路径（A/B/C 三只股票）、ASOF 边界、失败关闭优先级、除零保护。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factors.f_score import asof_filter  # noqa: E402
from factors.f_score import f7_proxy_calculator_v2  # noqa: E402
from factors.f_score import fscore_aggregator_v2  # noqa: E402
from factors.f_score import fscore_components_v2  # noqa: E402
from factors.f_score import panel_schema  # noqa: E402
from factors.step5_6_fscore.prior_period_resolver import resolve_prior_period  # noqa: E402
from factors.step5_6_fscore.fscore_aggregator import score_distribution  # noqa: E402

SCORING = list(fscore_components_v2.SCORING_COMPONENTS)
CURRENT_ONLY = {"F1", "F2", "F4"}
ALL_FIELDS = list(panel_schema.PANEL_FIELDS)

ANN_PRIOR = "2023-04-20"
ANN_CURRENT = "2024-04-25"
REB_PRIOR = "2023-05-02"
REB_CURRENT = "2024-05-06"

FAILURES: list[str] = []
CHECKS = 0


def record(case: str, item: str, expected, actual) -> None:
    global CHECKS
    CHECKS += 1
    ok = expected == actual
    print("[{}] {} | {} | 预期={!r} 实际={!r}".format("PASS" if ok else "FAIL", case, item, expected, actual))
    if not ok:
        FAILURES.append("{} | {} | 预期={!r} 实际={!r}".format(case, item, expected, actual))


def field(value, ann, status="AVAILABLE", identity="ROW"):
    return {
        "value": value,
        "status": status,
        "unit": "CNY",
        "unit_status": "CONFIRMED",
        "source_row_identity": identity,
        "source_update_flag": "UNCHANGED",
        "source_ann_date": ann,
        "source_f_ann_date": ann,
    }


def make_row(symbol, rebalance_date, period_end_date, ann, values, overrides=None):
    financial = {}
    for name in ALL_FIELDS:
        financial[name] = field(values.get(name, 0.0), ann, identity="{}-{}-{}".format(symbol, period_end_date, name))
    for name, patch in (overrides or {}).items():
        financial[name].update(patch)
    return {
        "symbol": symbol,
        "rebalance_date": rebalance_date,
        "period_kind": "ANNUAL",
        "period_end_date": period_end_date,
        "financial": financial,
        "price": {"close_raw": 10.0, "close_raw_date": rebalance_date},
        "asof": {
            "asof_cutoff": rebalance_date,
            "asof_eligible": True,
            "announcement_date": ann,
            "availability_date": ann,
        },
        "row_status": "OK",
        "drop_reasons": [],
        "fail_closed_conditions": [],
    }


def build_index(rows):
    index = {}
    for r in rows:
        index.setdefault((r["symbol"], str(r["period_end_date"])[:10]), []).append(r)
    return index


def evaluate(current, rows):
    index = build_index(rows)
    prior = resolve_prior_period(current, index)
    prior_row = prior.row
    date = current["rebalance_date"]
    components = {}
    for name in SCORING:
        fn = fscore_components_v2.COMPONENT_FUNCTIONS[name]
        if name in CURRENT_ONLY:
            components[name] = fn(current, date)
        else:
            components[name] = fn(current, prior_row, date)
    proxy = f7_proxy_calculator_v2.calculate_f7_proxy(current, prior_row, date)
    aggregate = fscore_aggregator_v2.aggregate_components(components)
    return prior, components, proxy, aggregate


def fscore_row(current, prior, components, proxy, aggregate):
    return {
        "symbol": current["symbol"],
        "rebalance_date": current["rebalance_date"],
        "period_kind": current["period_kind"],
        "period_end_date": current["period_end_date"],
        "prior_period_end_date": prior.period,
        "components": components,
        "F7_PROXY": proxy,
        "aggregated": aggregate,
        "reference_fscore_components": {},
        "cross_check_divergence_count": 0,
        "provenance": {},
    }


A_CURRENT = {"n_income": 100.0, "n_cashflow_act": 150.0, "total_assets": 1000.0, "total_liab": 400.0,
             "total_cur_assets": 500.0, "total_cur_liab": 250.0, "revenue": 800.0, "oper_cost": 500.0,
             "total_share": 100.0}
A_PRIOR = {"n_income": 80.0, "n_cashflow_act": 90.0, "total_assets": 900.0, "total_liab": 450.0,
           "total_cur_assets": 400.0, "total_cur_liab": 250.0, "revenue": 700.0, "oper_cost": 490.0,
           "total_share": 100.0}
B_CURRENT = {"n_income": -50.0, "n_cashflow_act": -60.0, "total_assets": 1000.0, "total_liab": 600.0,
             "total_cur_assets": 300.0, "total_cur_liab": 300.0, "revenue": 600.0, "oper_cost": 500.0,
             "total_share": 120.0}
B_PRIOR = {"n_income": 20.0, "n_cashflow_act": 40.0, "total_assets": 800.0, "total_liab": 400.0,
           "total_cur_assets": 400.0, "total_cur_liab": 250.0, "revenue": 500.0, "oper_cost": 400.0,
           "total_share": 100.0}
C_CURRENT = {"n_income": 10.0, "n_cashflow_act": 20.0, "total_assets": 500.0, "total_liab": 100.0,
             "total_cur_assets": 200.0, "total_cur_liab": 100.0, "revenue": 300.0, "oper_cost": 150.0,
             "total_share": 50.0}

CASE_MAIN = "用例1-主路径"
CASE_ASOF = "用例2-ASOF边界"
CASE_CONFLICT = "用例3-失败关闭优先级"
CASE_ZERO = "用例4-除零保护"


def case_main():
    a_prior = make_row("000001.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, A_PRIOR)
    a_current = make_row("000001.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, A_CURRENT)
    b_prior = make_row("000002.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, B_PRIOR)
    b_current = make_row("000002.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, B_CURRENT)
    c_current = make_row("000003.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, C_CURRENT)
    rows = [a_prior, a_current, b_prior, b_current, c_current]

    for r in rows:
        record(CASE_MAIN, "panel_schema.validate_row({})".format(r["symbol"]), [], panel_schema.validate_row(r))

    for symbol, current, expected_value in (("A", a_current, 1), ("B", b_current, 0)):
        prior, components, proxy, aggregate = evaluate(current, rows)
        for name in SCORING:
            record(CASE_MAIN, "股票{} {} value".format(symbol, name), expected_value, components[name]["value"])
            record(CASE_MAIN, "股票{} {} status".format(symbol, name), "AVAILABLE", components[name]["status"])
        record(CASE_MAIN, "股票{} total_score_8".format(symbol), expected_value * 8, aggregate["total_score_8"])
        record(CASE_MAIN, "股票{} complete".format(symbol), True, aggregate["complete"])
        record(CASE_MAIN, "股票{} components_available".format(symbol), 8, aggregate["components_available"])
        record(CASE_MAIN, "股票{} components_unavailable".format(symbol), 0, aggregate["components_unavailable"])
        record(CASE_MAIN, "股票{} F7_PROXY value".format(symbol), expected_value, proxy["value"])
        record(CASE_MAIN, "股票{} F7_PROXY not_classic_f7".format(symbol), True, proxy["not_classic_f7"])
        ok = panel_schema.validate_row(current)
        record(CASE_MAIN, "股票{} F7_PROXY label".format(symbol), "F7_PROXY", proxy["label"])
        fsrow = fscore_row(current, prior, components, proxy, aggregate)
        record(CASE_MAIN, "股票{} fscore_schema 校验".format(symbol), [], fscore_components_v2.validate_fscore_row(fsrow))

    prior, components, proxy, aggregate = evaluate(c_current, rows)
    record(CASE_MAIN, "股票C prior.row", None, prior.row)
    record(CASE_MAIN, "股票C prior.reason", "PRIOR_PERIOD_NOT_VISIBLE", prior.reason)
    for name in CURRENT_ONLY:
        record(CASE_MAIN, "股票C {} value".format(name), 1, components[name]["value"])
        record(CASE_MAIN, "股票C {} status".format(name), "AVAILABLE", components[name]["status"])
    for name in ("F3", "F5", "F6", "F8", "F9"):
        record(CASE_MAIN, "股票C {} value".format(name), None, components[name]["value"])
        record(CASE_MAIN, "股票C {} status".format(name), "UNAVAILABLE", components[name]["status"])
    record(CASE_MAIN, "股票C total_score_8", None, aggregate["total_score_8"])
    record(CASE_MAIN, "股票C complete", False, aggregate["complete"])
    record(CASE_MAIN, "股票C components_available", 3, aggregate["components_available"])
    record(CASE_MAIN, "股票C components_unavailable", 5, aggregate["components_unavailable"])
    record(CASE_MAIN, "股票C incomplete_reasons", ["F3", "F5", "F6", "F8", "F9"], aggregate["incomplete_reasons"])
    record(CASE_MAIN, "股票C F7_PROXY value", None, proxy["value"])
    record(CASE_MAIN, "股票C F7_PROXY status", "UNAVAILABLE", proxy["status"])
    fsrow = fscore_row(c_current, prior, components, proxy, aggregate)
    record(CASE_MAIN, "股票C fscore_schema 校验", [], fscore_components_v2.validate_fscore_row(fsrow))


def case_asof():
    d_prior = make_row("000004.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, A_PRIOR)
    d_current = make_row("000004.XSHE", "2024-04-01", "2023-12-31", ANN_CURRENT, A_CURRENT)
    rows = [d_prior, d_current]

    record(CASE_ASOF, "公告日>调仓日 is_asof_eligible", False,
           asof_filter.is_asof_eligible({"ann_date": ANN_CURRENT}, "2024-04-01"))
    record(CASE_ASOF, "公告日>调仓日 asof_reason", "ASOF_INELIGIBLE",
           asof_filter.asof_reason({"ann_date": ANN_CURRENT}, "2024-04-01"))
    record(CASE_ASOF, "缺公告日 asof_reason", "MISSING_ANNOUNCEMENT_DATE",
           asof_filter.asof_reason({}, "2024-04-01"))
    record(CASE_ASOF, "公告日=调仓日 边界", True,
           asof_filter.is_asof_eligible({"ann_date": "2024-04-01"}, "2024-04-01"))
    record(CASE_ASOF, "YYYYMMDD 归一化", True,
           asof_filter.is_asof_eligible({"ann_date": "20240425"}, "2024-04-30"))
    record(CASE_ASOF, "f_ann_date 更早时取 min", True,
           asof_filter.is_asof_eligible({"ann_date": "2024-06-01", "f_ann_date": "2024-04-25"}, "2024-04-30"))

    prior, components, proxy, aggregate = evaluate(d_current, rows)
    record(CASE_ASOF, "prior.row 仍可解析", True, prior.row is not None)
    for name in SCORING:
        record(CASE_ASOF, "{} value".format(name), None, components[name]["value"])
        record(CASE_ASOF, "{} status".format(name), "UNAVAILABLE", components[name]["status"])
    record(CASE_ASOF, "total_score_8", None, aggregate["total_score_8"])
    record(CASE_ASOF, "complete", False, aggregate["complete"])
    record(CASE_ASOF, "components_available", 0, aggregate["components_available"])
    record(CASE_ASOF, "components_unavailable", 8, aggregate["components_unavailable"])


def case_conflict():
    a_prior = make_row("000001.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, A_PRIOR)
    base = {"n_income": A_CURRENT["n_income"]}
    a_current = make_row("000001.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, A_CURRENT,
                         overrides={"n_income": {"status": "CONFLICT"}})
    rows = [a_prior, a_current]

    prior, components, proxy, aggregate = evaluate(a_current, rows)
    record(CASE_CONFLICT, "F1 status", "CONFLICT", components["F1"]["status"])
    record(CASE_CONFLICT, "F1 value", None, components["F1"]["value"])
    record(CASE_CONFLICT, "F3 status", "CONFLICT", components["F3"]["status"])
    record(CASE_CONFLICT, "F3 value", None, components["F3"]["value"])
    record(CASE_CONFLICT, "F4 status", "CONFLICT", components["F4"]["status"])
    record(CASE_CONFLICT, "F4 value", None, components["F4"]["value"])
    record(CASE_CONFLICT, "F2 不受影响 status", "AVAILABLE", components["F2"]["status"])
    record(CASE_CONFLICT, "F2 不受影响 value", 1, components["F2"]["value"])
    record(CASE_CONFLICT, "panel_schema.validate_row", [], panel_schema.validate_row(a_current))
    record(CASE_CONFLICT, "total_score_8", None, aggregate["total_score_8"])
    record(CASE_CONFLICT, "complete", False, aggregate["complete"])
    record(CASE_CONFLICT, "components_conflict", 3, aggregate["components_conflict"])

    invisible_conflict = make_row("000005.XSHE", "2024-04-01", "2023-12-31", "2024-12-31", A_CURRENT,
                                  overrides={"n_income": {"status": "CONFLICT"}})
    prior2, components2, proxy2, aggregate2 = evaluate(invisible_conflict, [a_prior, invisible_conflict])
    record(CASE_CONFLICT, "CONFLICT 优先于不可见 status", "CONFLICT", components2["F1"]["status"])
    record(CASE_CONFLICT, "CONFLICT 优先于不可见 value", None, components2["F1"]["value"])


def case_zero_division():
    a_prior = make_row("000001.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, dict(A_PRIOR, total_assets=0.0))
    a_current = make_row("000001.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, A_CURRENT)
    rows = [a_prior, a_current]

    try:
        prior, components, proxy, aggregate = evaluate(a_current, rows)
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = "{}: {}".format(type(exc).__name__, exc)
        prior = components = proxy = aggregate = None

    record(CASE_ZERO, "未抛异常", None, raised)
    if raised is not None:
        return
    for name in ("F3", "F5", "F9"):
        record(CASE_ZERO, "{} status".format(name), "UNAVAILABLE", components[name]["status"])
        record(CASE_ZERO, "{} value".format(name), None, components[name]["value"])
    for name in ("F1", "F2", "F4", "F6", "F8"):
        record(CASE_ZERO, "{} 仍可用 status".format(name), "AVAILABLE", components[name]["status"])
        record(CASE_ZERO, "{} 仍可用 value".format(name), 1, components[name]["value"])
    record(CASE_ZERO, "total_score_8", None, aggregate["total_score_8"])
    record(CASE_ZERO, "complete", False, aggregate["complete"])
    record(CASE_ZERO, "components_available", 5, aggregate["components_available"])
    record(CASE_ZERO, "components_unavailable", 3, aggregate["components_unavailable"])
    record(CASE_ZERO, "incomplete_reasons", ["F3", "F5", "F9"], aggregate["incomplete_reasons"])


CASE_STREAM = "用例5-streaming对比"


def synthetic_rows(scores):
    rows = []
    for score in scores:
        if score is None:
            rows.append({"aggregated": {"total_score_8": None, "complete": False, "incomplete_reasons": ["F3"]}})
        else:
            rows.append({"aggregated": {"total_score_8": score, "complete": True, "incomplete_reasons": []}})
    return rows


def compare_distributions(case, label, rows, expected):
    batch = score_distribution(rows)
    dist = fscore_aggregator_v2.streaming_distribution()
    for row in rows:
        fscore_aggregator_v2.observe(dist, row["aggregated"])
    stream = fscore_aggregator_v2.finalize(dist)
    for key in ("complete", "incomplete", "mean_score", "median_score", "score_counts"):
        record(case, "{} {} batch vs 预期".format(label, key), expected[key], batch[key])
        record(case, "{} {} streaming vs batch".format(label, key), batch[key], stream[key])


def case_streaming_vs_batch():
    a_prior = make_row("000001.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, A_PRIOR)
    a_current = make_row("000001.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, A_CURRENT)
    b_prior = make_row("000002.XSHE", REB_PRIOR, "2022-12-31", ANN_PRIOR, B_PRIOR)
    b_current = make_row("000002.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, B_CURRENT)
    c_current = make_row("000003.XSHE", REB_CURRENT, "2023-12-31", ANN_CURRENT, C_CURRENT)
    rows = [a_prior, a_current, b_prior, b_current, c_current]

    fscore_rows = []
    for current in (a_current, b_current, c_current):
        prior, components, proxy, aggregate = evaluate(current, rows)
        fscore_rows.append(fscore_row(current, prior, components, proxy, aggregate))

    record(CASE_STREAM, "真实A/B/C 行数", 3, len(fscore_rows))
    record(CASE_STREAM, "真实A/B/C 完整行数", 2, sum(1 for r in fscore_rows if r["aggregated"]["complete"]))
    record(CASE_STREAM, "真实A/B/C 含 None 行数", 1, sum(1 for r in fscore_rows if r["aggregated"]["total_score_8"] is None))

    compare_distributions(CASE_STREAM, "真实A/B/C", fscore_rows, {
        "complete": 2, "incomplete": 1, "mean_score": 4.0, "median_score": 4.0,
        "score_counts": {"0": 1, "8": 1},
    })
    compare_distributions(CASE_STREAM, "含None[8,0,None]", synthetic_rows([8, 0, None]), {
        "complete": 2, "incomplete": 1, "mean_score": 4.0, "median_score": 4.0,
        "score_counts": {"0": 1, "8": 1},
    })
    compare_distributions(CASE_STREAM, "全None[None]", synthetic_rows([None]), {
        "complete": 0, "incomplete": 1, "mean_score": None, "median_score": None,
        "score_counts": {},
    })
    compare_distributions(CASE_STREAM, "空输入[]", synthetic_rows([]), {
        "complete": 0, "incomplete": 0, "mean_score": None, "median_score": None,
        "score_counts": {},
    })
    compare_distributions(CASE_STREAM, "偶数中位数[8,0,5,6]", synthetic_rows([8, 0, 5, 6]), {
        "complete": 4, "incomplete": 0, "mean_score": 4.75, "median_score": 5.5,
        "score_counts": {"0": 1, "5": 1, "6": 1, "8": 1},
    })

def main() -> int:
    print("=" * 72)
    print("F-score v2 迁移冒烟测试")
    print("=" * 72)
    for case_fn in (case_main, case_asof, case_conflict, case_zero_division, case_streaming_vs_batch):
        print("-" * 72)
        case_fn()
    print("=" * 72)
    print("检查项总数: {}  失败数: {}".format(CHECKS, len(FAILURES)))
    if FAILURES:
        print("FAILURES:")
        for item in FAILURES:
            print("  - " + item)
        print("NOT ALL PASS")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())