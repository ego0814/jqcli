"""Independent diagnostic proxy for share-count stability."""

from __future__ import annotations

from typing import Any, Mapping


def _field(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    value = ((row or {}).get("financial") or {}).get("total_share")
    return value if isinstance(value, Mapping) else {}


def calculate_f7_proxy(current: Mapping[str, Any], prior: Mapping[str, Any] | None, rebalance_date: str) -> dict[str, Any]:
    current_field = _field(current)
    prior_field = _field(prior)
    fields = (current_field, prior_field)
    visible = True
    for field in fields:
        dates = [field.get("source_ann_date"), field.get("source_f_ann_date")]
        dates = [str(value)[:10] for value in dates if value not in (None, "", "None")]
        if field.get("status") != "AVAILABLE" or field.get("value") is None or not dates or min(dates) > str(rebalance_date)[:10]:
            visible = False
    value: int | None = None
    status = "AVAILABLE" if visible else "UNAVAILABLE"
    if visible:
        try:
            value = int(float(current_field["value"]) <= float(prior_field["value"]) * 1.01)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            status, value = "UNAVAILABLE", None
    return {
        "value": value,
        "status": status,
        "label": "F7_PROXY",
        "not_classic_f7": True,
        "inputs": ["total_share", "prior total_share"],
        "inputs_visible": status == "AVAILABLE",
    }
