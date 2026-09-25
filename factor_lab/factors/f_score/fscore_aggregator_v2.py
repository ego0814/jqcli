"""Streaming F-score aggregation for the v2 build."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from ..step5_6_fscore.fscore_aggregator import aggregate_components
from ..step5_6_fscore.fscore_schema import SCORING_COMPONENTS


def streaming_distribution() -> dict[str, Any]:
    """Incremental accumulator matching Step 5.6 v1 score_distribution semantics."""
    return {"counts": Counter(), "values": [], "incomplete": 0, "complete": 0}


def observe(distribution: dict[str, Any], aggregate: Mapping[str, Any]) -> None:
    if aggregate.get("complete"):
        score = int(aggregate["total_score_8"])
        distribution["counts"][score] += 1
        distribution["values"].append(score)
        distribution["complete"] += 1
    else:
        distribution["incomplete"] += 1


def finalize(distribution: dict[str, Any]) -> dict[str, Any]:
    values = sorted(distribution["values"])
    if not values:
        mean = median = None
    else:
        mean = sum(values) / len(values)
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {
        "complete": distribution["complete"],
        "incomplete": distribution["incomplete"],
        "mean_score": mean,
        "median_score": median,
        "score_counts": {str(key): distribution["counts"][key] for key in sorted(distribution["counts"])},
    }


__all__ = ["aggregate_components", "SCORING_COMPONENTS", "streaming_distribution", "observe", "finalize"]
