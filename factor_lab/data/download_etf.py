# -*- coding: utf-8 -*-
r"""下载 7 只 ETF 的日行情（后复权）到本地缓存（新建/增量追加）。

优先 AkShare（免积分）：
    fund_etf_hist_em(adjust="hfq")，失败则 fund_lof_hist_em（162411 是 LOF），
    仍失败则回退 Tushare fund_daily + fund_adj（手工算后复权）。

输出：factor_lab/data/cache/etf_daily.parquet
列：code（聚宽格式，如 510500.XSHG）, date, open, close, high, low, volume, amount, source
"""
from __future__ import annotations
import argparse
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"


def parse_args():
    ap = argparse.ArgumentParser(description="下载 7 只 ETF 日行情（后复权）；支持增量追加")
    ap.add_argument("--start", default="20180101", help="起始日 YYYYMMDD（默认 20180101）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（默认今天）")
    return ap.parse_args()


ARGS = parse_args()
START = ARGS.start
END = ARGS.end or datetime.now().strftime("%Y%m%d")

POOL = {
    "510500": "510500.XSHG", "512800": "512800.XSHG", "513100": "513100.XSHG",
    "513050": "513050.XSHG", "518880": "518880.XSHG", "162411": "162411.XSHE",
    "511260": "511260.XSHG",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def _norm(df: pd.DataFrame, code6: str, jq: str, source: str) -> pd.DataFrame:
    rename = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
              "最低": "low", "成交量": "volume", "成交额": "amount"}
    out = df.rename(columns=rename)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["code"] = jq
    out["source"] = source
    keep = ["code", "date", "open", "close", "high", "low", "volume", "amount", "source"]
    return out[[c for c in keep if c in out.columns]]


def fetch_akshare(code6: str) -> tuple[pd.DataFrame | None, str]:
    import akshare as ak
    for attempt in range(3):
        for fn, tag in ((ak.fund_etf_hist_em, "akshare-etf-hfq"), (ak.fund_lof_hist_em, "akshare-lof-hfq")):
            try:
                df = fn(symbol=code6, period="daily", start_date=START, end_date=END, adjust="hfq")
                if df is not None and not df.empty:
                    return df, tag
            except Exception as exc:
                last = "{}: {}".format(tag, str(exc)[:90])
        time.sleep(2.0 * (attempt + 1))
    return None, last if 'last' in dir() else "akshare 失败"


def fetch_tushare(code6: str) -> tuple[pd.DataFrame | None, str]:
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return None, "无 TUSHARE_TOKEN"
    pro = ts.pro_api(token)
    suffix = ".SH" if code6.startswith(("5", "6")) else ".SZ"
    ts_code = code6 + suffix
    try:
        px = pro.fund_daily(ts_code=ts_code, start_date=START, end_date=END)
        adj = pro.fund_adj(ts_code=ts_code, start_date=START, end_date=END)
        if px is None or px.empty:
            return None, "tushare fund_daily 空"
        px = px[["trade_date", "open", "close", "high", "low", "vol", "amount"]].copy()
        px["trade_date"] = px["trade_date"].astype(str)
        px["date"] = px["trade_date"].str[:4] + "-" + px["trade_date"].str[4:6] + "-" + px["trade_date"].str[6:8]
        if adj is not None and not adj.empty:
            a = adj.copy()
            a["trade_date"] = a["trade_date"].astype(str)
            px = px.merge(a[["trade_date", "adj_factor"]], on="trade_date", how="left")
            px["adj_factor"] = px["adj_factor"].ffill().bfill().fillna(1.0)
            for col in ("open", "close", "high", "low"):
                px[col] = px[col] * px["adj_factor"]      # 后复权
        else:
            log("  警告：{} 无 fund_adj，使用不复权价".format(ts_code))
        return px.rename(columns={"vol": "volume"}), "tushare-fund_daily-hfq"
    except Exception as exc:
        return None, "tushare: {}".format(str(exc)[:90])


def main() -> int:
    # 刷新时起始日要覆盖缓存里已有的最早日期，否则新老数据源口径不同时会在拼接处留下标度接缝
    global START
    dst = CACHE / "etf_daily.parquet"
    old = None
    pinned = {}
    if dst.exists():
        try:
            old = pd.read_parquet(dst)
            old["date"] = old["date"].astype(str)
            _mn = old["date"].str.replace("-", "", regex=False).str[:8].min()
            if _mn and _mn < START:
                log("缓存最早日 = {} < --start {} → 起始日前移到 {}".format(_mn, START, _mn))
                START = _mn
            pinned = (old.groupby("code")["source"]
                         .agg(lambda s: s.value_counts().idxmax()).to_dict())
            log("按缓存固定数据源（避免新旧混源产生标度接缝）: {}".format(pinned))
        except Exception as exc:
            log("读旧缓存失败（{}），按新建处理".format(type(exc).__name__))
            old, pinned = None, {}
    frames, failed = [], []
    for code6, jq in POOL.items():
        want = pinned.get(jq)
        if want and want.startswith("tushare"):
            df, tag = fetch_tushare(code6)
            if df is None:
                log("{} 固定源 Tushare 失败（{}），回退 AkShare".format(code6, tag))
                df, tag = fetch_akshare(code6)
        else:
            df, tag = fetch_akshare(code6)
            if df is None:
                log("{} AkShare 失败（{}），改试 Tushare".format(code6, tag))
                df, tag = fetch_tushare(code6)
        if df is None:
            failed.append((code6, tag))
            log("{} 全部失败: {}".format(code6, tag))
            continue
        frames.append(_norm(df, code6, jq, tag))
        log("{} -> {} 行 / {} ~ {} / {}".format(code6, len(df), df.iloc[0, 0], df.iloc[-1, 0], tag))
        time.sleep(1.0)
    if not frames:
        log("没有任何数据")
        return 1
    new = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code", "date"], keep="last")
    merged = new
    if old is not None:
        new_src = new.groupby("code")["source"].first().to_dict()
        reset = sorted([c for c, s in new_src.items() if pinned.get(c) and pinned[c] != s])
        keep_old = old
        if reset:
            log("  [warn] 以下标的本次换源，整段替换旧行（不做新旧混源）: {}".format(
                {c: "{} -> {}".format(pinned[c], new_src[c]) for c in reset}))
            keep_old = old[~old["code"].isin(reset)].copy()
            for c in reset:
                _o = old[old["code"] == c]
                _n = new[new["code"] == c]
                log("    {}: 旧 {} ~ {} → 新 {} ~ {}".format(
                    c, _o["date"].min(), _o["date"].max(), _n["date"].min(), _n["date"].max()))
        common = keep_old[["code", "date", "close"]].merge(
            new[["code", "date", "close"]], on=["code", "date"], suffixes=("_old", "_new"))
        if len(common):
            rel = (common["close_new"] - common["close_old"]).abs() / common["close_old"].abs()
            n_diff = int((rel > 1e-6).sum())
            log("  重叠 (code,date) {} 个；close 与旧文件不同 {} 个（最大相对差 {:.6g}）".format(
                len(common), n_diff, float(rel.max())))
            if n_diff:
                log("  [warn] 重叠行已被新值覆盖；注意本文件的 close 不是可成交价")
        merged = pd.concat([keep_old, new], ignore_index=True)
        merged = (merged.drop_duplicates(["code", "date"], keep="last")
                        .sort_values(["code", "date"]).reset_index(drop=True))
        log("  合并：旧 {} 行 + 新 {} 行 -> {} 行（净增 {}）".format(
            len(keep_old), len(new), len(merged), len(merged) - len(keep_old)))
    else:
        log("  缓存不存在 -> 新建")
    # 混源体检：同一标的跨数据源时，源切换日会带一次性假收益
    for code, g in merged.groupby("code"):
        g = g.sort_values("date").reset_index(drop=True)
        if g["source"].nunique() > 1:
            for i in g.index[g["source"].ne(g["source"].shift())]:
                if i == 0:
                    continue
                r = g["close"].iloc[i] / g["close"].iloc[i - 1] - 1
                log("  [warn] {} 源切换 {} {:.4f}（{}）→ {} {:.4f}（{}），隐含 {:+.2%}".format(
                    code, g["date"].iloc[i - 1], g["close"].iloc[i - 1], g["source"].iloc[i - 1],
                    g["date"].iloc[i], g["close"].iloc[i], g["source"].iloc[i], r))
    merged.to_parquet(dst, index=False)
    log("\n写出 {} 行={:,} 只={} 大小={:.2f} MB".format(
        dst.name, len(merged), merged["code"].nunique(), dst.stat().st_size / 1e6))
    log("来源分布: {}".format(merged.groupby("source").size().to_dict()))
    if failed:
        log("失败: {}".format(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
