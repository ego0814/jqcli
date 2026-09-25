"""Resolve the prior annual total-assets observation for ROA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PriorPeriodResult:
    row: Mapping[str, Any] | None
    period: str | None
    reason: str | None


def _period_key(period_end_date: str | None) -> str | None:
    if not period_end_date:
        return None
    try:
        current = date.fromisoformat(str(period_end_date)[:10])
        return f"{current.year - 1:04d}-12-31"
    except ValueError:
        return None


def _visible_before(row: Mapping[str, Any], rebalance_date: str) -> bool:
    field = (row.get("financial") or {}).get("total_assets") or {}
    if field.get("status") == "CONFLICT":
        return True
    dates = [field.get("source_ann_date"), field.get("source_f_ann_date")]
    present = [str(value)[:10] for value in dates if value not in (None, "", "None")]
    return bool(present) and min(present) <= rebalance_date


def resolve_prior_period(
    current_row: Mapping[str, Any],
    rows_by_symbol_period: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> PriorPeriodResult:
    """Return the latest prior-period row visible at the current rebalance."""

    current_period = str(current_row.get("period_end_date") or "")[:10]
    prior_period = _period_key(current_period)
    if prior_period is None:
        return PriorPeriodResult(None, None, "PRIOR_PERIOD_NOT_VISIBLE")
    key = (str(current_row.get("symbol")), prior_period)
    candidates = sorted(rows_by_symbol_period.get(key, ()), key=lambda row: str(row.get("rebalance_date", "")), reverse=True)
    rebalance = str(current_row.get("rebalance_date", ""))[:10]
    for row in candidates:
        if str(row.get("rebalance_date", ""))[:10] > rebalance:
            continue
        field = (row.get("financial") or {}).get("total_assets") or {}
        if field.get("status") == "CONFLICT":
            return PriorPeriodResult(row, prior_period, "PRIOR_PERIOD_CONFLICT")
        if field.get("status") == "AVAILABLE" and field.get("value") is not None and _visible_before(row, rebalance):
            return PriorPeriodResult(row, prior_period, None)
    return PriorPeriodResult(None, prior_period, "PRIOR_PERIOD_NOT_VISIBLE")
