# -*- coding: utf-8 -*-
"""
门禁自检 (阴性对照 / Negative Control)
=====================================
"292 个因子全部 PASS" 只有在**门禁本身有效**时才有意义。
本脚本向同一套截断不变性检验注入 5 个已知性质的假因子:

  LEAK_* : 故意使用未来信息 / 全样本统计量 -> 门禁**必须**判为 LEAK
  CLEAN_*: 严格因果                          -> 门禁**必须**判为 PASS

若门禁抓不到 LEAK_*, 则说明它无效, 那么对真因子的 PASS 结论也不可信。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("ops", "factors", "gates", "data"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from pit_gate import build_test_data, _max_abs_diff, F32  # noqa: E402


def make_fake(D):
    """返回 {name: DataFrame} 的假因子集, 已知是否含未来函数"""
    c = D["close"]
    out = {}

    # ---- 应当被抓出的泄漏 ----
    # 1. 直接用 5 日后的价格
    out["LEAK_shift_neg5"] = c.shift(-5)
    # 2. 全样本标准化 (用到了整段数据的均值/标准差)
    mu = float(c.stack().mean())
    sd = float(c.stack().std())
    out["LEAK_fullsample_zscore"] = (c - mu) / sd
    # 3. 反向填充 (把未来值填到过去) —— 必须作用在**含 NaN 的列**上,
    #    否则 bfill 退化为空操作, 不是一个有效的阴性对照
    out["LEAK_bfill_volume"] = D["volume"].bfill()
    # 4. 中心化滚动窗口 (窗口跨到未来)
    out["LEAK_center_rolling"] = c.rolling(21, center=True, min_periods=5).mean()
    # 5. 未来收益作为因子 (最典型的作弊)
    out["LEAK_future_return"] = c.pct_change().shift(-1)
    # 6. 全样本秩 (用到整段数据的排序)
    out["LEAK_fullsample_rank"] = c.rank(axis=1).rank(pct=True)

    # ---- 应当通过的因果算子 ----
    out["CLEAN_shift_pos1"] = c.shift(1)
    out["CLEAN_rolling_mean20"] = c.rolling(20, min_periods=10).mean()
    out["CLEAN_ewm"] = c.ewm(span=20, adjust=False, min_periods=5).mean()
    # 前向填充是因果的 (只用过去), 必须 PASS
    out["CLEAN_ffill_volume"] = D["volume"].ffill()

    return out


def main():
    print("=" * 66)
    print("门禁自检: 注入已知性质的假因子, 检验门禁能否正确判别")
    print("=" * 66)

    D = build_test_data()
    N = len(D["close"])
    K = int(N * 0.62)
    print(f"样本 {N} 行, 截断点 K = {K}\n")

    F = make_fake(D)
    D_trunc = {k: (v.iloc[:K] if hasattr(v, "iloc") else v) for k, v in D.items()}
    F_trunc = make_fake(D_trunc)

    rows = []
    for name in F:
        a = F[name].iloc[:K]
        b = F_trunc[name]
        md, nanmm = _max_abs_diff(a, b)
        flagged = (md > 1e-5) or (nanmm > 0)
        expect_leak = name.startswith("LEAK_")
        correct = (flagged == expect_leak)
        rows.append({
            "factor": name,
            "max_diff": md,
            "nan_mismatch": nanmm,
            "gate_says": "LEAK" if flagged else "PASS",
            "truth": "LEAK" if expect_leak else "CLEAN",
            "correct": correct,
        })

    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    show = df.copy()
    show["max_diff"] = show["max_diff"].map(lambda v: f"{v:.3e}")
    print(show.to_string(index=False))

    ok = int(df["correct"].sum())
    n_leak = int((df["truth"] == "LEAK").sum())
    n_clean = len(df) - n_leak
    print()
    print("=" * 66)
    print(f"自检结果: {ok}/{len(df)} 判别正确")
    if ok == len(df):
        print(f"=> 门禁有效: 能抓出全部 {n_leak} 类泄漏, 且不误杀 {n_clean} 个因果算子。")
        print("   因此前面 292 个真实因子的 PASS 结论成立。")
    else:
        bad = df.loc[~df["correct"], "factor"].tolist()
        print(f"=> 门禁存在漏判/误判 ({bad}), 前面对真实因子的 PASS 结论不可信!")
    print("=" * 66)

    df.to_csv(os.path.join(BASE, "pit_selftest_result.csv"),
              index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
