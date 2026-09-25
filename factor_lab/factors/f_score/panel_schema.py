"""Minimal deterministic validator for V2 panel rows."""

from __future__ import annotations

from typing import Any, Mapping


PANEL_FIELDS = [
    "n_income", "n_cashflow_act", "oper_cost", "revenue", "total_revenue",
    "total_assets", "total_liab", "total_cur_assets", "total_cur_liab", "total_share",
    "roe", "roa", "roe_waa", "netprofit_margin", "grossprofit_margin",
    "assets_turn", "eps", "bps", "netprofit_yoy",
]
FIELD_KEYS = ["value", "status", "unit", "unit_status", "source_row_identity", "source_update_flag", "source_ann_date", "source_f_ann_date"]

ROW_KEYS = [
    "symbol", "rebalance_date", "period_kind", "period_end_date", "financial",
    "price", "asof", "row_status", "drop_reasons", "fail_closed_conditions",
]
OPTIONAL_ROW_KEYS = ["pit_status", "partial_score"]
PIT_STATUS_VALUES = {"PRACTICAL_PIT_APPLIED", "STRICT_PIT_APPLIED"}
DEFAULT_PIT_STATUS = "PRACTICAL_PIT_APPLIED"


def validate_row(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ROW_KEYS:
        if key not in row:
            errors.append(f"MISSING_ROW_KEY:{key}")
    pit_status = row.get("pit_status", DEFAULT_PIT_STATUS)
    if pit_status not in PIT_STATUS_VALUES:
        errors.append(f"INVALID_PIT_STATUS:{pit_status}")
    partial_score = row.get("partial_score")
    if partial_score is not None:
        if isinstance(partial_score, bool) or not isinstance(partial_score, (int, float)) or not 0.0 <= float(partial_score) <= 8.0:
            errors.append("INVALID_PARTIAL_SCORE")
    financial = row.get("financial")
    if not isinstance(financial, Mapping):
        errors.append("INVALID_FINANCIAL_SECTION")
    else:
        for field in PANEL_FIELDS:
            entry = financial.get(field)
            if not isinstance(entry, Mapping):
                errors.append(f"MISSING_FIELD:{field}")
                continue
            for key in FIELD_KEYS:
                if key not in entry:
                    errors.append(f"MISSING_FIELD_KEY:{field}:{key}")
            if entry.get("status") not in {"AVAILABLE", "UNAVAILABLE", "CONFLICT"}:
                errors.append(f"INVALID_STATUS:{field}")
            if entry.get("unit_status") not in {"INFERRED_WITH_EVIDENCE", "CONFIRMED", "UNKNOWN"}:
                errors.append(f"INVALID_UNIT_STATUS:{field}")
    price = row.get("price")
    if not isinstance(price, Mapping) or "close_raw" not in price or "close_raw_date" not in price:
        errors.append("INVALID_PRICE_SECTION")
    asof = row.get("asof")
    if not isinstance(asof, Mapping) or not {"asof_cutoff", "asof_eligible", "announcement_date", "availability_date"}.issubset(asof):
        errors.append("INVALID_ASOF_SECTION")
    if row.get("row_status") not in {"OK", "DROPPED"}:
        errors.append("INVALID_ROW_STATUS")
    if not isinstance(row.get("drop_reasons"), list) or not isinstance(row.get("fail_closed_conditions"), list):
        errors.append("INVALID_DROP_METADATA")
    return sorted(set(errors))
