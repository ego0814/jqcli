# -*- coding: utf-8 -*-
r"""因子中性化：对市值、行业、或两者同时做横截面回归取残差。

输入：
    factor_df   : DataFrame，含 (symbol, rebalance_date, value)
    mv_df       : DataFrame，含 (symbol, rebalance_date, total_mv)
    industry_df : DataFrame，含 (symbol, industry)（当前时点快照，非 PIT）

输出：
    DataFrame，含 (symbol, rebalance_date, value=残差, raw_value, n_obs)

实现用 numpy.linalg.lstsq，不引入 statsmodels 依赖。
每个截面（rebalance_date）单独回归；样本数不足或矩阵退化时该截面原样返回（value 保持原值）并记录 fallback。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_OBS = 10


def _ols_residual(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def _prepare(factor_df: pd.DataFrame, mv_df: pd.DataFrame | None = None,
             industry_df: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = factor_df.copy()
    frame = frame.rename(columns={c: c for c in frame.columns})
    if "value" not in frame.columns:
        raise ValueError("factor_df 必须包含 value 列")
    if mv_df is not None:
        mv = mv_df.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"})
        _before = len(mv)
        mv = mv.drop_duplicates(subset=["symbol", "rebalance_date"], keep="last")
        if len(mv) != _before:
            print("[neutralize] mv 去重: {:,} -> {:,}".format(_before, len(mv)), flush=True)
        frame = frame.merge(mv[["symbol", "rebalance_date", "total_mv"]],
                            on=["symbol", "rebalance_date"], how="left")
        frame = frame[frame["total_mv"].notna() & (frame["total_mv"] > 0)].copy()
        frame["log_mv"] = np.log(frame["total_mv"].astype(float))
    if industry_df is not None:
        ind = industry_df.rename(columns={"ts_code": "symbol"})[["symbol", "industry"]]
        _b2 = len(ind)
        ind = ind.drop_duplicates(subset=["symbol"], keep="last")
        if len(ind) != _b2:
            print("[neutralize] industry 去重: {:,} -> {:,}".format(_b2, len(ind)), flush=True)
        frame = frame.merge(ind, on="symbol", how="left")
        frame["industry"] = frame["industry"].fillna("UNKNOWN")
    return frame


def neutralize_market_cap(factor_df: pd.DataFrame, mv_df: pd.DataFrame) -> pd.DataFrame:
    """对 log(总市值) 做横截面回归，返回残差。"""
    frame = _prepare(factor_df, mv_df=mv_df)
    return _run(frame, ["log_mv"])


def neutralize_industry(factor_df: pd.DataFrame, industry_df: pd.DataFrame) -> pd.DataFrame:
    """对行业哑变量做横截面回归，返回残差。"""
    frame = _prepare(factor_df, industry_df=industry_df)
    return _run(frame, ["industry"])


def neutralize_both(factor_df: pd.DataFrame, mv_df: pd.DataFrame, industry_df: pd.DataFrame) -> pd.DataFrame:
    """同时对 log(总市值) 与行业哑变量回归，返回残差。"""
    frame = _prepare(factor_df, mv_df=mv_df, industry_df=industry_df)
    out = _run(frame, ["log_mv", "industry"])
    _b3 = len(out)
    out = out.drop_duplicates(subset=["symbol", "rebalance_date"], keep="last")
    if len(out) != _b3:
        print("[neutralize] 输出去重: {:,} -> {:,}".format(_b3, len(out)), flush=True)
    return out


def _run(frame: pd.DataFrame, regressors: list[str]) -> pd.DataFrame:
    out = []
    for date, group in frame.groupby("rebalance_date", sort=True):
        g = group[group["value"].notna()].copy()
        y = g["value"].to_numpy(dtype=float)
        g["raw_value"] = y
        if len(g) < MIN_OBS:
            g["fallback"] = "TOO_FEW_OBS"
            out.append(g)
            continue
        blocks = []
        for name in regressors:
            if name == "industry":
                dummies = pd.get_dummies(g["industry"], drop_first=True, dtype=float)
                blocks.append(dummies.to_numpy(dtype=float))
            else:
                blocks.append(g[name].to_numpy(dtype=float)[:, None])
        x = np.column_stack(blocks) if blocks else np.empty((len(g), 0))
        try:
            resid = _ols_residual(y, x)
            g["value"] = resid
            g["fallback"] = ""
        except np.linalg.LinAlgError:
            g["value"] = y
            g["fallback"] = "SINGULAR"
        out.append(g)
    if not out:
        return pd.DataFrame(columns=["symbol", "rebalance_date", "value", "raw_value", "n_obs"])
    result = pd.concat(out, ignore_index=True)
    if "fallback" not in result.columns:
        result["fallback"] = ""
    result["n_obs"] = result.groupby("rebalance_date")["value"].transform("size")
    if "raw_value" not in result.columns:
        result["raw_value"] = result["value"]
    columns = ["symbol", "rebalance_date", "value", "raw_value", "n_obs", "fallback"]
    columns += [c for c in ("log_mv", "industry") if c in result.columns]
    return result[columns]


if __name__ == "__main__":
    raise SystemExit("请通过 compute/验证脚本调用本模块。")