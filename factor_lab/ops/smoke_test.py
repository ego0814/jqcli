# -*- coding: utf-8 -*-
"""
因子实现冒烟测试
================
用合成的宽表数据(小规模)验证 alpha101.py / gtja191.py 能跑通:
  1. 无异常抛出
  2. 输出维度正确
  3. 每个因子都有非 NaN 的有效值
  4. 因子值不是常数(退化检测)

不依赖下载完成的真实数据, 可并行运行。
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
import time
import traceback

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import ops as O
import alpha101 as A1
import gtja191 as G1

rng = np.random.default_rng(42)

# ---------- 构造合成宽表: 60 只股票 × 260 个交易日 ----------
N_STOCK, N_DAY = 60, 260
codes = [f"{600000+i}.SH" for i in range(N_STOCK // 2)] + \
        [f"{i:06d}.SZ" for i in range(N_STOCK // 2)]
dates = pd.bdate_range("2023-01-03", periods=N_DAY)

# 几何随机游走价格 (先造收盘价, 再由它在 [low,high] 内的随机位置反推 O/H/L)
ret = rng.normal(0, 0.02, size=(N_DAY, N_STOCK))
base = 20.0 * np.exp(np.cumsum(ret, axis=0))

# 真实数据里 close 在 [low,high] 内的位置是随机的(非对称)。
# 若用 high=c*(1+s), low=c*(1-s) 这种对称构造, (c-l)/(h-l) 会恒等于 0.5,
# 导致 gtja_108/119/130 被误判为"退化因子"。
u_low = np.abs(rng.normal(0.010, 0.006, size=(N_DAY, N_STOCK)))
u_high = np.abs(rng.normal(0.010, 0.006, size=(N_DAY, N_STOCK)))
low_arr = base * (1 - u_low)
high_arr = base * (1 + u_high)
close_arr = low_arr + (high_arr - low_arr) * rng.random((N_DAY, N_STOCK))
open_arr = low_arr + (high_arr - low_arr) * rng.random((N_DAY, N_STOCK))

low = pd.DataFrame(low_arr, index=dates, columns=codes)
high = pd.DataFrame(high_arr, index=dates, columns=codes)
close = pd.DataFrame(close_arr, index=dates, columns=codes)
open_ = pd.DataFrame(open_arr, index=dates, columns=codes)
volume = pd.DataFrame(
    np.abs(rng.normal(5e6, 1.5e6, size=(N_DAY, N_STOCK))),
    index=dates, columns=codes)
# VWAP ≈ 日内典型价 (h+l+c)/3 + 噪声, 而不是退化成 close。
# 若令 amount = volume*close*100 再反解 vwap, 会得到 vwap ≡ close 的循环定义,
# 使 alpha057 / gtja_116 这类 (vwap - c) 因子被误判为"退化"。
vwap = ((high + low + close) / 3) * (1 + rng.normal(0, 0.003, (N_DAY, N_STOCK)))
vwap = pd.DataFrame(np.asarray(vwap), index=dates, columns=codes)
amount = volume * vwap * 100.0
cap = close * 1e9 / 20.0  # 对数市值

D1 = {
    "open": open_, "high": high, "low": low, "close": close,
    "volume": volume, "amount": amount, "vwap": vwap,
    # 论文的口径: volume = 成交额(美元)。这里用 amount 代表成交额, 与 adv{d} 同量级,
    # 否则 alpha007 的 adv20 < v 会因量纲不一致而恒不成立。
    "volume_dollar": amount,
    "returns": close.pct_change().replace([np.inf, -np.inf], np.nan),
    "cap": cap,
}
D2 = dict(D1)

LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def check(name, res, expect_lo, expect_hi):
    """返回 (状态, 说明)"""
    if res is None:
        return "NONE", "返回 None"
    if not isinstance(res, pd.DataFrame):
        return "ERR", f"类型异常 {type(res).__name__}"
    if res.shape != (N_DAY, N_STOCK):
        return "ERR", f"维度异常 {res.shape} != {(N_DAY, N_STOCK)}"
    valid = res.notna().sum().sum()
    total = N_DAY * N_STOCK
    rate = valid / total if total else 0
    if valid == 0:
        return "EMPTY", "全 NaN"
    # 退化检测: 标准差为 0 或接近 0
    s = res.stack()
    if len(s) == 0:
        return "EMPTY", "无有效值"
    if float(s.std()) < 1e-12:
        return "CONST", f"近似常数 (std={float(s.std()):.3e}, 有效 {rate:.1%})"
    return "OK", f"有效率 {rate:.1%}, std={float(s.std()):.3e}"


# ========== Alpha101 ==========
log("=" * 70)
log("Alpha101 冒烟测试")
log("=" * 70)
t0 = time.time()
try:
    alphas = A1.make_alphas(D1)
    log(f"make_alphas() 成功, {len(alphas)} 个因子, 耗时 {time.time()-t0:.1f}s")
except Exception:
    log("make_alphas() 抛出异常:")
    log(traceback.format_exc())
    alphas = {}

a1_status = {}
for k in sorted(alphas.keys()):
    st, msg = check(k, alphas[k], 0, 0)
    a1_status[st] = a1_status.get(st, 0) + 1
    if st != "OK":
        log(f"  [{st:5s}] {k:12s} {msg}")

log("")
log(f"Alpha101 状态汇总: {a1_status}")
log(f"预期 101 个, 实际 {len(alphas)} 个")

# ========== GTJA191 ==========
log("")
log("=" * 70)
log("GTJA191 冒烟测试")
log("=" * 70)
t0 = time.time()
try:
    gtja = G1.make_gtja191(D2)
    log(f"make_gtja191() 成功, {len(gtja)} 个因子, 耗时 {time.time()-t0:.1f}s")
except Exception:
    log("make_gtja191() 抛出异常:")
    log(traceback.format_exc())
    gtja = {}

g_status = {}
for k in sorted(gtja.keys()):
    st, msg = check(k, gtja[k], 0, 0)
    g_status[st] = g_status.get(st, 0) + 1
    if st != "OK":
        log(f"  [{st:5s}] {k:12s} {msg}")

log("")
log(f"GTJA191 状态汇总: {g_status}")
log(f"预期 191 个, 实际 {len(gtja)} 个")

# ========== 写出汇总 ==========
log("")
log("=" * 70)
log("总汇总")
log("=" * 70)
log(f"Alpha101 : {len(alphas)} 个, OK {a1_status.get('OK',0)}, "
    f"异常 {sum(v for k,v in a1_status.items() if k!='OK')}")
log(f"GTJA191  : {len(gtja)} 个, OK {g_status.get('OK',0)}, "
    f"异常 {sum(v for k,v in g_status.items() if k!='OK')}")

with open(os.path.join(BASE, "smoke_test_result.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(LOG))

log("\n已写出 smoke_test_result.txt")
