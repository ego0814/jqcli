# -*- coding: utf-8 -*-
r"""可转债双低的 PIT 稳健性变体。

背景：cb_price_chg（转股价变动历史）无权限，只能用 cb_basic 的**当前** conv_price，
而 944 只里有 930 只（99%）的当前转股价 != 初始转股价（含除权调整与下修）。
因此主口径的历史溢价率是被扭曲的。这里产出两个对照变体：

    cb_double_low_first.parquet  双低值改用 first_conv_price（初始转股价）
    cb_price_only.parquet        只用转债价格（完全 PIT 安全，不含转股价）

两者共用 cb_daily / 正股行情 / 同一套 rebalance 骨架与 valid 规则。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"
sys.path.insert(0, str(DATA_DIR))
from compute_ic import norm_date  # noqa: E402


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    main_table = pd.read_parquet(OUT_DIR / "cb_double_low.parquet")
    basic = pd.read_parquet(CACHE / "cb_basic.parquet")[
        ["ts_code", "first_conv_price"]].drop_duplicates(subset=["ts_code"], keep="last")
    frame = main_table.merge(basic, left_on="symbol", right_on="ts_code", how="left")
    log("主表 {:,} 行，合并 first_conv_price 后 {:,} 行".format(len(main_table), len(frame)))

    # 变体 B：用初始转股价重算溢价率与双低值
    ok = (frame["first_conv_price"] > 0) & (frame["stk_close"] > 0) & (frame["cb_close"] > 0)
    conv_value_first = 100.0 * frame["stk_close"] / frame["first_conv_price"]
    premium_first = frame["cb_close"] / conv_value_first - 1.0
    b = frame[ok].copy()
    b["premium"] = premium_first[ok]
    b["conv_value"] = conv_value_first[ok]
    b["cb_double_low_first_value"] = b["cb_close"] + b["premium"] * 100.0
    b["pit_status"] = "CB_FIRST_CONV_PRICE_APPROX"
    b = b.drop(columns=["ts_code", "first_conv_price"])
    b.to_parquet(OUT_DIR / "cb_double_low_first.parquet", index=False)
    log("变体 B（初始转股价双低）写出 {:,} 行；双低中位 {:.2f}".format(
        len(b), float(b["cb_double_low_first_value"].median())))

    # 变体 C：纯价格（PIT 安全）
    c = frame[ok].copy()
    c["cb_price_value"] = c["cb_close"]
    c["pit_status"] = "PRICE_ONLY"
    c = c.drop(columns=["ts_code", "first_conv_price"])
    c.to_parquet(OUT_DIR / "cb_price_only.parquet", index=False)
    log("变体 C（纯价格）写出 {:,} 行；价格中位 {:.2f}".format(len(c), float(c["cb_price_value"].median())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
