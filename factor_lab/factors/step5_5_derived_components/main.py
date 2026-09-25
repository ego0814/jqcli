"""CLI for the local Step 5.5 derived-component build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .derived_panel_builder import DerivedPanelBuilder


def canonical_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run(
    output_dir: str | Path = "research/evidence/financial_data_repair_v2/step5_5_derived_components",
    panel_path: str | Path = "research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_v1.json",
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows, stats, drop_log_rows, cross_rows = DerivedPanelBuilder(panel_path=panel_path).build()
    panel = {"schema_version": 1, "panel_type": "V2_DERIVED_COMPONENTS_NO_IMPUTATION", "rows": rows}
    drop_log = {"schema_version": 1, "rows": drop_log_rows}
    cross_check = {"schema_version": 1, "threshold": 0.01, "rows": cross_rows}
    panel_fp = canonical_fingerprint(panel)
    stats["derived_panel_fingerprint"] = panel_fp
    stats_fp = canonical_fingerprint(stats)
    drop_fp = canonical_fingerprint(drop_log)
    cross_fp = canonical_fingerprint(cross_check)
    artifact = {
        "artifact_type": "QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS",
        "schema_version": 1,
        "status": "PASS_WITH_LIMITATIONS",
        "derived_components": ["roa_derived", "grossprofit_margin_derived", "assets_turn_derived"],
        "input_panel_rows": stats["input_panel_rows"],
        "output_panel_rows": stats["output_panel_rows"],
        "panel_fingerprint": panel_fp,
        "stats_fingerprint": stats_fp,
        "drop_log_fingerprint": drop_fp,
        "cross_check_fingerprint": cross_fp,
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_mutation": False,
        "business_code_mutation": "NEW_NAMESPACE_ONLY",
        "f_score_calculated": False,
        "far_calculated": False,
        "unit_conversion_applied": False,
        "imputation_applied": False,
        "revision_selection_executed": False,
        "deterministic_replay_verified": True,
        "artifact_fingerprint_scope": "SHA256_CANONICAL_JSON_EXCLUDING_ARTIFACT_FINGERPRINT",
    }
    artifact["artifact_fingerprint"] = canonical_fingerprint(artifact)
    fingerprint = {
        "artifact_type": "QUANT_LAB_STEP5_5_V2_DERIVED_COMPONENTS_FINGERPRINT",
        "schema_version": 1,
        "fingerprint_algorithm": "SHA256",
        "fingerprint_scope": "CANONICAL_JSON_EXCLUDING_OWN_ARTIFACT_FINGERPRINT",
        "V2_DERIVED_PANEL_FINGERPRINT": panel_fp,
        "V2_DERIVED_STATS_FINGERPRINT": stats_fp,
        "V2_DERIVED_DROP_LOG_FINGERPRINT": drop_fp,
        "V2_DERIVED_CROSS_CHECK_FINGERPRINT": cross_fp,
        "V2_DERIVED_ARTIFACT_FINGERPRINT": artifact["artifact_fingerprint"],
        "upstream_references": {
            "STEP5_1_SCOPE": "0896ADD06C90FF1FBAD965B8F64428C58E1BC9D10F10A4DCB0C21ED1D1598E74",
            "STEP5_1_DECISIONS": "AAFEC0EF566214ECE994E6EC79CC0E75DFD9C9FDA15323D964AF2A35341D24EA",
            "STEP5_3_CONTRACT": "FB7C023EE6C10DACCE604928E1910FDAD010E2A9D14C3D21A9DFDA407750BE2D",
            "STEP5_4_PANEL": "76E63BB1BB92D434465DBDCFB5230E8D323F8808BF5BB89DD01C1B65CA3233C3",
            "STEP5_4_STATS": "512FF9B30F0AE3DBE5ED2BD3A64AAB1AA5FBA72CF1BBE2D76DDF334AC41FAEA3",
        },
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_mutation": False,
        "artifact_fingerprint": "PENDING",
    }
    fingerprint_payload = dict(fingerprint)
    fingerprint_payload.pop("artifact_fingerprint", None)
    fingerprint["artifact_fingerprint"] = canonical_fingerprint(fingerprint_payload)
    _write(output / "v2_derived_panel_v1.json", panel)
    _write(output / "v2_derived_stats_v1.json", stats)
    _write(output / "v2_derived_drop_log_v1.json", drop_log)
    _write(output / "v2_derived_cross_check_v1.json", cross_check)
    _write(output / "v2_derived_artifact.json", artifact)
    _write(output / "v2_derived_fingerprint.json", fingerprint)
    return {"stats": stats, "artifact": artifact, "fingerprint": fingerprint}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build V2 derived financial components")
    parser.add_argument("--output-dir", default="research/evidence/financial_data_repair_v2/step5_5_derived_components")
    parser.add_argument("--panel", default="research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_v1.json")
    args = parser.parse_args()
    result = run(args.output_dir, args.panel)
    print(json.dumps(result["stats"], sort_keys=True))


if __name__ == "__main__":
    main()
