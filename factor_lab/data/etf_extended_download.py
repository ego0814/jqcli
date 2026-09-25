# -*- coding: utf-8 -*-
r"""下载 20 只 ETF 日线 -> factor_lab/data/cache/etf_extended_daily.parquet（新建/增量追加）。

数据源：Tushare fund_daily（原始 OHLC + vol + amount）+ fund_adj（复权因子）。
价格列做**前复权**：price_qfq = raw * adj_factor / adj_factor_last
amount 保持 Tushare 原单位（千元），不乘 1000。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import tushare as ts

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "factor_lab" / "data" / "cache"
DST = CACHE / "etf_extended_daily.parquet"
MANIFEST = REPO / "factor_lab" / "output" / "etf_pool_20" / "download_manifest.json"

def parse_args():
    ap = argparse.ArgumentParser(description="下载 20 只 ETF 日线（前复权）；支持增量追加")
    ap.add_argument("--start", default="20160101", help="起始日 YYYYMMDD（默认 20160101）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（默认今天）")
    return ap.parse_args()


ARGS = parse_args()
START = ARGS.start
END = ARGS.end or datetime.now().strftime("%Y%m%d")

ORIG15 = ["510500", "512800", "513100", "513050", "518880", "162411", "511260",
          "159915", "588000", "512880", "512010", "159928", "515000", "513500", "159980"]
NEW5 = ["510300", "510050", "511880", "512890", "511020"]
ALL20 = ORIG15 + NEW5


def jq(code: str) -> str:
    return code + (".SH" if code.startswith(("5", "6")) else ".SZ")


def jq_full(code: str) -> str:
    return code + (".XSHG" if code.startswith(("5", "6")) else ".XSHE")


def main() -> int:
    pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN"))
    frames, manifest = [], {}
    for code in ALL20:
        tc = jq(code)
        daily = pro.fund_daily(ts_code=tc, start_date=START, end_date=END)
        if daily is None or daily.empty:
            raise SystemExit("STOP: {} {} 无 fund_daily 数据".format(code, tc))
        daily = daily.sort_values("trade_date").reset_index(drop=True)
        adj = pro.fund_adj(ts_code=tc, start_date=START, end_date=END)
        if adj is None or adj.empty:
            raise SystemExit("STOP: {} {} 无 fund_adj 数据".format(code, tc))
        adj = adj.sort_values("trade_date").drop_duplicates("trade_date")
        n_before = len(daily)
        m = daily.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
        n_missing_adj = int(m["adj_factor"].isna().sum())
        m["adj_factor"] = m["adj_factor"].ffill().bfill()
        last_factor = float(m["adj_factor"].iloc[-1])
        for col in ("open", "high", "low", "close"):
            m[col] = (pd.to_numeric(m[col], errors="coerce") * m["adj_factor"] / last_factor).round(6)
        m["code"] = jq_full(code)
        m["date"] = m["trade_date"].astype(str).str[:4] + "-" + m["trade_date"].astype(str).str[4:6] + "-" + m["trade_date"].astype(str).str[6:8]
        m["volume"] = pd.to_numeric(m["vol"], errors="coerce")
        out = m[["code", "date", "open", "high", "low", "close", "volume", "amount", "adj_factor"]].copy()
        out["source"] = "tushare-fund_daily+fund_adj-qfq"
        frames.append(out)
        manifest[code] = {"ts_code": tc, "jq_code": jq_full(code), "rows": len(out),
                          "date_min": out["date"].min(), "date_max": out["date"].max(),
                          "rows_missing_adj": n_missing_adj, "adj_last": last_factor,
                          "raw_rows": n_before}
        print("  {:<8} rows={:<5} {} ~ {}  adj 缺失填充={}".format(
            code, len(out), out["date"].min(), out["date"].max(), n_missing_adj), flush=True)

    full = pd.concat(frames, ignore_index=True)
    full = full.sort_values(["code", "date"]).reset_index(drop=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    merged = full
    if DST.exists():
        old = pd.read_parquet(DST)
        old["date"] = old["date"].astype(str)
        full["date"] = full["date"].astype(str)
        common = old[["code", "date", "close"]].merge(
            full[["code", "date", "close"]], on=["code", "date"], suffixes=("_old", "_new"))
        if len(common):
            rel = (common["close_new"] - common["close_old"]).abs() / common["close_old"].abs()
            n_diff = int((rel > 1e-6).sum())
            print("  重叠 (code,date) {} 个；close 与旧文件不同 {} 个（最大相对差 {:.6g}）".format(
                len(common), n_diff, float(rel.max())))
            if n_diff:
                print("  [warn] 重叠行已被新值覆盖。若复权锚点 adj_last 变化，"
                      "整段历史标度会位移；请用完整窗口（默认 --start 20160101）刷新")
        merged = pd.concat([old, full], ignore_index=True)
        merged = (merged.drop_duplicates(["code", "date"], keep="last")
                        .sort_values(["code", "date"]).reset_index(drop=True))
        print("  合并：旧 {} 行 + 新 {} 行 -> {} 行（净增 {}）".format(
            len(old), len(full), len(merged), len(merged) - len(old)))
    else:
        print("  缓存不存在 -> 新建")
    merged.to_parquet(DST, index=False)

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "tushare fund_daily + fund_adj", "price_adjust": "qfq（前复权）",
        "amount_unit": "千元（Tushare 原单位，未换算）",
        "window": [START, END], "codes": ALL20, "per_code": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("总行数: {:,}  只数: {}".format(len(merged), merged["code"].nunique()))
    print("写出:", DST)
    print("清单:", MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())