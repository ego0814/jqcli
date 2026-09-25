# -*- coding: utf-8 -*-
r"""把 12 个 px_*.parquet 合并为单文件 px_merged.parquet。

内存安全：逐个文件读取（每个约 1.3M 行），按 (trade_date, ts_code) 排序后用
ParquetWriter 追加写入，峰值内存只驻留单个年度分片。
由于分片本身按年切分且路径有序，追加写入即得到全局按日期排序的结果。

原始 px_*.parquet 保持不变。
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CACHE = Path(__file__).resolve().parent / "cache"
COLUMNS = ["ts_code", "trade_date", "pct_chg", "vol", "amount"]
ROW_GROUP = 500_000


def main() -> int:
    files = sorted(CACHE.glob("px_*.parquet"))
    files = [f for f in files if f.name != "px_merged.parquet"]
    target = CACHE / "px_merged.parquet"
    t0 = time.time()

    src_rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    src_bytes = sum(f.stat().st_size for f in files)
    print("输入: {} 个分片, {:,} 行, {:.1f} MB".format(len(files), src_rows, src_bytes / 1024 / 1024), flush=True)

    writer = None
    out_rows = 0
    null_before = 0
    null_after = 0
    codes, dates = set(), set()
    try:
        for path in files:
            frame = pd.read_parquet(path, columns=COLUMNS)
            null_before += int(frame["pct_chg"].isna().sum())
            frame = frame.sort_values(["trade_date", "ts_code"], kind="mergesort")
            codes.update(frame["ts_code"].astype(str).unique().tolist())
            dates.update(frame["trade_date"].astype(str).unique().tolist())
            out_rows += len(frame)
            null_after += int(frame["pct_chg"].isna().sum())
            table = pa.Table.from_pandas(frame[COLUMNS], preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(target, table.schema, compression="snappy")
            writer.write_table(table, row_group_size=ROW_GROUP)
            print("  已合并 {}: {:,} 行".format(path.name, len(frame)), flush=True)
    finally:
        if writer is not None:
            writer.close()

    out_bytes = target.stat().st_size
    elapsed = time.time() - t0
    print("")
    print("输出: {} , {:,} 行, {:.1f} MB".format(target.name, out_rows, out_bytes / 1024 / 1024))
    print("压缩率: 源 {:.1f} MB -> 合并 {:.1f} MB（{:.2f}x）".format(
        src_bytes / 1024 / 1024, out_bytes / 1024 / 1024, src_bytes / out_bytes))
    print("耗时: {:.1f}s".format(elapsed))
    print("")
    print("等价性验证:")
    print("  行数一致 : {} ({:,} vs {:,})".format(out_rows == src_rows, out_rows, src_rows))
    print("  空值未变 : {} (源 {:,} vs 合并 {:,})".format(null_before == null_after, null_before, null_after))
    print("  唯一股票 : {:,}".format(len(codes)))
    print("  唯一日期 : {:,}".format(len(dates)))
    ok = (out_rows == src_rows) and (null_before == null_after)
    print("  结论     : {}".format("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
