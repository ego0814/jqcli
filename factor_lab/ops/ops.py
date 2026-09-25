# -*- coding: utf-8 -*-
"""
Alpha101 / GTJA191 因子算子库
=============================
所有算子作用于宽表 DataFrame: index=trade_date, columns=ts_code, values=float

参考:
- Kakushadze, Z. (2016). 101 Formulaic Alphas. arXiv:1601.00991
- 国泰君安《基于短周期量价特征的多因子选股体系》(2014)
"""
import numpy as np
import pandas as pd


# ============ 基础截面算子 ============
def rank(df):
    """截面排名, 归一化到 [0,1]"""
    return df.rank(axis=1, pct=True)


def scale(df, a=1.0):
    """使 sum(abs(x)) = a"""
    s = df.abs().sum(axis=1).replace(0, np.nan)
    return df.div(s, axis=0) * a


def _wrap(v, ref):
    """把任意结果包装/对齐成与 ref 同形状的 DataFrame"""
    if isinstance(v, pd.DataFrame):
        return v.reindex(index=ref.index, columns=ref.columns)
    return pd.DataFrame(np.asarray(v, dtype=float), index=ref.index, columns=ref.columns)


def sign(df):
    return np.sign(df)


def log(df):
    return np.log(df.where(df > 0))


def abs_(df):
    return df.abs()


def power(df, p):
    return np.sign(df) * (df.abs() ** p)


def signed_power(df, p):
    d = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    return np.sign(d) * (d.abs() ** p)


# ============ 时序算子 ============
def _w(d):
    """窗口长度归一化为 >=1 的整数 (Alpha101 原式含 16.1219 这类小数窗口)"""
    return max(1, int(round(float(d))))


def _mp(d):
    """滚动窗口的 min_periods, 保证 <= 窗口长度"""
    w = _w(d)
    return max(1, min(w, max(2, w // 2)))


def delay(df, d):
    return df.shift(int(d))


def delta(df, d):
    return df - df.shift(int(d))


def ts_sum(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).sum()


def ts_mean(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).mean()


def ts_std(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).std()


def ts_min(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).min()


def ts_max(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).max()


# ============ 向量化的滚动规约 (替代 Python 逐窗口 apply) ============
# 原实现用 df.rolling().apply(raw=True) 逐窗口调用 Python 回调, 在
# 2848×5902 的真实数据上单个算子要 400~550 秒。改用 numpy 的
# sliding_window_view (零拷贝视图) + 分块规约, 实测快 100 倍以上。
#
# NaN 语义与原 np.nanargmax / np.nanprod 保持一致:
#   - argmax/argmin: 窗口内忽略 NaN; 全 NaN -> NaN
#   - product:       窗口内 NaN 视为 1.0; 全 NaN -> NaN
# min_periods 语义也保持一致 (窗口内有效值数 >= _mp(d) 才出值)。
_TS_BUDGET = 4.8e7      # 每个分块临时数组的字节上限 (~48MB)


def _fill_nan(block, fill):
    """把 block 中的 NaN 替换为 fill, 返回 float64 副本"""
    nanm = np.isnan(block)
    filled = block.astype("float64", copy=True)
    np.copyto(filled, fill, where=nanm)
    return filled, nanm


def _reduce_arg(a, d, mode):
    """沿时间轴的滚动 argmax/argmin (返回窗口内 0-based 位置)"""
    T, N = a.shape
    mp = _mp(d)
    out = np.full((T, N), np.nan, dtype="float64")
    fill = -np.inf if mode == "max" else np.inf
    fn = np.argmax if mode == "max" else np.argmin

    # --- 部分窗口 (前 d-1 行) ---
    # 第 i 行的窗口 = a[0:i+1] (长度 i+1 <= d-1), 逐行处理量很小
    for i in range(mp - 1, min(d - 1, T)):
        blk = a[:i + 1]
        if blk.shape[0] < mp:
            continue
        filled, nanm = _fill_nan(blk, fill)
        idx = fn(filled, axis=0).astype("float64")
        idx[(~nanm).sum(axis=0) < mp] = np.nan     # min_periods: 有效值不足
        out[i] = idx

    # --- 完整窗口 (分列块, 控制内存) ---
    if T >= d:
        rows = T - d + 1
        blk_n = max(1, int(_TS_BUDGET // max(1, rows * d * 8)))
        for j0 in range(0, N, blk_n):
            j1 = min(j0 + blk_n, N)
            sw = np.lib.stride_tricks.sliding_window_view(a[:, j0:j1], d, axis=0)
            filled, nanm = _fill_nan(sw, fill)
            idx = fn(filled, axis=2).astype("float64")
            idx[(~nanm).sum(axis=2) < mp] = np.nan     # min_periods: 有效值不足
            out[d - 1:, j0:j1] = idx
    return out


def _reduce_prod(a, d):
    """沿时间轴的滚动连乘 (NaN 记为 1.0)"""
    T, N = a.shape
    mp = _mp(d)
    out = np.full((T, N), np.nan, dtype="float64")
    for i in range(mp - 1, min(d - 1, T)):
        blk = a[:i + 1]
        if blk.shape[0] < mp:
            continue
        filled, nanm = _fill_nan(blk, 1.0)
        p = filled.prod(axis=0)
        p[(~nanm).sum(axis=0) < mp] = np.nan
        out[i] = p
    if T >= d:
        rows = T - d + 1
        blk_n = max(1, int(_TS_BUDGET // max(1, rows * d * 8)))
        for j0 in range(0, N, blk_n):
            j1 = min(j0 + blk_n, N)
            sw = np.lib.stride_tricks.sliding_window_view(a[:, j0:j1], d, axis=0)
            filled, nanm = _fill_nan(sw, 1.0)
            p = filled.prod(axis=2)
            p[(~nanm).sum(axis=2) < mp] = np.nan
            out[d - 1:, j0:j1] = p
    return out


def _as_arr(df):
    return df.to_numpy(dtype="float64", copy=False)


def ts_argmax(df, d):
    """过去 d 日内最大值出现在第几天(0=最早)"""
    return pd.DataFrame(_reduce_arg(_as_arr(df), _w(d), "max"),
                        index=df.index, columns=df.columns)


def ts_argmin(df, d):
    return pd.DataFrame(_reduce_arg(_as_arr(df), _w(d), "min"),
                        index=df.index, columns=df.columns)


def ts_argmax_ref(df, d):
    """原 Python 逐窗口实现 (仅用于对拍验证)"""
    return df.rolling(_w(d), min_periods=_mp(d)).apply(
        lambda x: np.nan if np.isnan(x).all() else float(np.nanargmax(x)), raw=True)


def ts_argmin_ref(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).apply(
        lambda x: np.nan if np.isnan(x).all() else float(np.nanargmin(x)), raw=True)


def ts_product_ref(df, d):
    return df.rolling(_w(d), min_periods=_mp(d)).apply(
        lambda x: np.nan if np.isnan(x).all() else np.nanprod(x), raw=True)


def ts_rank(df, d):
    """当前值在过去 d 日内的排名(归一化 0~1)

    优先用 pandas 原生 rolling.rank (C 实现, 快百倍), 失败再回退 apply。
    """
    w, mp = _w(d), _mp(d)
    try:
        return df.rolling(w, min_periods=mp).rank(pct=True)
    except (AttributeError, TypeError):
        return df.rolling(w, min_periods=mp).apply(
            lambda x: np.nan if np.isnan(x).all() else
            (pd.Series(x).rank(pct=True).iloc[-1]), raw=True)


def ts_product(df, d):
    return pd.DataFrame(_reduce_prod(_as_arr(df), _w(d)),
                        index=df.index, columns=df.columns)


def stddev(df, d):
    return ts_std(df, d)


def correlation(x, y, d):
    """过去 d 日 x 与 y 的时序相关系数"""
    return x.rolling(_w(d), min_periods=_mp(d)).corr(y)


def covariance(x, y, d):
    return x.rolling(_w(d), min_periods=_mp(d)).cov(y)


def decay_linear(df, d):
    """线性衰减加权平均, 权重 d, d-1, ..., 1 (最新值权重最大)

    向量化实现: d 次向右移位加权和, 避免逐窗口 Python 调用 (大宽表下快百倍)。
    """
    d = _w(d)
    if d <= 1:
        return df.astype(float)
    denom = d * (d + 1) / 2.0
    out = None
    for k in range(d):
        t = df.shift(k) * ((d - k) / denom)   # shift(0)=最新 -> 权重 d
        out = t if out is None else out + t
    return out


def highday(df, d):
    return d - 1 - ts_argmax(df, d)


def lowday(df, d):
    return d - 1 - ts_argmin(df, d)


# ============ 条件/逻辑算子 ============
def element_wise_and(a, b):
    """a,b 均为 0/1 布尔矩阵"""
    return (a.astype(float) * b.astype(float) > 0).astype(float)


def less_than(a, b):
    return (a < b).astype(float)


def greater_than(a, b):
    return (a > b).astype(float)
