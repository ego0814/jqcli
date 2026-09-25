# -*- coding: utf-8 -*-
r"""获取集思录可转债强赎数据（AkShare）并生成"剔除强赎"后的双低因子表。

数据源：ak.bond_cb_redeem_jsl()（集思录公开数据，317 行 / 18 列）
关键字段：代码/名称/现价/剩余规模/转股价/强赎触发比/强赎触发价/强赎价/强赎天计数/强赎条款/强赎状态

**重要 PIT 限制**：该接口返回的是**当前快照**（此刻存续的转债状态），
没有历史。用它对 2018-2026 的历史因子做过滤属于"用未来信息剔除"，
影响面仅限于当前处于强赎状态的少数标的（实测 7 只"已公告强赎"）。
因此本表只能用于：
    1) 实盘/执行层的实时过滤（正确用法）
    2) 历史回测的"稳健性上限测试"（把强赎债剔到底，看 t 变化）

输出：
    cache/cb_redeem_jsl.parquet              强赎原始快照
    output/factor_values/cb_double_low_pit_redeem.parquet
                                             剔除"已公告强赎"与"强赎天计数>=12"后的双低因子
"""
from __future__ import annotations
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
CACHE = DATA_DIR / "cache"
OUT_DIR = LAB_DIR / "output" / "factor_values"


def log(message: str) -> None:
    print(message, flush=True)


def parse_day_count(text) -> int | None:
    """"23/15 | 30" -> 23；其它格式返回 None。"""
    if not isinstance(text, str):
        return None
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", text)
    return int(m.group(1)) if m else None


def main() -> int:
    import akshare as ak

    df = ak.bond_cb_redeem_jsl()
    log("集思录强赎快照: {} 行 / {} 列".format(len(df), len(df.columns)))
    cache_path = CACHE / "cb_redeem_jsl.parquet"
    df.to_parquet(cache_path, index=False)
    log("已写出 {}".format(cache_path.name))

    df["强赎天计数_数值"] = df["强赎天计数"].map(parse_day_count)
    status = df["强赎状态"].fillna("")
    announced = df[status.str.contains("已公告强赎", na=False)]
    counting = df[(df["强赎天计数_数值"].notna()) & (df["强赎天计数_数值"] >= 12)]
    log("已公告强赎: {} 只；强赎天计数 >= 12: {} 只".format(len(announced), len(counting)))

    excl_status = set(announced["代码"].astype(str))
    excl_count = set(counting["代码"].astype(str))
    excl = excl_status | excl_count
    log("合计剔除: {} 只（状态 {} + 计数 {}，去重后）".format(len(excl), len(excl_status), len(excl_count)))

    pit = pd.read_parquet(OUT_DIR / "cb_double_low_pit.parquet")
    pit["code6"] = pit["symbol"].astype(str).str.split(".").str[0]
    before_rows, before_bonds = len(pit), pit["code6"].nunique()
    kept = pit[~pit["code6"].isin(excl)].drop(columns=["code6"])
    log("因子表: {:,} 行 / {} 只 -> 剔除后 {:,} 行 / {} 只".format(
        before_rows, before_bonds, len(kept), kept["symbol"].astype(str).str.split(".").str[0].nunique()))
    dst = OUT_DIR / "cb_double_low_pit_redeem.parquet"
    kept.to_parquet(dst, index=False)
    log("已写出 {}".format(dst.name))

    # 剔除名单样本（便于核对）
    sample = announced[["代码", "名称", "强赎状态", "现价", "强赎天计数"]].head(10)
    log("已公告强赎样本:\n{}".format(sample.to_string(index=False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
