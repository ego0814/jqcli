"""ASOF eligibility logic for financial facts."""

from __future__ import annotations

from datetime import date
from typing import Mapping


def _normal_date(value: object) -> date | None:
    if value in (None, "", "None", "nan"):
        return None
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def visible_date(fact: Mapping[str, object]) -> date | None:
    """Use f_ann_date first; fall back to ann_date only when it is missing."""

    metadata = fact.get("metadata") if isinstance(fact.get("metadata"), Mapping) else fact
    for key in ("f_ann_date", "ann_date"):
        value = _normal_date(metadata.get(key))
        if value is not None:
            return value
    return None


def is_asof_eligible(fact: Mapping[str, object], rebalance_date: str) -> bool:
    cutoff = _normal_date(rebalance_date)
    available = visible_date(fact)
    return cutoff is not None and available is not None and available <= cutoff


def asof_reason(fact: Mapping[str, object], rebalance_date: str) -> str:
    if visible_date(fact) is None:
        return "MISSING_ANNOUNCEMENT_DATE"
    if not is_asof_eligible(fact, rebalance_date):
        return "ASOF_INELIGIBLE"
    return ""
