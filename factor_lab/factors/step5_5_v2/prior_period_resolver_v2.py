"""Streaming prior-period resolution for the v2 derived panel."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..step5_5_derived_components.prior_period_resolver import PriorPeriodResult

CHUNK = 4 * 1024 * 1024


def iter_array_objects(path: str | Path, key: str = "rows") -> Iterator[dict[str, Any]]:
    """Yield objects from a top-level JSON array without loading the whole file."""
    decoder = json.JSONDecoder()
    marker = f'"{key}"'
    with Path(path).open("r", encoding="utf-8") as fh:
        buf = ""
        pos = 0
        started = False
        eof = False
        while True:
            if not started:
                found = buf.find(marker)
                if found == -1:
                    keep = buf[-(len(marker) + 4):]
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        raise ValueError(f"array key {key} not found")
                    buf = keep + chunk
                    continue
                bracket = buf.find("[", found)
                if bracket == -1:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        raise ValueError(f"array key {key} not opened")
                    buf = buf[found:] + chunk
                    continue
                buf = buf[bracket + 1:]
                pos = 0
                started = True
            while True:
                while pos < len(buf) and buf[pos] in " \t\r\n,":
                    pos += 1
                if pos < len(buf):
                    break
                if eof:
                    return
                chunk = fh.read(CHUNK)
                if not chunk:
                    eof = True
                    return
                buf = buf[pos:] + chunk
                pos = 0
            if buf[pos] == "]":
                return
            try:
                obj, end = decoder.raw_decode(buf, pos)
            except ValueError:
                if eof:
                    raise
                chunk = fh.read(CHUNK)
                if not chunk:
                    eof = True
                    raise
                buf = buf[pos:] + chunk
                pos = 0
                continue
            yield obj
            pos = end
            if pos > CHUNK:
                buf = buf[pos:]
                pos = 0


def _period_key(period_end_date: str | None) -> str | None:
    if not period_end_date:
        return None
    try:
        current = date.fromisoformat(str(period_end_date)[:10])
        return f"{current.year - 1:04d}-12-31"
    except ValueError:
        return None


def _visible_before(field: Mapping[str, Any], rebalance_date: str) -> bool:
    if field.get("status") == "CONFLICT":
        return True
    dates = [field.get("source_ann_date"), field.get("source_f_ann_date")]
    present = [str(value)[:10] for value in dates if value not in (None, "", "None")]
    return bool(present) and min(present) <= rebalance_date


def build_prior_index(panel_path: str | Path) -> dict[tuple[str, str], list[tuple[str, str, Any, Any]]]:
    """Compact index of (symbol, period_end_date) -> [(rebalance_date, status, value, identity)]."""
    index: dict[tuple[str, str], list[tuple[str, str, Any, Any]]] = defaultdict(list)
    for row in iter_array_objects(panel_path, "rows"):
        if row.get("row_status") != "OK":
            continue
        field = ((row.get("financial") or {}).get("total_assets")) or {}
        if not field:
            continue
        rebalance = str(row.get("rebalance_date", ""))[:10]
        if not _visible_before(field, rebalance):
            continue
        key = (str(row.get("symbol")), str(row.get("period_end_date"))[:10])
        index[key].append((rebalance, str(field.get("status")), field.get("value"), field.get("source_row_identity")))
    return index


def resolve_prior(current_row: Mapping[str, Any], index: Mapping[tuple[str, str], list[tuple[str, str, Any, Any]]]) -> PriorPeriodResult:
    """Latest prior-period total_assets visible at the current rebalance date."""
    current_period = str(current_row.get("period_end_date") or "")[:10]
    prior_period = _period_key(current_period)
    if prior_period is None:
        return PriorPeriodResult(None, None, "PRIOR_PERIOD_NOT_VISIBLE")
    key = (str(current_row.get("symbol")), prior_period)
    rebalance = str(current_row.get("rebalance_date", ""))[:10]
    candidates = sorted(index.get(key, ()), key=lambda item: item[0], reverse=True)
    for candidate_rebalance, status, value, identity in candidates:
        if candidate_rebalance > rebalance:
            continue
        if status == "CONFLICT":
            return PriorPeriodResult(None, prior_period, "PRIOR_PERIOD_CONFLICT")
        if status == "AVAILABLE" and value is not None:
            synthetic = {"financial": {"total_assets": {"status": "AVAILABLE", "value": value, "source_row_identity": identity}}}
            return PriorPeriodResult(synthetic, prior_period, None)
    return PriorPeriodResult(None, prior_period, "PRIOR_PERIOD_NOT_VISIBLE")
