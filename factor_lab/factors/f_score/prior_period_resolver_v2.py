"""Streaming prior-period resolution for the Step 5.6 v2 F-score build."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from ..step5_5_v2.prior_period_resolver_v2 import iter_array_objects
from ..step5_6_fscore.prior_period_resolver import PriorPeriodResult, prior_annual_period

PRIOR_FIELDS = (
    "n_income",
    "total_assets",
    "total_liab",
    "total_cur_assets",
    "total_cur_liab",
    "revenue",
    "oper_cost",
    "total_share",
)


def _compact(field: Mapping[str, Any] | None) -> tuple:
    field = field or {}
    return (
        field.get("status"),
        field.get("value"),
        field.get("source_ann_date"),
        field.get("source_f_ann_date"),
        field.get("source_row_identity"),
    )


def _expand(entry: tuple) -> dict[str, Any]:
    status, value, ann, f_ann, identity = entry
    return {"status": status, "value": value, "source_ann_date": ann, "source_f_ann_date": f_ann, "source_row_identity": identity}


def build_prior_index(panel_path: str | Path) -> dict[tuple[str, str], list[tuple[str, dict[str, tuple]]]]:
    """Compact index: (symbol, period_end_date) -> [(rebalance_date, {field: compact_tuple})]."""
    index: dict[tuple[str, str], list[tuple[str, dict[str, tuple]]]] = {}
    for row in iter_array_objects(panel_path, "rows"):
        financial = row.get("financial") or {}
        symbol = str(row.get("symbol"))
        period = str(row.get("period_end_date"))[:10]
        rebalance = str(row.get("rebalance_date"))[:10]
        compact = {name: _compact(financial.get(name)) for name in PRIOR_FIELDS}
        index.setdefault((symbol, period), []).append((rebalance, compact))
    return index


def resolve_prior(current_row: Mapping[str, Any], index: Mapping[tuple[str, str], list[tuple[str, dict[str, tuple]]]]) -> PriorPeriodResult:
    period = prior_annual_period(str(current_row.get("period_end_date") or "")[:10])
    if period is None:
        return PriorPeriodResult(None, None, "PRIOR_PERIOD_NOT_VISIBLE")
    symbol = str(current_row.get("symbol") or "")
    rebalance = str(current_row.get("rebalance_date") or "")[:10]
    candidates = [item for item in index.get((symbol, period), ()) if item[0] <= rebalance]
    if not candidates:
        return PriorPeriodResult(None, period, "PRIOR_PERIOD_NOT_VISIBLE")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, compact = candidates[0]
    row = {"financial": {name: _expand(entry) for name, entry in compact.items()}}
    return PriorPeriodResult(row, period, None)
