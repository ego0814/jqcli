# -*- coding: utf-8 -*-
"""
构建 PIT (Point-In-Time) 逐日 ST 掩码
=====================================
问题: 原实现用 stock_basic 的"当前名称"判断 ST, 把 325 只当前 ST 票的
      **全部历史** 剔出样本 —— 这是前视偏差。同时历史上曾是 ST 但现已摘帽的
      ~783 只票, 其 ST 期间的数据被错误地保留。

做法: 用 namechange 的历史名称区间逐日还原每只股票的真实名称,
      按 start_date ~ end_date 判定当日是否为 ST / PT / 退市整理期。

输出: cache/st_mask.parquet  (index=trade_date, columns=ts_code, bool)
      True = 当日该股为风险警示/退市整理, 应剔出样本
"""
import gc
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
YEARS = list(range(2015, 2027))

# 需要剔除的名称特征: 风险警示(ST/*ST)、特别转让(PT)、退市整理期
ST_PAT = r"ST|PT|退"
FAR = "20991231"


def load_dates_codes():
    dates, codes = set(), set()
    for y in YEARS:
        p = os.path.join(CACHE, f"px_{y}.parquet")
        if not os.path.exists(p):
            continue
        d = pd.read_parquet(p, columns=["trade_date", "ts_code"])
        dates.update(d["trade_date"].astype(str).unique())
        codes.update(d["ts_code"].astype(str).unique())
        del d
        gc.collect()
    return sorted(dates), sorted(codes)


def main():
    print("=" * 66)
    print("构建 PIT 逐日 ST 掩码")
    print("=" * 66)

    dates, codes = load_dates_codes()
    print(f"样本网格: {len(dates)} 交易日 × {len(codes)} 只股票 "
          f"({dates[0]} ~ {dates[-1]})")

    nc = pd.read_parquet(os.path.join(CACHE, "namechange.parquet"))
    nc["start_date"] = nc["start_date"].astype(str)
    nc["end_date"] = nc["end_date"].fillna("").astype(str)
    nc.loc[nc["end_date"].isin(["", "nan", "None", "NaT"]), "end_date"] = FAR

    risk = nc[nc["name"].astype(str).str.contains(ST_PAT, na=False, regex=True)].copy()
    print(f"风险警示/退市整理 名称区间: {len(risk)} 条, "
          f"涉及 {risk['ts_code'].nunique()} 只股票")

    di = np.array(dates, dtype="U8")
    st = pd.DataFrame(False, index=dates, columns=codes)

    # 第一份记录之前视为"未知", 按非 ST 处理 (无法知道, 不作假设)
    unknown_pairs = 0
    hit_intervals = 0
    for r in risk.itertuples(index=False):
        code = str(r.ts_code)
        if code not in st.columns:
            continue
        s, e = str(r.start_date), str(r.end_date)
        sel = (di >= s) & (di <= e)
        n = int(sel.sum())
        if n:
            st.iloc[np.flatnonzero(sel), st.columns.get_loc(code)] = True
            hit_intervals += 1

    # 统计"样本期内该股票第一份名称记录尚未覆盖"的股票数
    first_start = nc.groupby("ts_code")["start_date"].min()
    early = 0
    for code in codes:
        fs = first_start.get(code)
        if fs is None or fs > dates[0]:
            early += 1
    unknown_pairs = early * len(dates)

    print(f"生效区间数: {hit_intervals}")
    print(f"样本期内'名称记录未覆盖起始段'的股票: {early} 只 (按非 ST 处理)")

    st_path = os.path.join(CACHE, "st_mask.parquet")
    st.to_parquet(st_path)
    print(f"\n已写出 {st_path}  ({st.shape[0]}×{st.shape[1]})")

    print(f"\nST 状态占据的 股票×日 单元: {int(st.to_numpy().sum()):,} / {st.size:,} "
          f"= {st.to_numpy().mean()*100:.2f}%")

    per_day = st.sum(axis=1)
    print(f"每日 ST 只数: 均值 {per_day.mean():.0f}, "
          f"最小 {per_day.min()}, 最大 {per_day.max()}")
    print("\n按年 (每日 ST 只数中位数):")
    byyear = per_day.groupby(per_day.index.str[:4]).median()
    print(byyear.rename("ST只数中位数").to_string())

    # ---- 与旧口径对比 ----
    print("\n" + "=" * 66)
    print("新旧口径差异")
    print("=" * 66)
    sb = pd.read_parquet(os.path.join(CACHE, "stock_basic.parquet"))
    old_static = set(sb.loc[sb["name"].astype(str).str.contains("ST", na=False), "ts_code"])
    old_cols = [c for c in codes if c in old_static]
    print(f"旧口径(当前快照): 剔除 {len(old_cols)} 只票的**全部**历史")
    print(f"新口径(PIT):     逐日剔除, 累计涉及 "
          f"{int((st.sum(axis=0) > 0).sum())} 只票")

    # 旧口径多剔的 股票×日 (在其非 ST 期间被误弃)
    if old_cols:
        wrongly_dropped = int((~st[old_cols]).to_numpy().sum())
        print(f"  -> 旧口径误弃(实际非ST却被剔除) 股票×日: {wrongly_dropped:,}")
    # 旧口径漏剔的 股票×日 (ST 期间却被保留)
    all_cols = [c for c in codes if c not in old_static]
    wrongly_kept = int(st[all_cols].to_numpy().sum())
    print(f"  -> 旧口径漏剔(ST期间却被保留) 股票×日: {wrongly_kept:,}")


if __name__ == "__main__":
    main()
