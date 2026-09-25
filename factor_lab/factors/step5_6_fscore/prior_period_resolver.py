"""Resolve prior annual rows without filling or looking ahead."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PriorPeriodResult:
    row: Mapping[str, Any] | None
    period: str | None
    reason: str | None


def prior_annual_period(period_end_date: str | None) -> str | None:
    if not period_end_date:
        return None
    try:
        current = date.fromisoformat(str(period_end_date)[:10])
    except ValueError:
        return None
    return f"{current.year - 1:04d}-12-31"


def resolve_prior_period(current_row: Mapping[str, Any], rows_by_symbol_period: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]) -> PriorPeriodResult:
    current_period = str(current_row.get("period_end_date") or "")[:10]
    period = prior_annual_period(current_period)
    if period is None:
        return PriorPeriodResult(None, None, "PRIOR_PERIOD_NOT_VISIBLE")
    symbol = str(current_row.get("symbol") or "")
    rebalance = str(current_row.get("rebalance_date") or "")[:10]
    candidates = [row for row in rows_by_symbol_period.get((symbol, period), ()) if str(row.get("rebalance_date") or "")[:10] <= rebalance]
    candidates.sort(key=lambda row: str(row.get("rebalance_date") or ""), reverse=True)
    if not candidates:
        return PriorPeriodResult(None, period, "PRIOR_PERIOD_NOT_VISIBLE")
    return PriorPeriodResult(candidates[0], period, None)
