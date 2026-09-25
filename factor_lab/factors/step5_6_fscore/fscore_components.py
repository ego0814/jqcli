"""Pure, fail-closed implementations of the approved F-score components."""

from __future__ import annotations

from typing import Any, Mapping


def _financial(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    value = (row or {}).get("financial")
    return value if isinstance(value, Mapping) else {}


def _field(row: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    value = _financial(row).get(name)
    return value if isinstance(value, Mapping) else {}


def _visible(field: Mapping[str, Any], rebalance_date: str) -> bool:
    if field.get("status") != "AVAILABLE" or field.get("value") is None:
        return False
    dates = [field.get("source_ann_date"), field.get("source_f_ann_date")]
    dates = [str(value)[:10] for value in dates if value not in (None, "", "None")]
    return bool(dates) and min(dates) <= str(rebalance_date)[:10]


def _result(name: str, fields: list[tuple[str, Mapping[str, Any]]], inputs: list[str], rebalance_date: str, condition: Any, reference_value: Any = None) -> dict[str, Any]:
    if any(field.get("status") == "CONFLICT" for _, field in fields):
        status = "CONFLICT"
    elif not all(_visible(field, rebalance_date) for _, field in fields):
        status = "UNAVAILABLE"
    else:
        try:
            status = "AVAILABLE"
            value = int(bool(condition([float(field["value"]) for _, field in fields])))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            status = "UNAVAILABLE"
    value = value if status == "AVAILABLE" else None
    return {"value": value, "status": status, "inputs": inputs, "inputs_visible": status == "AVAILABLE", "reference_value": reference_value}


def f1_n_income_positive(current: Mapping[str, Any], rebalance_date: str) -> dict[str, Any]:
    return _result("F1", [("n_income", _field(current, "n_income"))], ["n_income"], rebalance_date, lambda v: v[0] > 0)


def f2_operating_cashflow_positive(current: Mapping[str, Any], rebalance_date: str) -> dict[str, Any]:
    return _result("F2", [("n_cashflow_act", _field(current, "n_cashflow_act"))], ["n_cashflow_act"], rebalance_date, lambda v: v[0] > 0)


def f3_roa_improves(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    fields = [("n_income", _field(current, "n_income")), ("total_assets", _field(current, "total_assets")), ("prior_n_income", _field(prior, "n_income")), ("prior_total_assets", _field(prior, "total_assets"))]
    return _result("F3", fields, ["n_income", "total_assets", "prior n_income", "prior total_assets"], rebalance_date, lambda v: v[0] / v[1] > v[2] / v[3], _field(current, "roa").get("value"))


def f4_cashflow_exceeds_income(current: Mapping[str, Any], rebalance_date: str) -> dict[str, Any]:
    fields = [("n_cashflow_act", _field(current, "n_cashflow_act")), ("n_income", _field(current, "n_income"))]
    return _result("F4", fields, ["n_cashflow_act", "n_income"], rebalance_date, lambda v: v[0] > v[1])


def f5_leverage_decreases(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    fields = [("total_liab", _field(current, "total_liab")), ("total_assets", _field(current, "total_assets")), ("prior_total_liab", _field(prior, "total_liab")), ("prior_total_assets", _field(prior, "total_assets"))]
    return _result("F5", fields, ["total_liab", "total_assets", "prior total_liab", "prior total_assets"], rebalance_date, lambda v: v[0] / v[1] < v[2] / v[3])


def f6_current_ratio_improves(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    fields = [("total_cur_assets", _field(current, "total_cur_assets")), ("total_cur_liab", _field(current, "total_cur_liab")), ("prior_total_cur_assets", _field(prior, "total_cur_assets")), ("prior_total_cur_liab", _field(prior, "total_cur_liab"))]
    return _result("F6", fields, ["total_cur_assets", "total_cur_liab", "prior total_cur_assets", "prior total_cur_liab"], rebalance_date, lambda v: v[0] / v[1] > v[2] / v[3])


def f8_gross_margin_improves(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    fields = [("revenue", _field(current, "revenue")), ("oper_cost", _field(current, "oper_cost")), ("prior_revenue", _field(prior, "revenue")), ("prior_oper_cost", _field(prior, "oper_cost"))]
    return _result("F8", fields, ["revenue", "oper_cost", "prior revenue", "prior oper_cost"], rebalance_date, lambda v: (v[0] - v[1]) / v[0] > (v[2] - v[3]) / v[2], _field(current, "grossprofit_margin").get("value"))


def f9_asset_turnover_improves(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    fields = [("revenue", _field(current, "revenue")), ("total_assets", _field(current, "total_assets")), ("prior_revenue", _field(prior, "revenue")), ("prior_total_assets", _field(prior, "total_assets"))]
    return _result("F9", fields, ["revenue", "total_assets", "prior revenue", "prior total_assets"], rebalance_date, lambda v: v[0] / v[1] > v[2] / v[3], _field(current, "assets_turn").get("value"))


COMPONENT_FUNCTIONS = {
    "F1": f1_n_income_positive,
    "F2": f2_operating_cashflow_positive,
    "F3": f3_roa_improves,
    "F4": f4_cashflow_exceeds_income,
    "F5": f5_leverage_decreases,
    "F6": f6_current_ratio_improves,
    "F8": f8_gross_margin_improves,
    "F9": f9_asset_turnover_improves,
}
