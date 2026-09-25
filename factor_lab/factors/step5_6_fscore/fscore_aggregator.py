"""Fail-closed aggregation of the eight scoring components."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .fscore_schema import SCORING_COMPONENTS


def aggregate_components(components: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    available = sum(components.get(name, {}).get("status") == "AVAILABLE" for name in SCORING_COMPONENTS)
    unavailable = sum(components.get(name, {}).get("status") == "UNAVAILABLE" for name in SCORING_COMPONENTS)
    conflict = sum(components.get(name, {}).get("status") == "CONFLICT" for name in SCORING_COMPONENTS)
    reasons = [name for name in SCORING_COMPONENTS if components.get(name, {}).get("status") in {"UNAVAILABLE", "CONFLICT"}]
    complete = available == len(SCORING_COMPONENTS)
    return {
        "total_score_8": sum(int(components[name]["value"]) for name in SCORING_COMPONENTS) if complete else None,
        "complete": complete,
        "incomplete_reasons": reasons,
        "components_available": available,
        "components_unavailable": unavailable,
        "components_conflict": conflict,
    }


def score_distribution(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    complete_scores = [row["aggregated"]["total_score_8"] for row in rows if row["aggregated"].get("complete")]
    counts = Counter(row["aggregated"].get("total_score_8") for row in rows if row["aggregated"].get("complete"))
    if not complete_scores:
        mean = median = None
    else:
        values = sorted(complete_scores)
        mean = sum(values) / len(values)
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {"complete": len(complete_scores), "incomplete": len(rows) - len(complete_scores), "mean_score": mean, "median_score": median, "score_counts": {str(key): counts[key] for key in sorted(counts)}}
