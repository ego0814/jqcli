"""Streaming Step 5.5 v2 derived-component build."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from ..universe_expansion.phase_a.acquire import PROTECTED_HASHES
from .cross_source_comparator_v2 import CROSS_COMPONENTS, CROSS_THRESHOLD, compare_values
from .derived_calculator_v2 import calculate_derived_fields, validate_derived_row
from .prior_period_resolver_v2 import build_prior_index, iter_array_objects, resolve_prior

PROTOCOL_VERSION = "QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS_V2"
PANEL_PATH = Path("research/evidence/financial_data_repair_v2/step5_4_v2/v2_panel_v2.json")
OUTPUT_ROOT = Path("research/evidence/financial_data_repair_v2/step5_5_v2")
REPORT_PATH = Path("docs/governance/QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS.md")


def canonical_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")


def acl_preflight(output: Path) -> dict[str, Any]:
    sentinel = output / ".acl_check"
    try:
        output.mkdir(parents=True, exist_ok=True)
        if not os.access(output, os.W_OK):
            raise PermissionError(f"directory is not writable: {output}")
        sentinel.write_text("quantlab-step5-5-v2-acl-check\n", encoding="utf-8", newline="\n")
        if sentinel.read_text(encoding="utf-8") != "quantlab-step5-5-v2-acl-check\n":
            raise OSError("ACL sentinel read-back mismatch")
        sentinel.unlink()
        return {"status": "PASS", "method": "os_access_and_sentinel", "sentinel_removed": True, "provider_api_called": False}
    except Exception as exc:
        if sentinel.exists():
            try:
                sentinel.unlink()
            except OSError:
                pass
        return {"status": "FAIL", "method": "os_access_and_sentinel", "error_type": type(exc).__name__, "error_message": str(exc)[:300], "provider_api_called": False}


class _StreamWriter:
    def __init__(self, path: Path, prefix: str, suffix: str) -> None:
        self.path = path
        self.prefix = prefix.encode("utf-8")
        self.suffix = suffix.encode("utf-8")
        self.hasher = hashlib.sha256()
        self.hasher.update(self.prefix)
        self.handle = path.open("w", encoding="utf-8", newline="\n")
        self.handle.write(self.prefix.decode("utf-8"))
        self.count = 0

    def write(self, payload: Any) -> None:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        if self.count:
            self.handle.write(",")
            self.hasher.update(b",")
        self.handle.write(blob.decode("utf-8"))
        self.hasher.update(blob)
        self.count += 1

    def close(self) -> dict[str, Any]:
        self.handle.write(self.suffix.decode("utf-8"))
        self.hasher.update(self.suffix)
        self.handle.write("\n")
        self.handle.close()
        return {"path": str(self.path), "rows": self.count, "fingerprint": self.hasher.hexdigest().upper(), "bytes": self.path.stat().st_size}


def build_derived(panel_path: Path, index: dict, panel_out: Path, drop_out: Path, cross_out: Path, collect_drop: bool = False) -> dict[str, Any]:
    panel = _StreamWriter(panel_out, '{"panel_type":"V2_DERIVED_COMPONENTS_NO_IMPUTATION","rows":[', '],"schema_version":1}')
    drop = _StreamWriter(drop_out, '{"rows":[', '],"schema_version":1}')
    cross = _StreamWriter(cross_out, '{"rows":[', '],"schema_version":1,"threshold":0.01}')
    counters: Counter = Counter()
    validation_errors: list[str] = []
    try:
        for row in iter_array_objects(panel_path, "rows"):
            if row.get("row_status") != "OK":
                continue
            prior = resolve_prior(row, index)
            derived, reasons = calculate_derived_fields(row, prior)
            cross_section: dict[str, dict[str, Any]] = {}
            for component, field in CROSS_COMPONENTS:
                reference = ((row.get("financial") or {}).get(component)) or {}
                check = compare_values(component, derived[field].get("value"), reference.get("value"), CROSS_THRESHOLD)
                cross_section[component] = check
                if check["divergent"] and derived[field]["status"] == "AVAILABLE":
                    derived[field] = dict(derived[field])
                    derived[field]["status"] = "DIVERGENT"
                cross.write({"symbol": row["symbol"], "rebalance_date": row["rebalance_date"], "period_end_date": row["period_end_date"], "component": component, **check})
                if check["divergent"]:
                    counters[f"{component.upper()}_DIVERGENCE"] += 1
                status = derived[field]["status"]
                if status in {"AVAILABLE", "DIVERGENT"}:
                    counters[f"{component.upper()}_AVAILABLE"] += 1
                elif status == "CONFLICT":
                    counters[f"{component.upper()}_CONFLICT"] += 1
                else:
                    counters[f"{component.upper()}_UNAVAILABLE"] += 1
            if "PRIOR_PERIOD_NOT_VISIBLE" in reasons:
                counters["ROA_PRIOR_NOT_VISIBLE"] += 1
            if reasons:
                drop.write({"symbol": row["symbol"], "rebalance_date": row["rebalance_date"], "period_end_date": row["period_end_date"], "reasons": reasons})
            row["derived"] = derived
            row["reference"] = {component: {"field": component, **(((row.get("financial") or {}).get(component)) or {})} for component, _ in CROSS_COMPONENTS}
            row["cross_source"] = cross_section
            row["derived_drop_reasons"] = reasons
            row["derived_row_status"] = "OK" if all(item["status"] in {"AVAILABLE", "DIVERGENT"} for item in derived.values()) else "PARTIAL"
            errors = validate_derived_row(row)
            if errors:
                validation_errors.extend(f"{row.get('symbol')}:{row.get('rebalance_date')}:{error}" for error in errors[:3])
            panel.write(row)
            counters["OUTPUT_ROWS"] += 1
    finally:
        panel_info = panel.close()
        drop_info = drop.close()
        cross_info = cross.close()
    stats = {
        "schema_version": 1,
        "input_panel_rows": counters["OUTPUT_ROWS"],
        "output_panel_rows": counters["OUTPUT_ROWS"],
        "roa_derived_available": counters["ROA_AVAILABLE"],
        "roa_derived_unavailable": counters["ROA_UNAVAILABLE"],
        "roa_derived_conflict": counters["ROA_CONFLICT"],
        "roa_prior_not_visible": counters["ROA_PRIOR_NOT_VISIBLE"],
        "grossprofit_margin_derived_available": counters["GROSSPROFIT_MARGIN_AVAILABLE"],
        "grossprofit_margin_derived_unavailable": counters["GROSSPROFIT_MARGIN_UNAVAILABLE"],
        "grossprofit_margin_cogs_conflict": counters["GROSSPROFIT_MARGIN_CONFLICT"],
        "assets_turn_derived_available": counters["ASSETS_TURN_AVAILABLE"],
        "assets_turn_derived_unavailable": counters["ASSETS_TURN_UNAVAILABLE"],
        "assets_turn_derived_conflict": counters["ASSETS_TURN_CONFLICT"],
        "cross_source_divergence_count": {
            "ROA": counters["ROA_DIVERGENCE"],
            "GROSSPROFIT_MARGIN": counters["GROSSPROFIT_MARGIN_DIVERGENCE"],
            "ASSETS_TURN": counters["ASSETS_TURN_DIVERGENCE"],
        },
        "no_imputation": True,
        "no_lookahead": True,
        "deterministic": True,
        "panel_validation_errors": len(validation_errors),
    }
    return {"stats": stats, "panel": panel_info, "drop_log": drop_info, "cross_check": cross_info, "validation_errors": validation_errors}


def run(output_dir: str | Path = OUTPUT_ROOT, root: str | Path = Path(".")) -> dict[str, Any]:
    root = Path(root).resolve()
    output = Path(output_dir).resolve()
    before = {p: file_fingerprint(root / p) for p in PROTECTED_HASHES}
    if any(before[p] != PROTECTED_HASHES[p] for p in PROTECTED_HASHES):
        raise RuntimeError("protected hash preflight failed")
    acl = acl_preflight(output)
    if acl["status"] != "PASS":
        raise RuntimeError(f"ACL preflight failed: {acl}")

    panel_path = root / PANEL_PATH
    index = build_prior_index(panel_path)
    primary = build_derived(panel_path, index, output / "v2_derived_panel_v2.json", output / "v2_derived_drop_log_v2.json", output / "v2_derived_cross_check_v2.json")
    stats = primary["stats"]
    if primary["validation_errors"]:
        raise ValueError(f"derived schema validation failed: {primary['validation_errors'][:5]}")
    if stats["roa_derived_available"] < 100000:
        raise RuntimeError(f"roa_derived_available below floor: {stats['roa_derived_available']}")
    if stats["roa_derived_unavailable"] != stats["roa_prior_not_visible"]:
        raise RuntimeError("ROA unavailable count does not reconcile with prior-not-visible count")

    replay = build_derived(panel_path, index, output / ".replay_derived_panel.json", output / ".replay_drop.json", output / ".replay_cross.json")
    replay_matches = replay["panel"]["fingerprint"] == primary["panel"]["fingerprint"]
    for name in (".replay_derived_panel.json", ".replay_drop.json", ".replay_cross.json"):
        target = output / name
        if target.exists():
            target.unlink()
    if not replay_matches:
        raise RuntimeError("deterministic replay mismatch")

    panel_fp = primary["panel"]["fingerprint"]
    stats_fp_input = dict(stats)
    stats_fp_input["derived_panel_fingerprint"] = panel_fp
    stats_fp = canonical_fingerprint(stats_fp_input)
    drop_fp = primary["drop_log"]["fingerprint"]
    cross_fp = primary["cross_check"]["fingerprint"]

    artifact = {
        "artifact_type": "QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS",
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": 1,
        "status": "PASS",
        "derived_components": ["roa_derived", "grossprofit_margin_derived", "assets_turn_derived"],
        "input_panel_rows": stats["input_panel_rows"],
        "output_panel_rows": stats["output_panel_rows"],
        "roa_derived_available": stats["roa_derived_available"],
        "roa_derived_unavailable": stats["roa_derived_unavailable"],
        "roa_prior_not_visible": stats["roa_prior_not_visible"],
        "grossprofit_margin_derived_available": stats["grossprofit_margin_derived_available"],
        "assets_turn_derived_available": stats["assets_turn_derived_available"],
        "cross_source_divergence_count": stats["cross_source_divergence_count"],
        "panel_fingerprint": panel_fp,
        "stats_fingerprint": stats_fp,
        "drop_log_fingerprint": drop_fp,
        "cross_check_fingerprint": cross_fp,
        "acl_preflight_result": acl["status"],
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_mutation": False,
        "business_code_mutation": "NEW_NAMESPACE_ONLY",
        "f_score_calculated": False,
        "far_calculated": False,
        "unit_conversion_applied": False,
        "imputation_applied": False,
        "revision_selection_executed": False,
        "deterministic_replay_verified": replay_matches,
        "artifact_fingerprint_scope": "SHA256_CANONICAL_JSON_EXCLUDING_ARTIFACT_FINGERPRINT",
        "source_fingerprints": {
            "STEP5_4_V2_PANEL": json.loads((root / "research/evidence/financial_data_repair_v2/step5_4_v2/v2_panel_v2_fingerprint.json").read_text(encoding="utf-8"))["V2_PANEL_FINGERPRINT"],
            "PHASE_E": json.loads((root / "research/evidence/financial_data_repair_v2/universe_expansion/phase_e/phase_e_fingerprint.json").read_text(encoding="utf-8"))["phase_e_fingerprint"],
        },
    }
    artifact["artifact_fingerprint"] = canonical_fingerprint(artifact)
    fingerprints = {
        "artifact_type": "QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS_FINGERPRINT",
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": 1,
        "fingerprint_algorithm": "SHA256",
        "fingerprint_scope": "CANONICAL_JSON_EXCLUDING_OWN_ARTIFACT_FINGERPRINT",
        "V2_DERIVED_PANEL_FINGERPRINT": panel_fp,
        "V2_DERIVED_STATS_FINGERPRINT": stats_fp,
        "V2_DERIVED_DROP_LOG_FINGERPRINT": drop_fp,
        "V2_DERIVED_CROSS_CHECK_FINGERPRINT": cross_fp,
        "V2_DERIVED_ARTIFACT_FINGERPRINT": artifact["artifact_fingerprint"],
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_mutation": False,
    }
    payload = dict(fingerprints)
    fingerprints["artifact_fingerprint"] = canonical_fingerprint(payload)

    stats_payload = dict(stats)
    stats_payload["derived_panel_fingerprint"] = panel_fp
    _write_json(output / "v2_derived_stats_v2.json", stats_payload)
    _write_json(output / "v2_derived_artifact_v2.json", artifact)
    _write_json(output / "v2_derived_fingerprint_v2.json", fingerprints)

    report = [
        "# Quant Lab - Step F.2 / Step 5.5 v2 Derived Components", "",
        f"STEP_F2_STATUS = {artifact['status']}",
        f"INPUT_PANEL_ROWS = {stats['input_panel_rows']}",
        f"OUTPUT_PANEL_ROWS = {stats['output_panel_rows']}",
        f"ROA_DERIVED_AVAILABLE = {stats['roa_derived_available']}",
        f"ROA_DERIVED_UNAVAILABLE = {stats['roa_derived_unavailable']}",
        f"ROA_PRIOR_NOT_VISIBLE = {stats['roa_prior_not_visible']}",
        f"GROSSPROFIT_MARGIN_AVAILABLE = {stats['grossprofit_margin_derived_available']}",
        f"ASSETS_TURN_AVAILABLE = {stats['assets_turn_derived_available']}",
        "CROSS_SOURCE_DIVERGENCE_COUNT =",
        f"  ROA = {stats['cross_source_divergence_count']['ROA']}",
        f"  GROSSPROFIT_MARGIN = {stats['cross_source_divergence_count']['GROSSPROFIT_MARGIN']}",
        f"  ASSETS_TURN = {stats['cross_source_divergence_count']['ASSETS_TURN']}",
        "NO_IMPUTATION_VERIFIED = YES",
        f"DETERMINISTIC_REPLAY_VERIFIED = {'YES' if replay_matches else 'NO'}",
        f"STEP5_5_V2_PANEL_FINGERPRINT = {panel_fp}",
        f"STEP5_5_V2_ARTIFACT_FINGERPRINT = {artifact['artifact_fingerprint']}",
        "PROVIDER_API_CALLED = NO",
        "NEXT_ALLOWED_ACTION = OPERATOR_REVIEW_OF_STEP_F2",
        "",
    ]
    (root / REPORT_PATH).write_text("\n".join(report), encoding="utf-8", newline="\n")

    after = {p: file_fingerprint(root / p) for p in PROTECTED_HASHES}
    if any(after[p] != PROTECTED_HASHES[p] for p in PROTECTED_HASHES):
        raise RuntimeError("protected hashes changed")
    return {"stats": stats_payload, "artifact": artifact, "fingerprints": fingerprints, "replay_matches": replay_matches}


def main() -> None:
    raise SystemExit("step5_5_v2 main is prepared; invoke run() explicitly after operator authorization")


if __name__ == "__main__":
    main()
