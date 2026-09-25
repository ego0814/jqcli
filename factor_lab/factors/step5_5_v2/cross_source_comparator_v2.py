"""Cross-source comparison for the v2 derived panel (original contract reused)."""
from __future__ import annotations

from ..step5_5_derived_components.cross_source_comparator import REFERENCE_NAMES, compare_values, normalize_reference_value

CROSS_COMPONENTS = (("roa", "roa_derived"), ("grossprofit_margin", "grossprofit_margin_derived"), ("assets_turn", "assets_turn_derived"))
CROSS_THRESHOLD = 0.01

__all__ = ["REFERENCE_NAMES", "compare_values", "normalize_reference_value", "CROSS_COMPONENTS", "CROSS_THRESHOLD"]
