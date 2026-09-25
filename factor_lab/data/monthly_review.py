# -*- coding: utf-8 -*-
r"""CB 双低 · 月度复盘

读最新与上一期信号 CSV，输出差异报告（新买入 / 卖出 / 继续持有、换手率），
并把复盘结论追加到 TRACKING.md。

输入：
    <signal-dir>/*.csv            由 monthly_signal.py 生成的信号（<日期>.csv + <日期>_sell.csv）
    <signal-dir>/TRACKING.md      实盘模拟台账
输出：
    - 控制台差异报告
    - TRACKING.md 追加"## 复盘记录"条目（同一信号日只追加一次）

用法：
    python monthly_review.py [--signal-dir .../output/monthly_signals] [--tracking-file .../TRACKING.md]
"""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
LAB_DIR = DATA_DIR.parent
DEFAULT_DIR = LAB_DIR / "output" / "monthly_signals"


def log(msg: str) -> None:
    print(msg, flush=True)


def list_signals(signal_dir: Path) -> list[Path]:
    """返回按日期升序的目标清单 CSV（排除 _sell 与台账）。"""
    out = []
    for p in sorted(signal_dir.glob("*.csv")):
        if p.stem.endswith("_sell"):
            continue
        out.append(p)
    return out


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="CB 双低月度复盘")
    ap.add_argument("--signal-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--tracking-file", default=str(DEFAULT_DIR / "TRACKING.md"))
    ap.add_argument("--date", default=None, help="指定要复盘的信号日（默认取最新一期）")
    args = ap.parse_args()
    signal_dir, tracking = Path(args.signal_dir), Path(args.tracking_file)

    all_files = list_signals(signal_dir)
    files = all_files
    if not files:
        log("未找到信号文件（先跑 monthly_signal.py）")
        return 1
    if args.date:
        files = [f for f in files if f.stem == args.date]
        if not files:
            log("未找到信号日 {} 的清单".format(args.date))
            return 1

    cur_path = files[-1]
    prev_candidates = [f for f in all_files if f.stem < cur_path.stem]
    prev_path = prev_candidates[-1] if prev_candidates else None
    cur = load(cur_path)
    cur_date = cur_path.stem
    weight = float(cur["target_weight"].iloc[0]) if "target_weight" in cur.columns else float("nan")
    amount = float(cur["target_amount"].iloc[0]) if "target_amount" in cur.columns else float("nan")

    log("=== 月度复盘 ===")
    log("最新信号: {}（{} 只，单只权重 {:.2%}，金额 {:.0f} 元）".format(cur_date, len(cur), weight, amount))

    seller_path = signal_dir / "{}_sell.csv".format(cur_date)

    if prev_path is None:
        log("\n上期信号不存在 → **首次运行**（无换手、无卖出）")
        log("本期目标清单: {} 只；卖出清单文件: {}".format(len(cur), seller_path.name))
        summary = "首次运行：目标 {} 只，无上期可比，换手率 —".format(len(cur))
        new_codes, sold_codes, hold_codes = [], [], []
    else:
        prev = load(prev_path)
        prev_date = prev_path.stem
        prev_codes, cur_codes = set(prev["code"]), set(cur["code"])
        new_codes = sorted(cur_codes - prev_codes)
        sold_codes = sorted(prev_codes - cur_codes)
        hold_codes = sorted(cur_codes & prev_codes)
        turnover = (len(new_codes) + len(sold_codes)) / max(len(cur_codes) + len(prev_codes), 1)
        log("上期信号: {}（{} 只）".format(prev_date, len(prev)))
        log("\n其余指标: 新买入 {} 只 / 卖出 {} 只 / 继续持有 {} 只".format(
            len(new_codes), len(sold_codes), len(hold_codes)))
        log("按只数估计的单边换手率（买卖只数 / 两期只数之和）= {:.2%}".format(turnover))
        summary = "新买入 {} 只、卖出 {} 只、继续持有 {} 只；按只数估计单边换手 {:.2%}".format(
            len(new_codes), len(sold_codes), len(hold_codes), turnover)
        if new_codes:
            log("\n新买入清单:")
            log(cur[cur["code"].isin(new_codes)][["code", "name", "double_low_value", "price", "premium_rate"]]
                .sort_values("double_low_value").to_string(index=False))
        if sold_codes:
            log("\n卖出清单:")
            log(prev[prev["code"].isin(sold_codes)][["code", "name", "double_low_value", "price", "premium_rate"]]
                .sort_values("double_low_value").to_string(index=False))

    # 追加到台账（同一信号日只追加一次）
    if tracking.exists():
        text = tracking.read_text(encoding="utf-8")
        marker = "### 复盘 {}".format(cur_date)
        if marker in text:
            log("\n台账已含 {} 的复盘记录，跳过追加".format(cur_date))
        else:
            entry = ["", marker, "",
                     "- 复盘时间：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M")),
                     "- 信号日：{}".format(cur_date),
                     "- 目标持仓：{} 只（单只权重 {:.2%}、金额 {:.0f} 元）".format(len(cur), weight, amount),
                     "- {}".format(summary),
                     "- 信号文件：{}".format(cur_path.name)]
            tracking.write_text(text.rstrip() + "\n" + "\n".join(entry) + "\n", encoding="utf-8")
            log("\n已追加复盘记录到 {}（{}）".format(tracking.name, marker))
    else:
        log("\n台账不存在：{}（先创建 TRACKING.md）".format(tracking))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
