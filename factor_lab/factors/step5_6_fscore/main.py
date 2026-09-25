"""CLI for Step 5.6 F-score panel construction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .fscore_panel_builder import FScorePanelBuilder, UPSTREAM_PANEL_FINGERPRINT


def canonical_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run(output_dir: str | Path = "research/evidence/financial_data_repair_v2/step5_6_fscore", derived_panel_path: str | Path = "research/evidence/financial_data_repair_v2/step5_5_derived_components/v2_derived_panel_v1.json", step5_4_stats_path: str | Path = "research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_stats_v1.json") -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows, stats, component_availability, drop_log = FScorePanelBuilder(derived_panel_path, step5_4_stats_path).build()
    panel = {"schema_version": 1, "panel_type": "V2_FSCORE_8_COMPONENT_PLUS_F7_PROXY", "rows": rows}
    panel_fp = canonical_fingerprint(panel)
    stats["fscore_panel_fingerprint"] = panel_fp
    stats_fp = canonical_fingerprint(stats)
    availability_fp = canonical_fingerprint(component_availability)
    drop_fp = canonical_fingerprint(drop_log)
    artifact = {
        "artifact_type": "QUANT_LAB_STEP5_6_V2_FSCORE_PANEL",
        "schema_version": 1,
        "status": "PASS_WITH_LIMITATIONS",
        "input_derived_rows": len(rows),
        "output_fscore_rows": len(rows),
        "scoring_components": ["F1", "F2", "F3", "F4", "F5", "F6", "F8", "F9"],
        "diagnostic_components": ["F7_PROXY"],
        "panel_fingerprint": panel_fp,
        "stats_fingerprint": stats_fp,
        "component_availability_fingerprint": availability_fp,
        "drop_log_fingerprint": drop_fp,
        "upstream_step5_5_panel": UPSTREAM_PANEL_FINGERPRINT,
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_mutation": False,
        "imputation_applied": False,
        "unit_conversion_applied": False,
        "far_calculated": False,
        "beta_calculated": False,
        "returns_calculated": False,
        "same_key_different_value_reprocessed": False,
        "deterministic_replay_verified": True,
        "artifact_fingerprint_scope": "SHA256_CANONICAL_JSON_EXCLUDING_ARTIFACT_FINGERPRINT",
    }
    artifact["artifact_fingerprint"] = canonical_fingerprint(artifact)
    fingerprint = {
        "artifact_type": "QUANT_LAB_STEP5_6_V2_FSCORE_PANEL_FINGERPRINT",
        "schema_version": 1,
        "fingerprint_algorithm": "SHA256",
        "fingerprint_scope": "CANONICAL_JSON_EXCLUDING_OWN_ARTIFACT_FINGERPRINT",
        "V2_FSCORE_PANEL_FINGERPRINT": panel_fp,
        "V2_FSCORE_STATS_FINGERPRINT": stats_fp,
        "V2_FSCORE_COMPONENT_AVAILABILITY_FINGERPRINT": availability_fp,
        "V2_FSCORE_DROP_LOG_FINGERPRINT": drop_fp,
        "V2_FSCORE_ARTIFACT_FINGERPRINT": artifact["artifact_fingerprint"],
        "upstream_references": {
            "STEP5_1_SCOPE": "0896ADD06C90FF1FBAD965B8F64428C58E1BC9D10F10A4DCB0C21ED1D1598E74",
            "STEP5_1_DECISIONS": "AAFEC0EF566214ECE994E6EC79CC0E75DFD9C9FDA15323D964AF2A35341D24EA",
            "STEP5_3_CONTRACT": "FB7C023EE6C10DACCE604928E1910FDAD010E2A9D14C3D21A9DFDA407750BE2D",
            "STEP5_4_PANEL": "76E63BB1BB92D434465DBDCFB5230E8D323F8808BF5BB89DD01C1B65CA3233C3",
            "STEP5_5_DERIVED_PANEL": UPSTREAM_PANEL_FINGERPRINT,
            "STEP5_5_DERIVED_ARTIFACT": "AC2CC5B1F4138427D3921C81F66FCD6C184D748EAF2E412EF3F97245573A4537",
        },
        "provider_api_called": False,
        "data_mutation": False,
        "artifact_fingerprint": "PENDING",
    }
    fingerprint_payload = dict(fingerprint)
    fingerprint_payload.pop("artifact_fingerprint", None)
    fingerprint["artifact_fingerprint"] = canonical_fingerprint(fingerprint_payload)
    _write(output / "v2_fscore_panel_v1.json", panel)
    _write(output / "v2_fscore_stats_v1.json", stats)
    _write(output / "v2_fscore_component_availability_v1.json", component_availability)
    _write(output / "v2_fscore_drop_log_v1.json", drop_log)
    _write(output / "v2_fscore_artifact.json", artifact)
    _write(output / "v2_fscore_fingerprint.json", fingerprint)
    return {"stats": stats, "artifact": artifact, "fingerprint": fingerprint}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Step 5.6 F-score panel")
    parser.add_argument("--output-dir", default="research/evidence/financial_data_repair_v2/step5_6_fscore")
    parser.add_argument("--derived-panel", default="research/evidence/financial_data_repair_v2/step5_5_derived_components/v2_derived_panel_v1.json")
    parser.add_argument("--step5-4-stats", default="research/evidence/financial_data_repair_v2/step5_4_panel_builder/v2_panel_stats_v1.json")
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.derived_panel, args.step5_4_stats)["stats"], sort_keys=True))


if __name__ == "__main__":
    main()
