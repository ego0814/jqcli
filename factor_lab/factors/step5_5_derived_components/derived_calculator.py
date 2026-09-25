"""Fail-closed calculations for the three approved derived components."""

from __future__ import annotations

from typing import Any, Mapping

from .prior_period_resolver import PriorPeriodResult


def _field(row: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return (row.get("financial") or {}).get(name) or {}


def _derived(
    value: float | None,
    status: str,
    formula: str,
    inputs: list[str],
    identities: list[str | None],
) -> dict[str, Any]:
    identity = "derived_from_" + "|".join(str(item) for item in identities if item)
    return {
        "value": value,
        "status": status,
        "unit": "RATIO",
        "unit_status": "CONFIRMED",
        "source_row_identity": identity,
        "derivation_formula": formula,
        "input_fields": inputs,
    }


def _calculate(name: str, values: list[tuple[str, Mapping[str, Any]]], formula: str, inputs: list[str]) -> dict[str, Any]:
    if any(field.get("status") == "CONFLICT" for _, field in values):
        return _derived(None, "CONFLICT", formula, inputs, [field.get("source_row_identity") for _, field in values])
    if any(field.get("status") != "AVAILABLE" or field.get("value") is None for _, field in values):
        return _derived(None, "UNAVAILABLE", formula, inputs, [field.get("source_row_identity") for _, field in values])
    try:
        numbers = [float(field["value"]) for _, field in values]
        if name == "roa_derived":
            denominator = (numbers[1] + numbers[2]) / 2.0
            value = None if denominator == 0 else numbers[0] / denominator
        elif name == "grossprofit_margin_derived":
            value = None if numbers[0] == 0 else (numbers[0] - numbers[1]) / numbers[0]
        else:
            value = None if numbers[1] == 0 else numbers[0] / numbers[1]
    except (TypeError, ValueError, ZeroDivisionError):
        value = None
    status = "AVAILABLE" if value is not None else "UNAVAILABLE"
    return _derived(value, status, formula, inputs, [field.get("source_row_identity") for _, field in values])


def calculate_derived_fields(panel_row: Mapping[str, Any], prior: PriorPeriodResult) -> tuple[dict[str, dict[str, Any]], list[str]]:
    current = panel_row
    current_assets = _field(current, "total_assets")
    prior_assets = _field(prior.row or {}, "total_assets")
    income = _field(current, "n_income")
    revenue = _field(current, "revenue")
    oper_cost = _field(current, "oper_cost")
    derived = {
        "roa_derived": _calculate("roa_derived", [("n_income", income), ("total_assets_t_minus_1", prior_assets), ("total_assets_t", current_assets)], "n_income / ((total_assets_t_minus_1 + total_assets_t) / 2)", ["n_income", "total_assets_t", "total_assets_t_minus_1"]),
        "grossprofit_margin_derived": _calculate("grossprofit_margin_derived", [("revenue", revenue), ("oper_cost", oper_cost)], "(revenue - oper_cost) / revenue", ["revenue", "oper_cost"]),
        "assets_turn_derived": _calculate("assets_turn_derived", [("revenue", revenue), ("total_assets_t", current_assets)], "revenue / total_assets_t", ["revenue", "total_assets_t"]),
    }
    reasons: list[str] = []
    if prior.reason:
        reasons.append(prior.reason)
    if derived["grossprofit_margin_derived"]["status"] == "CONFLICT":
        reasons.append("COGS_CONFLICT")
    return derived, list(dict.fromkeys(reasons))
