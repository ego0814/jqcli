"""Validation for Step 5.5 derived panel rows."""

from __future__ import annotations

from typing import Any, Mapping


DERIVED_FIELDS = ("roa_derived", "grossprofit_margin_derived", "assets_turn_derived")
DERIVED_KEYS = ("value", "status", "unit", "unit_status", "source_row_identity", "derivation_formula", "input_fields")
VALID_STATUSES = {"AVAILABLE", "UNAVAILABLE", "CONFLICT", "DIVERGENT"}


def validate_derived_row(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("symbol", "rebalance_date", "period_kind", "period_end_date", "financial", "derived", "reference", "cross_source", "derived_row_status"):
        if key not in row:
            errors.append(f"MISSING_ROW_KEY:{key}")
    derived = row.get("derived")
    if not isinstance(derived, Mapping):
        errors.append("INVALID_DERIVED_SECTION")
    else:
        for field in DERIVED_FIELDS:
            value = derived.get(field)
            if not isinstance(value, Mapping):
                errors.append(f"MISSING_DERIVED_FIELD:{field}")
                continue
            for key in DERIVED_KEYS:
                if key not in value:
                    errors.append(f"MISSING_DERIVED_KEY:{field}:{key}")
            if value.get("status") not in VALID_STATUSES:
                errors.append(f"INVALID_DERIVED_STATUS:{field}")
            if value.get("unit") != "RATIO" or value.get("unit_status") != "CONFIRMED":
                errors.append(f"INVALID_DERIVED_UNIT:{field}")
            if not isinstance(value.get("input_fields"), list) or not value.get("input_fields"):
                errors.append(f"INVALID_INPUT_FIELDS:{field}")
    cross = row.get("cross_source")
    if not isinstance(cross, Mapping) or not all(field in cross for field in ("roa", "grossprofit_margin", "assets_turn")):
        errors.append("INVALID_CROSS_SOURCE_SECTION")
    return sorted(set(errors))
