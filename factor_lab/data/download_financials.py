# -*- coding: utf-8 -*-
r"""下载 Tushare 财报数据，补齐 F-score 需要的 18 个缺失字段。

账号限制（2026-09-19 实测）：
    income / balancesheet / cashflow / fina_indicator 四个接口在本账号下**必须传 ts_code**，
    按 period 批量抓取会返回「必填参数, ts_code」。
    因此采用逐股票策略：一次调用取一只股票的全部报告期，再在本地按年分片。

限流（2026-09-19 修复）：
    账号上限 200 次/分钟。单次调用后固定休眠 0.35s，约 171 次/分钟，留出安全余量。

重试：
    失败后指数退避，依次等待 60 / 120 / 300 / 600 / 1800 秒，最多 6 次尝试。
    仍失败则把该股票写入 data\skipped_stocks.json 并跳过；下次运行会优先补跑。

断点续传（细化到股票）：
    读取已有分片中的 ts_code 集合，只对缺失的股票发请求，再把新数据合并回分片。
    因此先前失败或跳过的股票会自动补上，已完成的股票不会被重复抓取。

用法：
    python download_financials.py --probe                          # 只探测，不写文件
    python download_financials.py --download --yes                 # 下载
    python download_financials.py --download --yes --limit-stocks 50
    python download_financials.py --download --yes --max-attempts 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import tushare as ts

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"
STOCK_BASIC = CACHE / "stock_basic.parquet"
SKIPPED_FILE = DATA_DIR / "skipped_stocks.json"

INTERFACES = ("income", "balancesheet", "cashflow", "fina_indicator")
YEARS = tuple(range(2015, 2027))
WINDOW_END = "2026-09-18"
CALL_SLEEP = 0.35
RETRY_WAITS = (60, 120, 300, 600, 1800)
PROGRESS_EVERY = 100
FLUSH_EVERY = 500
PROBE_SAMPLES = ("000001.SZ", "600000.SH", "300750.SZ")

KEEP_FIELDS = {
    "income": ("n_income", "oper_cost", "revenue", "total_revenue"),
    "balancesheet": ("total_assets", "total_liab", "total_cur_assets", "total_cur_liab", "total_share"),
    "cashflow": ("n_cashflow_act",),
    "fina_indicator": ("roe", "roa", "roe_waa", "netprofit_margin", "grossprofit_margin",
                       "assets_turn", "eps", "bps", "netprofit_yoy"),
}
KEY_FIELDS = ("ts_code", "end_date", "ann_date", "f_ann_date")


def log(message: str) -> None:
    print(message, flush=True)


def get_pro():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        sys.exit("ERROR: 环境变量 TUSHARE_TOKEN 未设置")
    return ts.pro_api(token)


def load_universe() -> list[str]:
    frame = pd.read_parquet(STOCK_BASIC, columns=["ts_code"])
    return sorted(str(code) for code in frame["ts_code"].dropna().unique())


def load_skipped() -> dict:
    if not SKIPPED_FILE.exists():
        return {}
    try:
        data = json.loads(SKIPPED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log("警告: 读取 {} 失败（{}），按空处理".format(SKIPPED_FILE.name, exc))
        return {}


def save_skipped(data: dict) -> None:
    SKIPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: sorted(set(v)) for k, v in sorted(data.items()) if v}
    SKIPPED_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def record_skipped(name: str, code: str) -> None:
    data = load_skipped()
    bucket = set(data.get(name, []))
    bucket.add(code)
    data[name] = sorted(bucket)
    save_skipped(data)


def clear_skipped(name: str, code: str) -> None:
    data = load_skipped()
    bucket = set(data.get(name, []))
    if code in bucket:
        bucket.discard(code)
        data[name] = sorted(bucket)
        save_skipped(data)


def call_api(pro, name: str, ts_code: str, max_attempts: int) -> pd.DataFrame:
    last_error = None
    attempts = max(1, min(max_attempts, len(RETRY_WAITS) + 1))
    for attempt in range(1, attempts + 1):
        try:
            frame = getattr(pro, name)(ts_code=ts_code)
            time.sleep(CALL_SLEEP)
            return frame if frame is not None else pd.DataFrame()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                wait = RETRY_WAITS[min(attempt - 1, len(RETRY_WAITS) - 1)]
                log("    失败 {}/{} {} {} -> {}  等待 {}s 后重试".format(attempt, attempts, name, ts_code, exc, wait))
                time.sleep(wait)
    raise RuntimeError("{} {} 重试 {} 次仍失败: {}".format(name, ts_code, attempts, last_error))


def plan_interface(name: str, universe: list[str]) -> tuple[dict, list[str]]:
    existing: dict[int, set[str]] = {}
    for year in YEARS:
        target = CACHE / "fin_{}_{}.parquet".format(name, year)
        if target.exists():
            codes = pd.read_parquet(target, columns=["ts_code"])["ts_code"].astype(str)
            existing[year] = set(codes)
        else:
            existing[year] = set()
    pending = [code for code in universe if any(code not in existing[year] for year in YEARS)]
    return existing, pending


def flush_buckets(name: str, buckets: dict) -> list:
    """把当前累积的新数据合并进已有分片并写盘，成功写入的年份桶会被清空。"""
    written = []
    for year in YEARS:
        parts = [p for p in buckets[year] if not p.empty]
        if not parts:
            continue
        target = CACHE / "fin_{}_{}.parquet".format(name, year)
        new_rows = pd.concat(parts, ignore_index=True)
        if target.exists():
            merged = pd.concat([pd.read_parquet(target), new_rows], ignore_index=True)
        else:
            merged = new_rows
        merged = merged.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
        merged.to_parquet(target, index=False)
        written.append((target.name, len(merged), target.stat().st_size))
        buckets[year] = []
    return written

def download(pro, universe: list[str], max_attempts: int, limit_stocks: int | None = None) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if limit_stocks:
        universe = universe[:limit_stocks]
        log("小样本模式：只处理前 {} 只股票".format(len(universe)))
    log("开始下载：{} 个接口 × {} 只股票（休眠 {:.2f}s/次，最多 {} 次尝试）".format(
        len(INTERFACES), len(universe), CALL_SLEEP, max_attempts))
    for name in INTERFACES:
        existing, pending = plan_interface(name, universe)
        if not pending:
            log("[{}] 目标股票池已全部到位，跳过".format(name))
            continue
        skipped_now = [c for c in load_skipped().get(name, []) if c in set(pending)]
        rest = [c for c in pending if c not in set(skipped_now)]
        jobs = skipped_now + rest
        log("[{}] 待抓取 {} 只（其中优先补跑历史跳过 {} 只）".format(name, len(jobs), len(skipped_now)))
        buckets: dict[int, list[pd.DataFrame]] = {year: [] for year in YEARS}
        counters = {"success": 0, "fail": 0, "empty": 0}
        started = time.time()
        for index, code in enumerate(jobs, 1):
            try:
                frame = call_api(pro, name, code, max_attempts)
            except Exception as exc:  # noqa: BLE001
                record_skipped(name, code)
                counters["fail"] += 1
                log("    [写入 skipped] {} {} -> {}".format(code, exc))
                continue
            if frame.empty:
                counters["empty"] += 1
                continue
            keep = [c for c in KEY_FIELDS + KEEP_FIELDS[name] if c in frame.columns]
            frame = frame.loc[:, keep].copy()
            frame["end_date"] = frame["end_date"].astype(str)
            years_of = frame["end_date"].str[:4]
            for year in YEARS:
                if code in existing[year]:
                    continue
                part = frame[years_of == str(year)]
                if not part.empty:
                    buckets[year].append(part)
            counters["success"] += 1
            clear_skipped(name, code)
            if index % PROGRESS_EVERY == 0:
                elapsed = time.time() - started
                rate = index / elapsed if elapsed > 0 else 0.0
                eta = (len(jobs) - index) / rate if rate > 0 else 0.0
                log("    进度 {}/{} 已用时 {:.0f}s 剩余约 {:.0f}s 成功 {} 失败 {} 空 {}".format(
                    index, len(jobs), elapsed, eta, counters["success"], counters["fail"], counters["empty"]))
            if index % FLUSH_EVERY == 0:
                for fname, rows, size in flush_buckets(name, buckets):
                    log("    周期落盘 {} rows={} bytes={}".format(fname, rows, size))
        for fname, rows, size in flush_buckets(name, buckets):
            log("  最终落盘 {} rows={} bytes={}".format(fname, rows, size))
        log("[{}] 完成：成功 {} 失败 {} 空 {} 用时 {:.0f}s".format(
            name, counters["success"], counters["fail"], counters["empty"], time.time() - started))

    remaining = load_skipped()
    if remaining:
        log("仍有未抓取成功的股票（见 {}）: {}".format(
            SKIPPED_FILE.name, {k: len(v) for k, v in remaining.items()}))
    else:
        log("无残留跳过记录。")


def probe(pro) -> dict:
    log("=" * 78)
    log("探测模式：逐股票调用（每接口 {} 只样本），不写任何文件".format(len(PROBE_SAMPLES)))
    log("=" * 78)
    outcome = {}
    latencies = []
    for name in INTERFACES:
        rows_seen = []
        columns = []
        missing = []
        date_fields = []
        for code in PROBE_SAMPLES:
            started = time.time()
            try:
                frame = call_api(pro, name, code, 2)
            except Exception as exc:  # noqa: BLE001
                log("[FAIL] {:<16} {:<10} 错误={}".format(name, code, exc))
                continue
            elapsed = time.time() - started
            latencies.append(elapsed)
            rows_seen.append(len(frame))
            if not columns:
                columns = [str(c) for c in frame.columns]
                missing = [f for f in KEEP_FIELDS[name] if f not in columns]
                date_fields = [f for f in ("ann_date", "f_ann_date") if f in columns]
            log("[OK]   {:<16} {:<10} rows={:<6} 用时={:5.2f}s".format(name, code, len(frame), elapsed))
        if rows_seen:
            outcome[name] = {"avg_rows": sum(rows_seen) / len(rows_seen), "columns": columns,
                             "missing": missing, "date_fields": date_fields}
            log("       -> cols={} 缺失必需字段={} 日期字段={} 平均行数={:.0f}".format(
                len(columns), missing if missing else "无", date_fields, outcome[name]["avg_rows"]))
    outcome["_latency"] = sum(latencies) / len(latencies) if latencies else None
    return outcome


def estimate(outcome: dict) -> None:
    universe_size = len(load_universe())
    calls = universe_size * len(INTERFACES)
    latency = outcome.get("_latency")
    log("")
    log("=" * 78)
    log("数据量与耗时估算")
    log("=" * 78)
    log("股票池规模: {} 只（stock_basic.parquet）".format(universe_size))
    log("调用次数: {} 只 × {} 接口 = {:,} 次".format(universe_size, len(INTERFACES), calls))
    if latency:
        per_call = latency - CALL_SLEEP + CALL_SLEEP
        log("实测单次调用延迟: {:.2f}s（含 {:.2f}s 主动休眠）".format(latency, CALL_SLEEP))
        log("按 0.53s/次估计（0.35 休眠 + 0.18 网络）: {:.1f} 分钟".format(calls * 0.53 / 60.0))
        log("按 200 次/分钟限流估计: {:.1f} 分钟".format(calls / 200.0))
    est_rows = sum(v["avg_rows"] for k, v in outcome.items() if not k.startswith("_"))
    log("预计总行数（全部历史，未按 2015+ 过滤）: 约 {:,} 行".format(int(est_rows * universe_size)))


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 Tushare 财报数据（逐股票策略）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="只探测，不写文件")
    mode.add_argument("--download", action="store_true", help="下载财报数据")
    parser.add_argument("--yes", action="store_true", help="确认执行下载")
    parser.add_argument("--limit-stocks", type=int, default=None, help="只处理前 N 只股票")
    parser.add_argument("--max-attempts", type=int, default=len(RETRY_WAITS) + 1,
                        help="单只股票最大尝试次数，默认 6")
    args = parser.parse_args()

    pro = get_pro()
    if args.probe:
        outcome = probe(pro)
        estimate(outcome)
        log("")
        log("探测结束：未写入任何文件。")
        return 0

    if not args.yes:
        log("下载需要显式确认：请追加 --yes 重新运行。")
        return 2

    universe = load_universe()
    download(pro, universe, args.max_attempts, args.limit_stocks)
    log("下载结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())