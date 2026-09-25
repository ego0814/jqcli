# -*- coding: utf-8 -*-
r"""把聚宽 bond.CONBOND_DAILY_CONVERT 的年度数据解码成本地 parquet。

流程（两步）：
    1) 远端：research exec 按月查询该年数据 → gzip+base64 → 打印到 stdout
       （远端的 write_file 不存在，只能走 stdout 回传）
    2) 本地：本脚本读取 <year>.b64.txt → 解码 → cache/cb_convert_<year>.parquet

字段：code, date, convert_price, convert_premium_rate

用法：
    python download_cb_convert.py --decode 2018 2019 ... 2026
"""
from __future__ import annotations
import argparse
import base64
import gzip
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"


def decode_year(year: int) -> pd.DataFrame | None:
    src = CACHE / "_cb_convert_{}.b64.txt".format(year)
    if not src.exists():
        print("[{}] 缺少远端回传文件 {}".format(year, src.name))
        return None
    payload = None
    meta = ""
    for line in src.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if "B64:" in line:
            payload = line.split("B64:", 1)[1].strip()
        elif line.startswith("YEAR="):
            meta = line.strip()
        elif line.startswith("[stdout]") and "B64:" in line:
            payload = line.split("B64:", 1)[1].strip()
    if not payload:
        print("[{}] 未找到 B64 数据".format(year))
        return None
    csv = gzip.decompress(base64.b64decode(payload)).decode("utf-8")
    frame = pd.read_csv(pd.io.common.StringIO(csv), dtype={"code": str, "date": str})
    dst = CACHE / "cb_convert_{}.parquet".format(year)
    frame.to_parquet(dst, index=False)
    print("[{}] {} -> {} 行={:,} 转债={} 日期 {} ~ {}".format(
        year, meta or "(无元信息)", dst.name, len(frame), frame["code"].nunique(),
        frame["date"].min(), frame["date"].max()))
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description="解码聚宽可转债转股价/溢价率数据")
    ap.add_argument("--decode", type=int, nargs="+", required=True, help="要解码的年份")
    args = ap.parse_args()
    frames = [f for y in args.decode if (f := decode_year(y)) is not None]
    if frames:
        allf = pd.concat(frames, ignore_index=True)
        print("合计 {:,} 行 / {} 只 / {} ~ {}".format(
            len(allf), allf["code"].nunique(), allf["date"].min(), allf["date"].max()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
