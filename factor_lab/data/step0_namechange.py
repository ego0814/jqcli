# -*- coding: utf-8 -*-
"""
下载全市场证券名称变更历史 (namechange), 用于构建 PIT 正确的 ST 状态。
=====================================================================
关键: 接口的 start_date/end_date 过滤的是 ann_date(公告日),
      因此要拿到完整的历史名称链, 必须把 ann_date 范围放到尽可能宽。
输出: cache/namechange.parquet
"""
import os
import time

import pandas as pd
import tushare as ts

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")

TOKEN = os.environ.get("TUSHARE_TOKEN")
if not TOKEN:
    raise SystemExit("环境变量 TUSHARE_TOKEN 未设置")
ts.set_token(TOKEN)
pro = ts.pro_api()

FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"
out_path = os.path.join(CACHE, "namechange.parquet")

y0, y1 = 1990, 2026
parts = []
failed_years = []
for y in range(y0, y1 + 1):
    s, e = f"{y}0101", f"{y}1231"
    df = pd.DataFrame()
    for attempt in range(4):
        try:
            _d = pro.namechange(start_date=s, end_date=e, fields=FIELDS)
        except Exception as ex:
            print(f"  {y} 第{attempt+1}次失败: {type(ex).__name__}: {str(ex)[:100]}")
            _d = None
        if _d is not None and len(_d):
            df = _d
            break
        time.sleep(1.5 * (attempt + 1))
    if not len(df):
        failed_years.append(y)
    print(f"  {y}: {len(df):5d} 条")
    if len(df):
        parts.append(df)
    time.sleep(0.35)

if failed_years:
    raise SystemExit("ERROR: {} 个年份未取到数据, 不写入: {}".format(
        len(failed_years), failed_years))

if not parts:
    raise SystemExit("未取到任何数据")

nc = pd.concat(parts, ignore_index=True)
nc = nc.drop_duplicates(["ts_code", "name", "start_date", "end_date"], keep="last")
nc = nc.sort_values(["ts_code", "start_date"]).reset_index(drop=True)

# 统一日期为字符串 YYYYMMDD
for c in ["start_date", "end_date", "ann_date"]:
    nc[c] = nc[c].astype(str).str.replace(r"\.0$", "", regex=True)

nc.to_parquet(out_path, index=False)
print()
print(f"合计 {len(nc)} 条, 覆盖 {nc['ts_code'].nunique()} 只股票 -> {out_path}")
print()
print("change_reason 分布 (Top15):")
print(nc["change_reason"].value_counts().head(15).to_string())
print()
# ST 相关记录数
st_like = nc["name"].astype(str).str.contains("ST", na=False)
print(f"名称含 ST 的记录: {int(st_like.sum())} 条, 涉及 {nc.loc[st_like,'ts_code'].nunique()} 只股票")
