# -*- coding: utf-8 -*-
r"""因子处理基础：去极值（winsorize）与标准化（standardize）。

约定：
    所有函数都保持 NaN 原样（不填充、不参与统计量计算）
    输入为 pandas Series，输出同长度 Series
    index 原样保留

自测：
    python factor_processing.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAD_SCALE = 1.4826  # 若需要把 MAD 折算成标准差量级，可传 scale=MAD_SCALE


def winsorize(series: pd.Series, method: str = "mad", n: float = 5.0,
              lower: float = 0.01, upper: float = 0.99, scale: float = 1.0) -> pd.Series:
    """去极值。

    method='mad'     ：中位数 ± n × MAD（MAD 为中位数绝对偏差）之外的值截断到边界
    method='percentile'：截断到 [lower, upper] 分位
    method='std'     ：中位数 ± n × 标准差之外的值截断到边界

    scale 仅用于 mad：MAD × scale 作为离散度（scale=1.4826 时近似正态标准差）。
    NaN 不参与统计量计算，也不被填充。
    """
    out = series.astype(float).copy()
    valid = out.dropna()
    if valid.empty:
        return out

    if method == "percentile":
        lo = float(valid.quantile(lower))
        hi = float(valid.quantile(upper))
    elif method == "mad":
        med = float(valid.median())
        mad = float((valid - med).abs().median()) * float(scale)
        if mad == 0:
            return out
        lo, hi = med - n * mad, med + n * mad
    elif method == "std":
        med = float(valid.median())
        std = float(valid.std(ddof=1))
        if std == 0 or np.isnan(std):
            return out
        lo, hi = med - n * std, med + n * std
    else:
        raise ValueError("unknown method: {}".format(method))

    if hi < lo:
        lo, hi = hi, lo
    return out.clip(lower=lo, upper=hi)


def standardize(series: pd.Series, method: str = "rank") -> pd.Series:
    """标准化。

    method='rank'   ：秩分数，线性映射到 [0, 1]（最小值 0，最大值 1；单值时返回 0.5）
    method='zscore' ：(x - mean) / std（std=0 时全部返回 0）

    NaN 不参与统计量计算，也不被填充。
    """
    out = series.astype(float).copy()
    valid = out.dropna()
    if valid.empty:
        return out

    if method == "rank":
        ranks = valid.rank(method="average")
        if len(valid) == 1:
            out.loc[valid.index] = 0.5
        else:
            out.loc[valid.index] = (ranks - 1.0) / (len(valid) - 1.0)
        return out
    if method == "zscore":
        mean = float(valid.mean())
        std = float(valid.std(ddof=0))
        if std == 0:
            out.loc[valid.index] = 0.0
        else:
            out.loc[valid.index] = (valid - mean) / std
        return out
    raise ValueError("unknown method: {}".format(method))


def process(series: pd.Series, winsor_method: str = "mad", n: float = 5.0,
            standard_method: str = "rank") -> pd.Series:
    """常用组合：先去极值，再标准化。"""
    return standardize(winsorize(series, method=winsor_method, n=n), method=standard_method)


def _self_test() -> int:
    checks = 0
    failures = []

    def check(name, expected, actual, tol=1e-9):
        nonlocal checks
        checks += 1
        ok = np.allclose(expected, actual, equal_nan=True, atol=tol)
        if not ok:
            failures.append("{}: 预期 {} 实际 {}".format(name, expected, actual))

    s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
    check("mad 截断上界（median=3, MAD=1, 上界=8）", [1.0, 2.0, 3.0, 4.0, 8.0], winsorize(s, method="mad", n=5).tolist())
    check("percentile 截断（80% 分位=23.2）", [1.0, 2.0, 3.0, 4.0, 23.2], winsorize(s, method="percentile", lower=0.0, upper=0.8).tolist())

    check("rank [0,1]", [0.0, 0.25, 0.5, 0.75, 1.0], standardize(s, method="rank").tolist())
    z = standardize(s, method="zscore")
    check("zscore 均值", 0.0, float(np.nanmean(z)), tol=1e-9)
    check("zscore 单位方差", 1.0, float(np.nanstd(z, ddof=0)), tol=1e-9)

    same = pd.Series([3.0, 3.0, 3.0])
    check("全相同值 mad 不变", [3.0, 3.0, 3.0], winsorize(same, method="mad", n=5).tolist())
    check("全相同值 rank（平均秩，全 0.5）", [0.5, 0.5, 0.5], standardize(same, method="rank").tolist())
    check("全相同值 zscore", [0.0, 0.0, 0.0], standardize(same, method="zscore").tolist())

    one = pd.Series([7.5])
    check("单值 mad", [7.5], winsorize(one, method="mad", n=5).tolist())
    check("单值 rank", [0.5], standardize(one, method="rank").tolist())
    check("单值 zscore", [0.0], standardize(one, method="zscore").tolist())

    nan_s = pd.Series([1.0, np.nan, 3.0, 4.0, 100.0])
    w = winsorize(nan_s, method="mad", n=5)
    check("含 NaN 保持 NaN", True, bool(np.isnan(w.iloc[1])))
    check("含 NaN 不参与统计（median=3.5, MAD=1.5, 上界=11）", [1.0, np.nan, 3.0, 4.0, 11.0], w.tolist())
    r = standardize(nan_s, method="rank")
    check("含 NaN rank 保持 NaN", True, bool(np.isnan(r.iloc[1])))
    check("含 NaN rank 值域（NaN 保持 NaN）", [0.0, np.nan, 0.3333333333, 0.6666666667, 1.0], r.tolist(), tol=1e-6)

    check("process 组合值域", [0.0, 0.5, 1.0], process(pd.Series([1.0, 5.0, 9.0])).tolist())

    print("单元测试: {} 项，失败 {}".format(checks, len(failures)))
    for item in failures:
        print("  FAIL", item)
    print("ALL PASS" if not failures else "NOT ALL PASS")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())