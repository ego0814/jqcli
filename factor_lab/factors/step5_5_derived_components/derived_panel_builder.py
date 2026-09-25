"""Build the derived-component panel from the Step 5.4 panel."""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .cross_source_comparator import compare_values
from .derived_calculator import calculate_derived_fields
from .derived_schema import DERIVED_FIELDS, validate_derived_row
from .prior_period_resolver import resolve_prior_period


class DerivedPanelBuilder:
    def __init__(self, panel_path: str | Path = "research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_v1.json") -> None:
        self.panel_path = Path(panel_path)

    def _rows(self) -> list[dict[str, Any]]:
        payload = json.loads(self.panel_path.read_text(encoding="utf-8"))
        rows = payload.get("rows") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("Step 5.4 panel must contain rows")
        return [row for row in rows if isinstance(row, dict) and row.get("row_status") == "OK"]

    @staticmethod
    def _cross(row: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for component, field in (("roa", "roa_derived"), ("grossprofit_margin", "grossprofit_margin_derived"), ("assets_turn", "assets_turn_derived")):
            reference = (row.get("financial") or {}).get(component) or {}
            output[component] = compare_values(component, derived[field].get("value"), reference.get("value"))
        return output

    def build(self) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        source_rows = self._rows()
        rows_by_symbol_period: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in source_rows:
            rows_by_symbol_period[(str(row.get("symbol")), str(row.get("period_end_date")))].append(row)
        output: list[dict[str, Any]] = []
        drop_log: list[dict[str, Any]] = []
        cross_checks: list[dict[str, Any]] = []
        counters = Counter()
        for row in source_rows:
            prior = resolve_prior_period(row, rows_by_symbol_period)
            derived, reasons = calculate_derived_fields(row, prior)
            cross = self._cross(row, derived)
            for component, field in (("roa", "roa_derived"), ("grossprofit_margin", "grossprofit_margin_derived"), ("assets_turn", "assets_turn_derived")):
                check = cross[component]
                if check["divergent"] and derived[field]["status"] == "AVAILABLE":
                    derived[field] = dict(derived[field])
                    derived[field]["status"] = "DIVERGENT"
                cross_checks.append({"symbol": row["symbol"], "rebalance_date": row["rebalance_date"], "period_end_date": row["period_end_date"], "component": component, **check})
                if check["divergent"]:
                    counters[f"{component.upper()}_DIVERGENCE"] += 1
                if derived[field]["status"] in {"AVAILABLE", "DIVERGENT"}:
                    counters[f"{component.upper()}_AVAILABLE"] += 1
                elif derived[field]["status"] == "CONFLICT":
                    counters[f"{component.upper()}_CONFLICT"] += 1
                else:
                    counters[f"{component.upper()}_UNAVAILABLE"] += 1
            if "PRIOR_PERIOD_NOT_VISIBLE" in reasons:
                counters["ROA_PRIOR_NOT_VISIBLE"] += 1
            if reasons:
                drop_log.append({"symbol": row["symbol"], "rebalance_date": row["rebalance_date"], "period_end_date": row["period_end_date"], "reasons": reasons})
            derived_row = copy.deepcopy(row)
            derived_row["derived"] = derived
            derived_row["reference"] = {
                component: {"field": component, **((row.get("financial") or {}).get(component) or {})}
                for component in ("roa", "grossprofit_margin", "assets_turn")
            }
            derived_row["cross_source"] = cross
            derived_row["derived_drop_reasons"] = reasons
            derived_row["derived_row_status"] = "OK" if all(item["status"] in {"AVAILABLE", "DIVERGENT"} for item in derived.values()) else "PARTIAL"
            errors = validate_derived_row(derived_row)
            if errors:
                raise ValueError(f"derived schema validation failed: {errors[:5]}")
            output.append(derived_row)
        stats = {
            "schema_version": 1,
            "input_panel_rows": len(source_rows),
            "output_panel_rows": len(output),
            "roa_derived_available": counters["ROA_AVAILABLE"],
            "roa_derived_unavailable": counters["ROA_UNAVAILABLE"],
            "roa_prior_not_visible": counters["ROA_PRIOR_NOT_VISIBLE"],
            "grossprofit_margin_derived_available": counters["GROSSPROFIT_MARGIN_AVAILABLE"],
            "grossprofit_margin_derived_unavailable": counters["GROSSPROFIT_MARGIN_UNAVAILABLE"],
            "grossprofit_margin_cogs_conflict": counters["GROSSPROFIT_MARGIN_CONFLICT"],
            "assets_turn_derived_available": counters["ASSETS_TURN_AVAILABLE"],
            "assets_turn_derived_unavailable": counters["ASSETS_TURN_UNAVAILABLE"],
            "cross_source_divergence_count": {"ROA": counters["ROA_DIVERGENCE"], "GROSSPROFIT_MARGIN": counters["GROSSPROFIT_MARGIN_DIVERGENCE"], "ASSETS_TURN": counters["ASSETS_TURN_DIVERGENCE"]},
            "no_imputation": True,
            "no_lookahead": True,
            "deterministic": True,
        }
        return output, stats, drop_log, cross_checks


def build_derived_panel(**kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    return DerivedPanelBuilder(**kwargs).build()
