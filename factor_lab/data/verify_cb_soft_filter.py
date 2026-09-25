# -*- coding: utf-8 -*-
r"""验证 CB 双低的"软过滤"是否保留。

软过滤定义：
    1. 剔除折价债（转股溢价率 <= 0）
    2. 剔除价格 < 80 元的债

对比：
    - 纯 40% 分位（无软过滤）
    - 40% 分位 + 软过滤

决策：
    软过滤后 t >= 1.5 → 采用软过滤版本
    否则 → 采用纯 40% 分位版本

数据来源：cb_double_low_pit.parquet（已含 cb_close / premium_rate / conv_price，PIT 口径来自聚宽）。
注意：本脚本**不应用** AkShare 强赎过滤，避免混淆两个变量。
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
OUT_DIR = LAB_DIR / "output" / "factor_values"
VENV_PY = DATA_DIR.parent / ".venv" / "Scripts" / "python.exe"


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    src = OUT_DIR / "cb_double_low_pit.parquet"
    f = pd.read_parquet(src)
    log("输入: {} 行={:,} 期={:,} 转债={:,}".format(
        src.name, len(f), f["rebalance_date"].nunique(), f["symbol"].nunique()))

    discount = f[f["premium_rate"] <= 0]
    cheap = f[f["cb_close"] < 80]
    log("软过滤明细：折价债（溢价率 <= 0）{:,} 行 / {} 只；价格 < 80 元 {:,} 行 / {} 只".format(
        len(discount), discount["symbol"].nunique(), len(cheap), cheap["symbol"].nunique()))

    kept = f[(f["premium_rate"] > 0) & (f["cb_close"] >= 80)].copy()
    log("软过滤后: {:,} 行（剔除 {:,} 行，占 {:.1%}）/ {} 只".format(
        len(kept), len(f) - len(kept), (len(f) - len(kept)) / len(f), kept["symbol"].nunique()))
    dst = OUT_DIR / "cb_double_low_pit_soft.parquet"
    kept.to_parquet(dst, index=False)
    log("已写出 {}".format(dst.name))
    log("每期只数：过滤前 {:.0f} -> 过滤后 {:.0f}".format(
        len(f) / f["rebalance_date"].nunique(), len(kept) / kept["rebalance_date"].nunique()))

    # 重扫（软过滤版本）
    cmd = [
        str(VENV_PY), str(DATA_DIR / "quantile_alpha_scan.py"),
        "--input", str(dst), "--factor-col", "cb_double_low_value", "--ascending",
        "--cache-returns", str(OUT_DIR / "cb_returns_21d.parquet"),
        "--pre-filter", "--min-amount", "5000000",
        "--liq-file", str(OUT_DIR / "cb_liq_avg_amount_20d.parquet"),
        "--output", str(OUT_DIR / "cb_soft_filter_scan.csv"),
    ]
    log("\n=== 重扫（软过滤版本）===")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(proc.stdout, flush=True)
    if proc.returncode != 0:
        log("扫描失败: {}".format(proc.stderr[-500:]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
