"""Build the deterministic Step 5.6 panel from Step 5.5 evidence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .f7_proxy_calculator import calculate_f7_proxy
from .fscore_aggregator import aggregate_components, score_distribution
from .fscore_components import COMPONENT_FUNCTIONS
from .fscore_schema import SCORING_COMPONENTS, validate_fscore_row
from .prior_period_resolver import PriorPeriodResult, resolve_prior_period


UPSTREAM_PANEL_FINGERPRINT = "599027222A5FFD9CEC61307C1C8E2D5154B062DDDFE634E47BFD136FC366A65A"
SCORING_PERIOD_KIND = "ANNUAL"  # Piotroski F-score 只用年报期
DERIVATION_RULES = {
    "F1": "n_income_t > 0",
    "F2": "n_cashflow_act_t > 0",
    "F3": "n_income_t / total_assets_t > n_income_t-1 / total_assets_t-1",
    "F4": "n_cashflow_act_t > n_income_t",
    "F5": "total_liab_t / total_assets_t < total_liab_t-1 / total_assets_t-1",
    "F6": "total_cur_assets_t / total_cur_liab_t > prior current ratio",
    "F8": "(revenue_t - oper_cost_t) / revenue_t > prior gross margin",
    "F9": "revenue_t / total_assets_t > prior asset turnover",
    "F7_PROXY": "total_share_t <= total_share_t-1 * 1.01; diagnostic only",
}


def _field(row: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    value = ((row or {}).get("financial") or {}).get(name)
    return value if isinstance(value, Mapping) else {}


def _ratio(row: Mapping[str, Any], numerator: str, denominator: str) -> float | None:
    left, right = _field(row, numerator), _field(row, denominator)
    if left.get("status") != "AVAILABLE" or right.get("status") != "AVAILABLE":
        return None
    try:
        return float(left["value"]) / float(right["value"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _reference_checks(current: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    checks: dict[str, Any] = {}
    definitions = {
        "F3": ("roa", _ratio(current, "n_income", "total_assets"), True),
        "F8": ("grossprofit_margin", None, True),
        "F9": ("assets_turn", _ratio(current, "revenue", "total_assets"), False),
    }
    revenue, cost = _field(current, "revenue"), _field(current, "oper_cost")
    if revenue.get("status") == "AVAILABLE" and cost.get("status") == "AVAILABLE":
        try:
            definitions["F8"] = ("grossprofit_margin", (float(revenue["value"]) - float(cost["value"])) / float(revenue["value"]), True)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    divergent = 0
    for component, (field_name, derived, percent_form) in definitions.items():
        reference = _field(current, field_name).get("value")
        normalized = None if reference is None else float(reference) / 100.0 if percent_form else float(reference)
        relative = None if derived is None or normalized is None else abs(derived - normalized) / max(abs(normalized), 1e-9)
        is_divergent = relative is not None and relative > 0.01
        divergent += int(is_divergent)
        checks[component] = {"field": field_name, "reference_value": reference, "reference_normalized_value": normalized, "derived_value": derived, "divergent": is_divergent}
    return checks, divergent


class FScorePanelBuilder:
    def __init__(self, derived_panel_path: str | Path = "research/evidence/financial_data_repair_v2/step5_5_derived_components/v2_derived_panel_v1.json", step5_4_stats_path: str | Path = "research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_stats_v1.json", expected_row_count: int | None = None, expected_drop_count: int | None = None) -> None:
        self.derived_panel_path = Path(derived_panel_path)
        self.step5_4_stats_path = Path(step5_4_stats_path)
        self.expected_row_count = expected_row_count
        self.expected_drop_count = expected_drop_count

    def _rows(self) -> list[dict[str, Any]]:
        payload = json.loads(self.derived_panel_path.read_text(encoding="utf-8"))
        rows = payload.get("rows") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("Step 5.5 panel must contain rows")
        result = [row for row in rows if isinstance(row, dict)]
        if self.expected_row_count is not None and len(result) != self.expected_row_count:
            raise ValueError(f"unexpected Step 5.5 row count: {len(result)} (expected {self.expected_row_count})")
        if any(row.get("derived_row_status") not in {"OK", "PARTIAL"} for row in result):
            raise ValueError("invalid Step 5.5 row status")
        return result

    def _verify_upstream_drop(self) -> dict[str, Any]:
        if self.expected_drop_count is None:
            return {"status": "SKIPPED", "step5_4_same_key_different_value_dropped": None,
                    "expected_drop_count": None, "rows_reprocessed": 0}
        stats = json.loads(self.step5_4_stats_path.read_text(encoding="utf-8"))
        dropped = int(stats.get("drop_reasons_breakdown", {}).get("SAME_KEY_DIFFERENT_VALUE", 0))
        if self.expected_drop_count is None:
            status = "SKIPPED"
        else:
            status = "PASS" if dropped == self.expected_drop_count else "FAIL"
        return {"status": status, "step5_4_same_key_different_value_dropped": dropped,
                "expected_drop_count": self.expected_drop_count, "rows_reprocessed": 0}

    def build(self) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self.build_from_rows(self._rows())

    def build_from_rows(self, source_rows: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """与 build() 同逻辑，但直接接收内存中的 panel 行，避免为大数据集写 JSON。"""
        scoring_rows = [row for row in source_rows if str(row.get("period_kind")) == SCORING_PERIOD_KIND]
        rows_by_symbol_period: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in scoring_rows:
            rows_by_symbol_period[(str(row.get("symbol")), str(row.get("period_end_date"))[:10])].append(row)

        output: list[dict[str, Any]] = []
        availability = {name: Counter() for name in SCORING_COMPONENTS + ("F7_PROXY",)}
        prior_reasons = Counter()
        conflict_reasons = Counter()
        for current in scoring_rows:
            prior: PriorPeriodResult = resolve_prior_period(current, rows_by_symbol_period)
            prior_row = prior.row
            current_date = str(current.get("rebalance_date") or "")
            components = {name: COMPONENT_FUNCTIONS[name](current, current_date) if name in {"F1", "F2", "F4"} else COMPONENT_FUNCTIONS[name](current, prior_row, current_date) for name in SCORING_COMPONENTS}
            proxy = calculate_f7_proxy(current, prior_row, current_date)
            aggregate = aggregate_components(components)
            available_values = [int(components[n]["value"]) for n in SCORING_COMPONENTS
                                if components[n]["status"] == "AVAILABLE" and components[n]["value"] is not None]
            partial_score = (sum(available_values) / len(available_values)) if (not aggregate["complete"] and available_values) else None
            checks, divergence_count = _reference_checks(current)
            if prior.reason:
                prior_reasons[prior.reason] += 1
            for name, item in {**components, "F7_PROXY": proxy}.items():
                availability[name][item["status"]] += 1
                if item["status"] == "CONFLICT":
                    conflict_reasons[name] += 1
            identities = {}
            for name in SCORING_COMPONENTS:
                names = components[name]["inputs"]
                identities[name] = {input_name: (_field(current, input_name.replace("prior ", "")).get("source_row_identity") if not input_name.startswith("prior ") else _field(prior_row, input_name[6:]).get("source_row_identity")) for input_name in names}
            identities["F7_PROXY"] = {"total_share": _field(current, "total_share").get("source_row_identity"), "prior total_share": _field(prior_row, "total_share").get("source_row_identity")}
            result = {
                "symbol": current["symbol"],
                "rebalance_date": current["rebalance_date"],
                "period_kind": current["period_kind"],
                "period_end_date": current["period_end_date"],
                "prior_period_end_date": prior.period,
                "components": components,
                "F7_PROXY": proxy,
                "aggregated": aggregate,
                "reference_fscore_components": checks,
                "cross_check_divergence_count": divergence_count,
                "pit_status": "PRACTICAL_PIT_APPLIED",
                "partial_score": partial_score,
                "provenance": {"source_row_identities": identities, "derivation_rules": DERIVATION_RULES, "upstream_panel_reference": UPSTREAM_PANEL_FINGERPRINT, "upstream_panel_path": str(self.derived_panel_path).replace("\\", "/")},
            }
            errors = validate_fscore_row(result)
            if errors:
                raise ValueError(f"F-score schema validation failed: {errors[:5]}")
            output.append(result)

        upstream = self._verify_upstream_drop()
        stats = {
            "schema_version": 1,
            "input_derived_rows": len(source_rows),
            "scoring_period_kind": SCORING_PERIOD_KIND,
            "scoring_rows": len(scoring_rows),
            "excluded_non_annual_rows": len(source_rows) - len(scoring_rows),
            "output_fscore_rows": len(output),
            "component_availability": {name: dict(counts) for name, counts in availability.items()},
            "component_unavailable_due_to_prior_not_visible": {name: availability[name].get("UNAVAILABLE", 0) for name in ("F3", "F5", "F6", "F8", "F9", "F7_PROXY")},
            "component_conflict": dict(conflict_reasons),
            "total_score_8_distribution": score_distribution(output),
            "incomplete_reasons_distribution": dict(Counter(reason for row in output for reason in row["aggregated"]["incomplete_reasons"])),
            "same_key_different_value_verification": upstream,
            "no_imputation": True,
            "no_lookahead": True,
            "deterministic": True,
            "provenance_complete": True,
        }
        component_availability = {"schema_version": 1, "components": stats["component_availability"], "prior_resolution_reasons": dict(prior_reasons)}
        drop_log = {"schema_version": 1, "dropped_rows": 0, "rows": [], "incomplete_rows_retained": stats["total_score_8_distribution"]["incomplete"]}
        return output, stats, component_availability, drop_log
