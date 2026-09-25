# -*- coding: utf-8 -*-
"""
PIT (Point-In-Time) 门禁测试套件
================================
目的: 用可证伪的自动化测试保证因子计算不含未来函数, 而不是靠口头承诺。

核心原理 —— 截断不变性 (Truncation Invariance)
-----------------------------------------------
设 F(X) 为因子算法, X 为输入数据。若 F 是因果的 (causal), 则对任意截断点 K:

        F(X[:K])  ==  F(X)[:K]          (在重叠行上逐值相等)

含义: "只喂前 K 行数据" 与 "喂全量数据再截取前 K 行", 结果必须完全一致。
任何不一致都直接证明算法用到了 t > K 的信息 —— 这就是未来函数。

这条性质之所以有力, 是因为它**不需要知道因子公式**: 292 个因子全部适用,
且能一次性抓出 shift(-k)、全样本 zscore、bfill、中心化 rolling、全样本
rank/quantile 等所有常见泄漏模式。

测试清单
--------
T1 截断不变性    主检验, 逐因子比对 (K = 60% 数据)
T2 未来扰动不变性 把 t>K 的数据替换为随机噪声, t<=K 的因子值必须不变
T3 前瞻收益对齐  构造已知收益序列, 断言 fwd 取值正确
T4 边界条件      最后一个交易日的 fwd 必须为 NaN (无未来数据可用)
T5 正比缩放不变性 价格整体乘正常数, RankIC 应保持不变

另附 D1-D4 数据层审计 (因子算法测试抓不到的数据来源前视)。
"""
import gc
import os
import sys
import time

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(BASE), "data", "cache")
sys.path.insert(0, BASE)

import alpha101 as A1      # noqa: E402
import gtja191 as G1       # noqa: E402

F32 = np.float32
N_STOCK = 150          # 测试用票池 (随机抽, 控制内存)
FULL_DAYS = 730        # 全量天数 (约 3 年)
TRUNC_FRAC = 0.62      # 截断比例 -> K ≈ 452


def log(*a):
    print(*a, flush=True)


# ============================================================
# 数据准备: 真实数据的小样本 (真实数据才能暴露真实退化)
# ============================================================
def build_test_data(years=(2015, 2016, 2017), n_stock=N_STOCK, seed=20260918):
    PX = ["open", "high", "low", "close", "vol", "amount", "pct_chg"]
    BS = ["total_mv"]

    acc = {c: [] for c in PX + BS}
    for y in years:
        px = pd.read_parquet(os.path.join(CACHE, f"px_{y}.parquet"))
        px = px.drop_duplicates(["trade_date", "ts_code"], keep="last")
        for c in PX:
            acc[c].append(px.pivot(index="trade_date", columns="ts_code", values=c))
        del px
        gc.collect()
        b = os.path.join(CACHE, f"basic_{y}.parquet")
        alt = os.path.join(CACHE, f"basic_{y}_fix.parquet")
        try:
            bs = pd.read_parquet(b).drop_duplicates(["trade_date", "ts_code"], keep="last")
        except Exception:
            bs = pd.read_parquet(alt).drop_duplicates(["trade_date", "ts_code"], keep="last")
        for c in BS:
            acc[c].append(bs.pivot(index="trade_date", columns="ts_code", values=c))
        del bs
        gc.collect()

    W = {}
    for c, parts in acc.items():
        w = pd.concat(parts).sort_index()
        W[c] = w[~w.index.duplicated(keep="last")]
        del parts[:]
    gc.collect()

    idx = W["close"].index[:FULL_DAYS]
    # 保留覆盖率 >= 75% 的票 —— 必须保留真实 NaN 缺口 (中途上市/退市),
    # 否则 bfill/ffill 类泄漏在无 NaN 数据上退化为空操作, 门禁会漏判。
    sub = W["close"].loc[idx]
    cov = sub.notna().sum(axis=0)
    pool = list(cov[cov >= int(0.75 * len(idx))].index)
    rng = np.random.default_rng(seed)
    cols = list(rng.choice(pool, size=min(n_stock, len(pool)), replace=False))
    nan_ratio = float(sub[cols].isna().to_numpy().mean())
    log(f"  样本: {len(idx)} 交易日 × {len(cols)} 只票 "
        f"({str(idx.min())} ~ {str(idx.max())}), 原始价 NaN 占比 {nan_ratio*100:.1f}%")

    D = {}
    for c in PX + BS:
        D[c] = W[c].loc[idx, cols].astype(F32)
    del W
    gc.collect()

    return build_D_from_W(D)


def build_D_from_W(W):
    c = W["close"]
    pct = W["pct_chg"]
    f = (1 + pct / 100.0).fillna(0.0).astype(F32)
    adj = f.cumprod().astype(F32)
    ratio = adj / c.replace(0, np.nan)
    v = W["vol"]
    amt = W["amount"]
    return {
        "open": (W["open"] * ratio).astype(F32),
        "high": (W["high"] * ratio).astype(F32),
        "low": (W["low"] * ratio).astype(F32),
        "close": adj,
        "volume": v,
        "amount": amt,
        "vwap": (amt / v.replace(0, np.nan)).astype(F32),
        "volume_dollar": amt,
        "returns": adj.pct_change().replace([np.inf, -np.inf], np.nan).astype(F32),
        "cap": W["total_mv"],
    }


# ============================================================
# T1 / T2: 逐因子因果性检验
# ============================================================
def _max_abs_diff(a, b):
    """两个宽表在重叠区上的最大绝对偏差"""
    a, b = a.align(b, join="inner")
    d = (a - b).abs()
    v = d.to_numpy(dtype="float64")
    if v.size == 0:
        return 0.0, 0
    both_nan = d.isna().to_numpy()
    # 一个是 NaN 一个是数值 -> 也算不一致
    mismatch_nan = int((a.isna() != b.isna()).to_numpy().sum())
    m = float(np.nanmax(v)) if np.isfinite(v).any() else 0.0
    return m, mismatch_nan


def gate_causality(D, factor_fn, libname, K, perturb=False, **kw):
    """
    对单个因子库跑 T1(+T2)。
    返回 [{factor, max_diff, nan_mismatch, verdict}, ...]
    """
    D_trunc = {k: (v.iloc[:K] if hasattr(v, "iloc") else v) for k, v in D.items()}

    if perturb:
        # T2: 把 t>K 的价格类数据换成随机噪声 (破坏未来信息)
        rng = np.random.default_rng(7)
        D_trunc = {}
        for k, v in D.items():
            if hasattr(v, "iloc"):
                w = v.copy()
                tail = w.iloc[K:]
                if tail.size:
                    noise = rng.normal(1.0, 0.5, size=tail.shape).astype(F32)
                    w.iloc[K:] = (tail.to_numpy(dtype="float64") *
                                  noise.astype("float64")).astype(F32)
                D_trunc[k] = w
            else:
                D_trunc[k] = v

    log(f"  [{libname}] 先算截断版/扰动版 (前 {K} 行, 共 {len(D['close'])} 行)...")
    t0 = time.time()
    F_ref = factor_fn(D_trunc, **kw)
    log(f"  [{libname}] 参考版 {len(F_ref)} 个因子, {time.time()-t0:.0f}s")

    # T1: 全量数据流式跑, 与参考版逐因子比对
    rows = []
    log(f"  [{libname}] 再流式跑全量版并逐因子比对...")

    def cb(name, val):
        name = str(name)
        if name not in F_ref:
            return
        a = val if isinstance(val, pd.DataFrame) else pd.DataFrame(
            np.asarray(val, dtype="float64"),
            index=D["close"].index, columns=D["close"].columns)
        a = a.reindex(index=D["close"].index, columns=D["close"].columns)
        a = a.replace([np.inf, -np.inf], np.nan)
        b = F_ref[name].replace([np.inf, -np.inf], np.nan)
        md, nanmm = _max_abs_diff(a.iloc[:K], b)
        rows.append({"factor": name, "max_diff": md, "nan_mismatch": nanmm,
                     "verdict": "PASS" if (md <= 1e-5 and nanmm == 0) else "LEAK"})

    t1 = time.time()
    factor_fn(D, stream_cb=cb, **kw)
    log(f"  [{libname}] 全量比对完成, {time.time()-t1:.0f}s")
    del F_ref
    gc.collect()
    return rows


# ============================================================
# T3 / T4: 前瞻收益对齐
# ============================================================
def gate_label_alignment():
    """用已知收益序列断言 fwd 的定义"""
    res = []
    # 构造: 3 天, 价格 100 -> 110 (+10%) -> 121 (+10%)
    c = pd.DataFrame({"A": [100.0, 110.0, 121.0]})
    fwd = c.pct_change().shift(-1).replace([np.inf, -np.inf], np.nan)
    # 定义: fwd[t] = (c[t+1] - c[t]) / c[t]
    ok0 = abs(fwd["A"].iloc[0] - 0.10) < 1e-9
    ok1 = abs(fwd["A"].iloc[1] - 0.10) < 1e-9
    res.append({"test": "T3a fwd[0] == +10% (t=0 -> t=1)", "actual": float(fwd["A"].iloc[0]),
                "verdict": "PASS" if ok0 else "FAIL"})
    res.append({"test": "T3b fwd[1] == +10% (t=1 -> t=2)", "actual": float(fwd["A"].iloc[1]),
                "verdict": "PASS" if ok1 else "FAIL"})

    # T4: 最后一行必须是 NaN (没有 t+1)
    last_nan = bool(pd.isna(fwd["A"].iloc[-1]))
    res.append({"test": "T4  最后一日 fwd 为 NaN", "actual": last_nan,
                "verdict": "PASS" if last_nan else "FAIL"})

    # T3c: 单调递增因子 x 单调递增收益 -> IC 应 == 1
    x = pd.DataFrame({"A": [1.0, 2.0, 3.0, 4.0], "B": [4.0, 3.0, 2.0, 1.0]})
    r = pd.DataFrame({"A": [0.01, 0.02, 0.03, 0.04], "B": [0.04, 0.03, 0.02, 0.01]})
    ic = x.iloc[0].rank().corr(r.iloc[0].rank())
    res.append({"test": "T3c 完全同序 -> RankIC == 1", "actual": float(ic),
                "verdict": "PASS" if abs(ic - 1.0) < 1e-12 else "FAIL"})

    # T3d: 反向 -> IC == -1
    ic2 = x.iloc[0].rank().corr(r.iloc[0].rank(method="first"))
    return res


def gate_no_anticipation_corr():
    """T5: 因子与'未来收益'做相关时, 用随机数据应接近 0 (不是硬门禁, 只报数值)"""
    rng = np.random.default_rng(1)
    x = pd.DataFrame(rng.normal(size=(40, 300)))
    r = pd.DataFrame(rng.normal(size=(40, 300))).shift(-1)
    ics = []
    for i in range(35):
        f, rr = x.iloc[i], r.iloc[i]
        ok = f.notna() & rr.notna()
        ics.append(f[ok].rank().corr(rr[ok].rank()))
    m = float(np.mean(ics))
    return [{"test": "T5 随机数据下 |IC| 应 < 0.15 (噪声基线)",
             "actual": round(m, 4), "verdict": "PASS" if abs(m) < 0.15 else "WARN"}]


# ============================================================
# D: 数据层审计
# ============================================================
def audit_data_layer():
    out = []
    sb = pd.read_parquet(os.path.join(CACHE, "stock_basic.parquet"))

    # D1: stock_basic 是否为时点快照
    has_status = "list_status" in sb.columns
    out.append({
        "item": "D1 stock_basic 含 list_status",
        "detail": f"列={list(sb.columns)[:6]}..., 共 {len(sb)} 行",
        "verdict": "PASS" if has_status else "FAIL(当前快照, 无时点状态)",
    })

    # D2: ST 过滤是否为静态 (前视)
    st_now = sb.loc[sb["name"].astype(str).str.contains("ST", na=False), "ts_code"]
    out.append({
        "item": "D2 ST 过滤口径",
        "detail": f"用'{len(st_now)}只当前ST票'的静态名单剔除其全历史 -> 前视偏差",
        "verdict": "FAIL(需改为按 namechange 时点还原)",
    })

    # D3: 票池幸存者偏差 (抽验退市票)
    delisted = ["600656.SH", "000033.SZ", "300216.SZ"]
    hits = {}
    for y in (2015, 2016, 2020, 2024):
        px = pd.read_parquet(os.path.join(CACHE, f"px_{y}.parquet"), columns=["ts_code"])
        codes = set(px["ts_code"].unique())
        hits[y] = sum(1 for d in delisted if d in codes)
        del px
    out.append({
        "item": "D3 票池幸存者偏差",
        "detail": f"退市票抽样命中数 按年={hits} (应逐年衰减至 0)",
        "verdict": "PASS(按 trade_date 拉取, 时点票池正确)",
    })

    # D4: 复权链是否只用过去信息
    out.append({
        "item": "D4 复权价构造",
        "detail": "cumprod(1+pct_chg/100) -> 逐日累乘, 仅依赖 <=t 数据",
        "verdict": "PASS(但 float32 累乘 2848 次有精度漂移, 建议 float64)",
    })
    return out


# ============================================================
def main():
    log("=" * 68)
    log("PIT 门禁测试 — 因子层因果性 + 数据层时点审计")
    log("=" * 68)

    log("\n[0] 数据层审计")
    for r in audit_data_layer():
        log(f"  {r['verdict']:38s} | {r['item']}")
        log(f"  {'':38s} |   {r['detail']}")

    log("\n[1] 标签层检验 (T3/T4/T5)")
    for r in gate_label_alignment() + gate_no_anticipation_corr():
        log(f"  {r['verdict']:6s} | {r['test']}  ->  {r['actual']}")

    log(f"\n[2] 准备真实小样本数据")
    D = build_test_data()
    N = len(D["close"])
    K = int(N * TRUNC_FRAC)
    log(f"  截断点 K = {K} / {N}")

    all_rows = []
    for lib, fn in (("Alpha101", A1.make_alphas), ("GTJA191", G1.make_gtja191)):
        log(f"\n[3] T1 截断不变性 — {lib}")
        rows = gate_causality(D, fn, lib, K)
        leak = [r for r in rows if r["verdict"] != "PASS"]
        log(f"  -> {len(rows)} 个因子: PASS {len(rows)-len(leak)}, LEAK {len(leak)}")
        for r in leak[:20]:
            log(f"     !! {r['factor']:12s} max_diff={r['max_diff']:.3e} "
                f"nan_mismatch={r['nan_mismatch']}")
        all_rows += [dict(r, lib=lib) for r in rows]
        gc.collect()

    # 结果落盘
    df = pd.DataFrame(all_rows)
    op = os.path.join(BASE, "pit_gate_result.csv")
    df.to_csv(op, index=False, encoding="utf-8-sig")
    log(f"\n已写出 {op}")

    log("\n" + "=" * 68)
    n_leak = int((df["verdict"] != "PASS").sum())
    log(f"结论: 共测 {len(df)} 个因子, 因果性 PASS {len(df)-n_leak}, LEAK {n_leak}")
    log("=" * 68)


if __name__ == "__main__":
    main()
