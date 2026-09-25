# -*- coding: utf-8 -*-
r"""下载/刷新可转债数据（Tushare cb_basic + cb_daily，AkShare 强赎快照）。

两种模式：

    全量（默认，兜底）：逐只 ts_code 拉全历史（约 1,100 只 / 76 万行 / 数分钟），
        用于首次建库或缓存损坏后的重建。
    增量（--incremental，日常刷新）：按 trade_date 拉"缓存之后"的每个交易日全市场，
        单日约 314 行 / 0.5s，月末刷新只要几秒。

为什么增量按 trade_date 而不是按 ts_code：
    - cb_daily 单次返回上限 2000 行；单日全市场约 314 行，不会被截断
    - 按区间查会截断为最近 2000 行（不可用），逐只拉则要 1,100 次请求
    - 实测（2026-09-22）：单日接口 314 行是本地缓存 311 行的严格超集，
      多出 118059.SH / 123175.SZ / 127068.SZ（逐只全量模式漏掉的标的）
      → 增量模式同时具备"补洞"能力

增量模式同时刷新：
    - cb_basic（一次请求，捕捉新上市转债）
    - cb_redeem_jsl（AkShare 集思录强赎快照；该表是当前快照、无历史，
      仅用于执行层实时过滤）
    - 检查 cb_convert_*（聚宽 PIT 转股价/溢价率）是否滞后，滞后则打印人工刷新指引

不能自动化的部分：
    cb_convert_* 来自聚宽 bond.CONBOND_DAILY_CONVERT（PIT 安全，5000 行/次上限），
    只能在聚宽研究环境抓取回传后本地解码：
        python download_cb_convert.py --decode <年份>
    Tushare 无法替代（cb_price_chg 转股价变动历史本账号无权限）。

输出：
    cache/cb_daily.parquet
    cache/cb_basic.parquet
    cache/cb_redeem_jsl.parquet（增量模式）

用法：
    python download_cb.py                              # 全量（兜底）
    python download_cb.py --incremental                # 增量（日常/月末刷新）
    python download_cb.py --incremental --skip-redeem  # 只刷行情
    python download_cb.py --incremental --start 20260901 --end 20260930
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

START = "20150101"                 # 只保留 2015 年后上市的转债
CAL_PATH = CACHE / "trade_cal.parquet"
DAILY_PATH = CACHE / "cb_daily.parquet"
BASIC_PATH = CACHE / "cb_basic.parquet"
REDEEM_PATH = CACHE / "cb_redeem_jsl.parquet"


def log(message: str) -> None:
    print(message, flush=True)


def get_pro():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        sys.exit("ERROR: TUSHARE_TOKEN 未设置")
    import tushare as ts
    return ts.pro_api(token)


def trading_days(start: str, end: str) -> list[str]:
    """从覆盖目标区间的交易日历取开放日；日历缺失或过期时失败。"""
    if not CAL_PATH.exists():
        raise RuntimeError("缺少交易日历，不能用工作日猜测交易日")
    cal = pd.read_parquet(CAL_PATH)
    dates = cal["cal_date"].astype(str)
    if dates.empty or dates.max() < end:
        raise RuntimeError("交易日历未覆盖 {}，请先刷新 trade_cal".format(end))
    open_dates = cal.loc[cal["is_open"].astype(int) == 1, "cal_date"].astype(str)
    return sorted(d for d in open_dates if start <= d <= end)

def fetch_by_day(pro, days: list) -> tuple:
    """按 trade_date 逐日拉全市场，返回 (frames, failed)。"""
    frames, failed = [], []
    t0 = time.time()
    for i, day in enumerate(days, 1):
        frame, err = None, None
        for attempt in range(3):
            try:
                frame = pro.cb_daily(trade_date=day)
                err = None
                break
            except Exception as exc:
                err = exc
                time.sleep(1.0 * (attempt + 1))
        if err is not None:
            failed.append((day, str(err)[:160]))
            continue
        if frame is not None and not frame.empty:
            frames.append(frame)
        if i % 20 == 0:
            got = sum(len(f) for f in frames)
            log("  进度 {}/{}，累计 {:,} 行，用时 {:.0f}s".format(i, len(days), got, time.time() - t0))
        time.sleep(0.35)
    return frames, failed


def load_or_fetch_basic(pro, prefer_cache: bool = False, require_refresh: bool = False) -> pd.DataFrame:
    """刷新 cb_basic；历史全量可回退缓存，正式增量要求刷新成功。"""
    if not prefer_cache:
        try:
            frame = pro.cb_basic()
            if frame is not None and not frame.empty:
                old_n = len(pd.read_parquet(BASIC_PATH)) if BASIC_PATH.exists() else 0
                frame.to_parquet(BASIC_PATH, index=False)
                log("[cb_basic] 刷新 {} 行 -> {} 行（新增 {}）".format(old_n, len(frame), len(frame) - old_n))
                return frame
            log("[cb_basic] 接口返回空，回退缓存")
        except Exception as exc:
            log("[cb_basic] 刷新失败，回退缓存：{}".format(str(exc)[:160]))
    if require_refresh:
        raise RuntimeError("cb_basic 刷新失败，不能发布当期信号")
    if BASIC_PATH.exists():
        frame = pd.read_parquet(BASIC_PATH)
        log("[cb_basic] 复用缓存 {} 行".format(len(frame)))
        return frame
    sys.exit("ERROR: 无 cb_basic 数据（接口失败且无缓存）")


def refresh_redeem() -> bool:
    """刷新集思录强赎快照（AkShare），返回是否成功。"""
    old_announced = -1
    if REDEEM_PATH.exists():
        try:
            old = pd.read_parquet(REDEEM_PATH)
            old_announced = int(old["强赎状态"].fillna("").str.contains("已公告强赎", na=False).sum())
        except Exception:
            old_announced = -1
    try:
        import akshare as ak
        frame = ak.bond_cb_redeem_jsl()
    except Exception as exc:
        log("[cb_redeem_jsl] 刷新失败（沿用旧快照）：{}".format(str(exc)[:160]))
        return False
    if frame is None or frame.empty:
        log("[cb_redeem_jsl] 接口返回空，沿用旧快照")
        return False
    frame.to_parquet(REDEEM_PATH, index=False)
    status = frame["强赎状态"].fillna("")
    log("[cb_redeem_jsl] 刷新 {} 行 / {} 列（已公告强赎 {} 只，上一快照 {} 只）".format(
        len(frame), len(frame.columns), int(status.str.contains("已公告强赎", na=False).sum()), old_announced))
    return True


def check_convert(latest: str) -> None:
    """检查聚宽 PIT 转股价数据是否滞后。"""
    parts = sorted(CACHE.glob("cb_convert_*.parquet"))
    if not parts:
        log("[cb_convert] 未找到 cb_convert_*.parquet，溢价率将无法计算")
        return
    newest, newest_file = "", ""
    for path in parts:
        try:
            dates = pd.read_parquet(path, columns=["date"])["date"].astype(str)
        except Exception:
            continue
        # 聚宽回传的是 YYYY-MM-DD，行情是 YYYYMMDD，比较前统一归一化
        norm = dates.str.replace("-", "", regex=False).str[:8]
        if len(norm) and str(norm.max()) > newest:
            newest, newest_file = str(norm.max()), path.name
    latest_norm = str(latest).replace("-", "")[:8]
    log("[cb_convert] 最新 PIT 日期 {}（{}）；cb_daily 最新 {}".format(newest, newest_file, latest_norm))
    if newest and newest < latest_norm:
        log("  [warn] 聚宽 PIT 转股价数据滞后于行情：{} < {}".format(newest, latest_norm))
        log("  [warn] 修复：在聚宽研究环境抓取 CONBOND_DAILY_CONVERT 回传后运行")
        log("          python download_cb_convert.py --decode {}".format(latest_norm[:4]))
    else:
        log("  [ok] 聚宽 PIT 转股价数据与行情同步，无需人工刷新")


def run_incremental(args) -> int:
    pro = get_pro()
    if not DAILY_PATH.exists():
        log("[incremental] 缓存不存在，自动退化为全量模式")
        return run_full(args)

    old = pd.read_parquet(DAILY_PATH)
    old_max = str(old["trade_date"].astype(str).max())
    end = args.end or datetime.today().strftime("%Y%m%d")
    if args.start:
        start = args.start
    else:
        start = (datetime.strptime(old_max, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    log("[incremental] 缓存 {:,} 行 / {} 只 / {} ~ {}".format(
        len(old), old["ts_code"].nunique(), old["trade_date"].astype(str).min(), old_max))
    log("[incremental] 目标区间 {} ~ {}".format(start, end))

    latest = old_max
    failed, missing = [], []
    # 重新检查已有区间的缺日，避免某一天 API 失败后被较晚的 max(trade_date) 掩盖。
    holes = []
    if CAL_PATH.exists():
        calendar = pd.read_parquet(CAL_PATH)
        open_days = calendar[calendar["is_open"].astype(int) == 1]["cal_date"].astype(str)
        present = set(old["trade_date"].astype(str))
        holes = [day for day in open_days if str(old["trade_date"].astype(str).min()) <= day <= old_max and day not in present]
    if start > end and not holes:
        log("[incremental] 已是最新（缓存 max={} >= end={}），无需拉取行情".format(old_max, end))
    else:
        days = sorted(set(holes) | set(trading_days(start, end) if start <= end else []))
        if holes:
            log("[incremental] 补拉历史缺日 {} 个".format(len(holes)))
        if len(days) > 60:
            log("[warn] 待拉取 {} 个交易日，耗时较长；若非必要建议改跑全量模式".format(len(days)))
        log("[incremental] 待拉取 {} 个交易日".format(len(days)))
        if days:
            frames, failed = fetch_by_day(pro, days)
            if frames:
                new = pd.concat(frames, ignore_index=True)
                merged = pd.concat([old, new], ignore_index=True)
                merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
                merged = merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
                merged.to_parquet(DAILY_PATH, index=False)
                latest = str(merged["trade_date"].astype(str).max())
                log("[cb_daily] 新增 {:,} 行（{:,} -> {:,}）/ 覆盖 {} ~ {}".format(
                    len(merged) - len(old), len(old), len(merged),
                    merged["trade_date"].astype(str).min(), latest))
            else:
                log("[cb_daily] 未取到任何新数据（接口当日行情可能尚未更新）")
            if failed:
                log("[cb_daily] 失败 {} 个交易日：{}".format(
                    len(failed), ", ".join(d for d, _ in failed[:20])))
            present_now = set(pd.read_parquet(DAILY_PATH, columns=["trade_date"])["trade_date"].astype(str))
            missing = [day for day in days if day not in present_now]
            if missing:
                log("[cb_daily] 缺失 {} 个交易日：{}".format(len(missing), ", ".join(missing[:20])))

    try:
        load_or_fetch_basic(pro, require_refresh=True)
    except RuntimeError as exc:
        log("[cb_basic] {}".format(exc))
        return 2
    redeem_ok = args.skip_redeem or refresh_redeem()
    check_convert(latest)
    log("[incremental] 完成：cb_daily max = {}".format(latest))
    if start <= end or holes:
        return 2 if failed or missing or not redeem_ok else 0
    return 0 if redeem_ok else 2


def run_full(args) -> int:
    pro = get_pro()
    log("[full] 全量模式：逐只拉 cb_daily 全历史（约 1,100 只，耗时数分钟）")
    basic = load_or_fetch_basic(pro)
    basic["list_date"] = basic["list_date"].astype(str)
    keep = basic[basic["list_date"] >= START].copy()
    codes = sorted(keep["ts_code"].dropna().unique())
    log("[cb_basic] 上市日 >= {} 的转债 {} 只（全部 {} 只）".format(START, len(codes), len(basic)))

    end = args.end or datetime.today().strftime("%Y%m%d")
    frames, failed = [], []
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        ok, last = False, ""
        frame = None
        for attempt in range(3):
            try:
                frame = pro.cb_daily(ts_code=code, start_date=START, end_date=end)
                ok = True
                break
            except Exception as exc:
                last = str(exc)[:120]
                time.sleep(1.5 * (attempt + 1))
        if not ok:
            failed.append((code, last))
            continue
        if frame is not None and not frame.empty:
            frames.append(frame)
        if frame is not None and len(frame) >= 2000:
            log("[warn] {} 返回 {} 行，可能被 2000 行上限截断".format(code, len(frame)))
        if i % 100 == 0:
            log("  进度 {}/{}，累计 {:,} 行，用时 {:.0f}s".format(
                i, len(codes), sum(len(f) for f in frames), time.time() - t0))
        time.sleep(0.12)

    if not frames:
        log("没有任何数据，退出")
        return 1
    daily = pd.concat(frames, ignore_index=True)
    daily = daily.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    daily = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    daily.to_parquet(DAILY_PATH, index=False)
    log("[cb_daily] 写出 {:,} 行 / {} 只 / {} ~ {} -> {}".format(
        len(daily), daily["ts_code"].nunique(),
        daily["trade_date"].astype(str).min(), daily["trade_date"].astype(str).max(), DAILY_PATH.name))
    if failed:
        log("[cb_daily] 失败 {} 只：{}".format(len(failed), ", ".join(c for c, _ in failed[:20])))
    log("完成，用时 {:.0f}s".format(time.time() - t0))
    return 2 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="下载/刷新可转债数据")
    ap.add_argument("--incremental", action="store_true",
                    help="只拉取缓存之后的新交易日（按 trade_date 拉全市场），并刷新 cb_basic/强赎快照")
    ap.add_argument("--start", default=None, help="起始日 YYYYMMDD（增量默认 = 缓存 max + 1 天）")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD（默认今天）")
    ap.add_argument("--skip-redeem", action="store_true", help="增量模式跳过 AkShare 强赎快照刷新")
    args = ap.parse_args()
    if args.incremental:
        return run_incremental(args)
    return run_full(args)


if __name__ == "__main__":
    sys.exit(main())
