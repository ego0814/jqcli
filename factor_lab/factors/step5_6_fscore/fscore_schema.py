"""Schema validation for the Step 5.6 F-score panel."""

from __future__ import annotations

from typing import Any, Mapping


SCORING_COMPONENTS = ("F1", "F2", "F3", "F4", "F5", "F6", "F8", "F9")
ALL_COMPONENTS = SCORING_COMPONENTS + ("F7_PROXY",)
VALID_STATUSES = {"AVAILABLE", "UNAVAILABLE", "CONFLICT"}


def validate_fscore_row(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = (
        "symbol", "rebalance_date", "period_kind", "period_end_date",
        "prior_period_end_date", "components", "F7_PROXY", "aggregated",
        "reference_fscore_components", "cross_check_divergence_count",
        "provenance",
    )
    for key in required:
        if key not in row:
            errors.append(f"MISSING_ROW_KEY:{key}")

    components = row.get("components")
    if not isinstance(components, Mapping):
        errors.append("INVALID_COMPONENTS_SECTION")
    else:
        for name in SCORING_COMPONENTS:
            item = components.get(name)
            if not isinstance(item, Mapping):
                errors.append(f"MISSING_COMPONENT:{name}")
                continue
            for key in ("value", "status", "inputs", "inputs_visible", "reference_value"):
                if key not in item:
                    errors.append(f"MISSING_COMPONENT_KEY:{name}:{key}")
            status = item.get("status")
            if status not in VALID_STATUSES:
                errors.append(f"INVALID_COMPONENT_STATUS:{name}")
            if not isinstance(item.get("inputs"), list) or not item.get("inputs"):
                errors.append(f"INVALID_COMPONENT_INPUTS:{name}")
            if not isinstance(item.get("inputs_visible"), bool):
                errors.append(f"INVALID_COMPONENT_VISIBILITY:{name}")
            value = item.get("value")
            if status == "AVAILABLE" and value not in (0, 1):
                errors.append(f"INVALID_AVAILABLE_VALUE:{name}")
            if status in {"UNAVAILABLE", "CONFLICT"} and value is not None:
                errors.append(f"NON_NULL_FAIL_CLOSED_VALUE:{name}")

    proxy = row.get("F7_PROXY")
    if not isinstance(proxy, Mapping):
        errors.append("INVALID_F7_PROXY")
    else:
        for key in ("value", "status", "label", "not_classic_f7", "inputs", "inputs_visible"):
            if key not in proxy:
                errors.append(f"MISSING_F7_PROXY_KEY:{key}")
        if proxy.get("label") != "F7_PROXY" or proxy.get("not_classic_f7") is not True:
            errors.append("INVALID_F7_PROXY_LABEL")
        if proxy.get("status") not in {"AVAILABLE", "UNAVAILABLE"}:
            errors.append("INVALID_F7_PROXY_STATUS")
        if proxy.get("status") == "AVAILABLE" and proxy.get("value") not in (0, 1):
            errors.append("INVALID_F7_PROXY_VALUE")
        if proxy.get("status") == "UNAVAILABLE" and proxy.get("value") is not None:
            errors.append("NON_NULL_F7_PROXY_VALUE")

    aggregate = row.get("aggregated")
    if not isinstance(aggregate, Mapping):
        errors.append("INVALID_AGGREGATED_SECTION")
    else:
        for key in ("total_score_8", "complete", "incomplete_reasons", "components_available", "components_unavailable", "components_conflict"):
            if key not in aggregate:
                errors.append(f"MISSING_AGGREGATED_KEY:{key}")
        if not isinstance(aggregate.get("complete"), bool):
            errors.append("INVALID_COMPLETE_FLAG")
        if not isinstance(aggregate.get("incomplete_reasons"), list):
            errors.append("INVALID_INCOMPLETE_REASONS")
        if aggregate.get("complete") and aggregate.get("total_score_8") not in range(9):
            errors.append("INVALID_COMPLETE_SCORE")
        if not aggregate.get("complete") and aggregate.get("total_score_8") is not None:
            errors.append("INCOMPLETE_SCORE_MUST_BE_NULL")
        for key in ("components_available", "components_unavailable", "components_conflict"):
            if not isinstance(aggregate.get(key), int) or not 0 <= aggregate.get(key) <= 8:
                errors.append(f"INVALID_AGGREGATED_COUNT:{key}")

    return sorted(set(errors))
