"""Compare derived ratios with the preserved fina_indicator reference."""

from __future__ import annotations

from typing import Any


REFERENCE_NAMES = {"roa": "roa", "grossprofit_margin": "grossprofit_margin", "assets_turn": "assets_turn"}


def normalize_reference_value(component: str, value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if component in {"roa", "grossprofit_margin"} and abs(number) > 1.0:
        return number / 100.0
    return number


def compare_values(component: str, derived_value: Any, reference_value: Any, threshold: float = 0.01) -> dict[str, Any]:
    normalized = normalize_reference_value(component, reference_value)
    if derived_value is None or normalized is None:
        return {"derived_value": derived_value, "reference_value": reference_value, "reference_normalized_value": normalized, "relative_diff": None, "divergent": False, "status": "NOT_COMPARABLE"}
    derived = float(derived_value)
    relative = abs(derived - normalized) / max(abs(normalized), 1e-9)
    divergent = relative > threshold
    return {"derived_value": derived, "reference_value": reference_value, "reference_normalized_value": normalized, "relative_diff": relative, "divergent": divergent, "status": "CROSS_SOURCE_DIVERGENCE" if divergent else "CONSISTENT"}
