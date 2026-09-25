# -*- coding: utf-8 -*-
r"""F-score 因子计算：读 panel → 调 FScorePanelBuilder → 输出因子表。

输入（默认）：output\factor_values\fscore_panel.parquet
输出（默认）：output\factor_values\fscore.parquet

处理规则：
    只处理 row_status == 'OK' 的行（ST 与 NO_VISIBLE_ANNUAL 行不参与计算）
    计算层再按 period_kind == 'ANNUAL' 过滤（由 FScorePanelBuilder 内部完成）
    按 symbol 分块（--chunk-symbols），块内保留全部历史以便 prior 期解析，控制内存峰值

输出列：
    symbol, rebalance_date, period_end_date, f_score, f1..f9, f7_proxy,
    complete, partial_score, pit_status

用法：
    python compute_fscore.py --input output\factor_values\fscore_panel_sample.parquet --output output\factor_values\fscore_sample.parquet
    python compute_fscore.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(LAB_DIR))

from factors.step5_6_fscore.fscore_panel_builder import FScorePanelBuilder  # noqa: E402

SCORING = ("F1", "F2", "F3", "F4", "F5", "F6", "F8", "F9")
DEFAULT_OUTPUT_COLUMNS = ["symbol", "rebalance_date", "period_end_date", "f_score",
                          "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9",
                          "f7_proxy", "complete", "partial_score", "pit_status"]


def log(message: str) -> None:
    print(message, flush=True)


def as_list(value) -> list:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


def unflatten(flat: dict) -> dict:
    financial = {}
    for key, value in flat.items():
        if key.startswith("financial."):
            _, field, sub = key.split(".", 2)
            financial.setdefault(field, {})[sub] = None if (isinstance(value, float) and pd.isna(value)) else value
    return {
        "symbol": flat["symbol"],
        "rebalance_date": flat["rebalance_date"],
        "period_kind": flat["period_kind"],
        "period_end_date": flat["period_end_date"],
        "financial": financial,
        "price": {"close_raw": flat.get("price.close_raw"), "close_raw_date": flat.get("price.close_raw_date")},
        "asof": {k.split(".", 1)[1]: flat.get(k) for k in flat if k.startswith("asof.")},
        "row_status": flat["row_status"],
        "drop_reasons": as_list(flat.get("drop_reasons")),
        "fail_closed_conditions": as_list(flat.get("fail_closed_conditions")),
        "pit_status": flat.get("pit_status"),
        "partial_score": flat.get("partial_score"),
    }


def flatten(result: dict) -> dict:
    components = result["components"]
    row = {
        "symbol": result["symbol"],
        "rebalance_date": result["rebalance_date"],
        "period_end_date": result["period_end_date"],
        "f_score": result["aggregated"]["total_score_8"],
        "f7": None,
        "f7_proxy": result["F7_PROXY"]["value"],
        "complete": result["aggregated"]["complete"],
        "partial_score": result.get("partial_score"),
        "pit_status": result.get("pit_status"),
    }
    for name in SCORING:
        row[name.lower()] = components[name]["value"]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="计算 F-score 因子")
    parser.add_argument("--input", default=str(OUT_DIR / "fscore_panel.parquet"))
    parser.add_argument("--output", default=str(OUT_DIR / "fscore.parquet"))
    parser.add_argument("--chunk-symbols", type=int, default=200, help="每批处理的股票数，控制内存峰值")
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    log("输入: {} ({:.1f} MB)".format(src.name, src.stat().st_size / 1024 / 1024))

    symbols = sorted(set(pd.read_parquet(src, columns=["symbol"])["symbol"]))
    log("panel 唯一股票数: {}".format(len(symbols)))
    builder = FScorePanelBuilder(expected_row_count=None, expected_drop_count=None)

    rows_out: list[dict] = []
    total_ok = 0
    total_skipped = 0
    for start in range(0, len(symbols), args.chunk_symbols):
        batch = symbols[start:start + args.chunk_symbols]
        frame = pd.read_parquet(src, filters=[("symbol", "in", batch)])
        ok = frame[frame["row_status"] == "OK"]
        total_skipped += len(frame) - len(ok)
        if ok.empty:
            continue
        total_ok += len(ok)
        records = [unflatten(r) for r in ok.to_dict("records")]
        output, stats, _, _ = builder.build_from_rows(records)
        rows_out.extend(flatten(r) for r in output)
        log("  批次 {}-{}: 输入 OK {} 行 → F-score {} 行".format(
            start + 1, start + len(batch), len(ok), len(output)))

    if not rows_out:
        log("没有产出任何 F-score 行。")
        return 1

    result_frame = pd.DataFrame(rows_out)[DEFAULT_OUTPUT_COLUMNS]
    dst.parent.mkdir(parents=True, exist_ok=True)
    result_frame.to_parquet(dst, index=False)

    complete = result_frame["complete"]
    scores = Counter(result_frame.loc[complete, "f_score"].dropna().astype(int))
    log("")
    log("输入 OK 行: {:,}  跳过(DROPPED): {:,}".format(total_ok, total_skipped))
    log("输出 F-score 行: {:,}  列数: {}".format(len(result_frame), result_frame.shape[1]))
    log("complete=True: {:,} ({:.2%})".format(int(complete.sum()), complete.mean()))
    log("f_score 分布: {}".format(dict(sorted(scores.items()))))
    log("partial_score 非空: {:,}".format(int(result_frame["partial_score"].notna().sum())))
    log("pit_status: {}".format(dict(result_frame["pit_status"].value_counts())))
    log("输出: {} rows={} bytes={}".format(dst.name, len(result_frame), dst.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())