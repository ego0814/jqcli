# -*- coding: utf-8 -*-
"""
GTJA191 因子库 (国泰君安《基于短周期量价特征的多因子选股体系》, 2014)
=================================================================
输入约定 (宽表 index=date, columns=code):
  open, high, low, close, volume, amount, vwap, returns, cap, benchmark_open/close

SMA(X, N, M) : 中国式 SMA, Y_t = (M*X_t + (N-M)*Y_{t-1}) / N
"""
import numpy as np
import pandas as pd

from ops import (abs_, correlation, covariance, decay_linear, delay, delta,
                 greater_than, less_than, log, rank, scale, sign, signed_power,
                 ts_argmax, ts_argmin, ts_max, ts_mean, ts_min, ts_product,
                 ts_rank, ts_std, ts_sum)
from alpha101 import _StreamDict


def SMA(x, n, m=1):
    """中国式移动平均: Y_t = (m*x_t + (n-m)*Y_{t-1}) / n"""
    alpha = m / n
    return x.ewm(alpha=alpha, adjust=False, min_periods=max(2, n // 3)).mean()


def MEAN(x, n):
    return ts_mean(x, n)


def STD(x, n):
    return ts_std(x, n)


def SUM(x, n):
    return ts_sum(x, n)


def COUNT(cond, n):
    """过去 n 日满足条件的天数"""
    return cond.astype(float).rolling(n, min_periods=1).sum()


def HIGHDAY(x, n):
    return n - 1 - ts_argmax(x, n)


def LOWDAY(x, n):
    return n - 1 - ts_argmin(x, n)


def REF(x, n):
    return x.shift(n)


def DIFF(x, n):
    return x - x.shift(n)


def MAX(a, b):
    if isinstance(b, pd.DataFrame):
        return np.maximum(a.values if isinstance(a, pd.DataFrame) else a,
                          b.values) if isinstance(a, pd.DataFrame) else np.maximum(a, b)
    return np.maximum(a, b)


def MIN(a, b):
    if isinstance(b, pd.DataFrame):
        return np.minimum(a.values if isinstance(a, pd.DataFrame) else a,
                          b.values) if isinstance(a, pd.DataFrame) else np.minimum(a, b)
    return np.minimum(a, b)


def _df(arr, ref):
    return pd.DataFrame(arr, index=ref.index, columns=ref.columns)


def _safe_frac(num, den):
    """除法保护: 分母为 0 或 NaN 时返回 NaN, 避免 inf 污染"""
    return (num / den.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def make_gtja191(D, benchmark=None, stream_cb=None, want=None, stop_after=None):
    """
    D: dict 含 open/high/low/close/volume/amount/vwap/returns/cap
    benchmark: Series (index=date) 基准收盘价, 用于部分因子; 缺失则用全市场等权均值替代
    返回 dict {gtja_001 ... gtja_191}
    """
    o, h, l, c = D["open"], D["high"], D["low"], D["close"]
    v, amt, vwap, ret = D["volume"], D["amount"], D["vwap"], D["returns"]
    cap = D["cap"]
    idx, cols = c.index, c.columns

    if benchmark is None:
        benchmark = c.mean(axis=1)
    bm = pd.DataFrame(np.repeat(benchmark.values[:, None], len(cols), axis=1),
                      index=idx, columns=cols)
    # 全市场等权收益 (BANCHMARKINDEXCLOSE 代理)
    bm_ret = bm.pct_change()

    A = _StreamDict(stream_cb, want=want, stop_after=stop_after)
    def put(k, val):
        if isinstance(val, pd.DataFrame):
            A[k] = val.replace([np.inf, -np.inf], np.nan)
        else:
            A[k] = _df(np.asarray(val, dtype=float), c).replace([np.inf, -np.inf], np.nan)
    V1 = v
    # 001: -1*CORR(RANK(DELTA(LOG(VOLUME),1)),RANK((CLOSE-OPEN)/OPEN),6)
    if A.want("gtja_001"):
        put("gtja_001", -1 * correlation(rank(delta(log(v), 1)), rank((c - o) / o), 6))
    # 注: gtja_002 原式 = -1*DELTA(((C-L)-(H-C))/(H-L),1)。当 H==L(一字板)时原式为 0/0,
    #     这里改用日内振幅占比的差分, 保持经济含义的同时避免退化。
    _amp = _safe_frac((c - l) - (h - c), (h - l).replace(0, np.nan))
    if A.want("gtja_002"):
        put("gtja_002", -1 * delta(_amp.fillna(0.0), 1))
    # 003: SUM((CLOSE==DELAY(CLOSE,1)?0:CLOSE-(CLOSE>DELAY(CLOSE,1)?MIN(LOW,DELAY(CLOSE,1)):MAX(HIGH,DELAY(CLOSE,1)))),6)
    prev = delay(c, 1)
    part = np.where(c.eq(prev).values, 0.0,
                    np.where(c.gt(prev).values, np.minimum(l.values, prev.values),
                             np.maximum(h.values, prev.values)))
    if A.want("gtja_003"):
        put("gtja_003", ts_sum(_df(c.values - part, c), 6))
    # 004: 复杂条件计数
    cond4 = ((ts_sum(c, 8) / 8) + ts_std(c, 8)) < (ts_sum(c, 2) / 2)
    cond4b = (ts_sum(c, 2) / 2) < ((ts_sum(c, 8) / 8) - ts_std(c, 8))
    cond4c = (c == 1) | (c <= 0)
    if A.want("gtja_004"):
        put("gtja_004", ts_sum(cond4.astype(float), 8) if False else
            (COUNT(cond4, 8) * -1 + COUNT(cond4b, 8) * 1) + COUNT(cond4c, 8))
    # 005: -1*TSMAX(CORR(TSRANK(VOLUME,5),TSRANK(HIGH,5),5),3)
    if A.want("gtja_005"):
        put("gtja_005", -1 * ts_max(correlation(ts_rank(v, 5), ts_rank(h, 5), 5), 3))
    # 006: RANK(SIGN(DELTA(OPEN*0.85+HIGH*0.15,4)))*-1
    if A.want("gtja_006"):
        put("gtja_006", -1 * rank(sign(delta(o * 0.85 + h * 0.15, 4))))
    # 007: (RANK(MAX(VWAP-CLOSE,3))+RANK(MIN(VWAP-CLOSE,3)))*RANK(DELTA(VOLUME,3))
    if A.want("gtja_007"):
        put("gtja_007", (rank(ts_max(vwap - c, 3)) + rank(ts_min(vwap - c, 3))) * rank(delta(v, 3)))
    # 008: RANK(DELTA((HIGH+LOW)/2*0.2+VWAP*0.8,4))*-1
    if A.want("gtja_008"):
        put("gtja_008", -1 * rank(delta((h + l) / 2 * 0.2 + vwap * 0.8, 4)))
    # 009: SMA(((HIGH+LOW)/2-(DELAY(HIGH,1)+DELAY(LOW,1))/2)*(HIGH-LOW)/VOLUME,7,2)
    if A.want("gtja_009"):
        put("gtja_009", SMA(((h + l) / 2 - (delay(h, 1) + delay(l, 1)) / 2) * (h - l) / v, 7, 2))
    # 010: RANK(MAX(((RET<0)?STD(RET,20):CLOSE)^2,5))
    r20 = ts_std(ret, 20)
    base10 = _df(np.where(ret.values < 0, r20.values, c.values), c)
    if A.want("gtja_010"):
        put("gtja_010", rank(ts_max(base10 ** 2, 5)))
    # 011: SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME,6)
    if A.want("gtja_011"):
        put("gtja_011", ts_sum(((c - l) - (h - c)) / (h - l) * v, 6))
    # 012: RANK(OPEN-(SUM(VWAP,10)/10))*-1
    if A.want("gtja_012"):
        put("gtja_012", -1 * rank(o - ts_sum(vwap, 10) / 10))
    # 013: (HIGH*LOW)^0.5 - VWAP
    if A.want("gtja_013"):
        put("gtja_013", (h * l) ** 0.5 - vwap)
    # 014: CLOSE-DELAY(CLOSE,5)
    if A.want("gtja_014"):
        put("gtja_014", c - delay(c, 5))
    # 015: OPEN/DELAY(CLOSE,1)-1
    if A.want("gtja_015"):
        put("gtja_015", o / delay(c, 1) - 1)
    # 016: -1*TSMAX(RANK(CORR(RANK(VOLUME),RANK(VWAP),5)),5)
    if A.want("gtja_016"):
        put("gtja_016", -1 * ts_max(rank(correlation(rank(v), rank(vwap), 5)), 5))
    # 017: RANK(VWAP-MAX(VWAP,15))^DELTA(CLOSE,5)
    if A.want("gtja_017"):
        put("gtja_017", signed_power(rank(vwap - ts_max(vwap, 15)), delta(c, 5)))
    # 018: CLOSE/DELAY(CLOSE,5)
    if A.want("gtja_018"):
        put("gtja_018", c / delay(c, 5))
    # 019: (CLOSE<DELAY(CLOSE,5)?(CLOSE-DELAY(CLOSE,5))/DELAY(CLOSE,5):(CLOSE-DELAY(CLOSE,5))/CLOSE)
    d5 = delay(c, 5)
    if A.want("gtja_019"):
        put("gtja_019", _df(np.where(c.values < d5.values, (c.values - d5.values) / d5.values,
                                     (c.values - d5.values) / np.where(c.values == 0, np.nan, c.values)), c))
    # 020: (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100
    if A.want("gtja_020"):
        put("gtja_020", (c - delay(c, 6)) / delay(c, 6) * 100)
    # 021: REGBETA(MEAN(CLOSE,6),SEQUENCE(6)) —— 用滚动线性回归斜率近似
    if A.want("gtja_021"):
        put("gtja_021", _rolling_slope(ts_mean(c, 6), 6))
    # 022: SMA(((CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6)-DELAY(...)) ,12,1)
    m6 = ts_mean(c, 6)
    dev = (c - m6) / m6
    if A.want("gtja_022"):
        put("gtja_022", SMA(dev - delay(dev, 3), 12, 1))
    # 023: SMA((CLOSE>DELAY(CLOSE,1)?STD(CLOSE,20):0),20,1)/(SMA(...)+SMA(...))*100
    cond_up = (c > delay(c, 1)).astype(float) * ts_std(c, 20)
    cond_dn = (c < delay(c, 1)).astype(float) * ts_std(c, 20)
    su, sd = SMA(cond_up, 20, 1), SMA(cond_dn, 20, 1)
    if A.want("gtja_023"):
        put("gtja_023", su / (su + sd) * 100)
    # 024: SMA(CLOSE-DELAY(CLOSE,5),5,1)
    if A.want("gtja_024"):
        put("gtja_024", SMA(c - delay(c, 5), 5, 1))
    # 025: -1*RANK(DELTA(CLOSE,7)*(1-RANK(DECAYLINEAR(VOLUME/MEAN(VOLUME,20),9))))
    if A.want("gtja_025"):
        put("gtja_025", -1 * rank(delta(c, 7) * (1 - rank(decay_linear(v / ts_mean(v, 20), 9)))))
    # 026: (((SUM(CLOSE,7)/7)-CLOSE))+((CORR(VWAP,DELAY(CLOSE,5),230)))
    if A.want("gtja_026"):
        put("gtja_026", (ts_sum(c, 7) / 7 - c) + correlation(vwap, delay(c, 5), 230))
    # 027: WMA((CLOSE-DELAY(CLOSE,3))/DELAY(CLOSE,3)*100+(CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*100,12)
    t27 = (c - delay(c, 3)) / delay(c, 3) * 100 + (c - delay(c, 6)) / delay(c, 6) * 100
    if A.want("gtja_027"):
        put("gtja_027", decay_linear(t27, 12))
    # 028: 3*SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100,3,1)-2*SMA(SMA(...,3,1),3,1)
    k28 = (c - ts_min(l, 9)) / (ts_max(h, 9) - ts_min(l, 9)) * 100
    s28 = SMA(k28, 3, 1)
    if A.want("gtja_028"):
        put("gtja_028", 3 * s28 - 2 * SMA(s28, 3, 1))
    # 029: (CLOSE-DELAY(CLOSE,6))/DELAY(CLOSE,6)*VOLUME
    if A.want("gtja_029"):
        put("gtja_029", (c - delay(c, 6)) / delay(c, 6) * v)
    # 030: WMA((CLOSE/DELAY(CLOSE,1)-1-(HIGH/LOW-1)),5)*100  —— 非FF依赖版本
    if A.want("gtja_030"):
        put("gtja_030", decay_linear((c / delay(c, 1) - 1) - (h / l - 1), 5) * 100)
    # 031: (CLOSE-MEAN(CLOSE,12))/MEAN(CLOSE,12)*100
    if A.want("gtja_031"):
        put("gtja_031", (c - ts_mean(c, 12)) / ts_mean(c, 12) * 100)
    # 032: -1*SUM(RANK(CORR(RANK(HIGH),RANK(VOLUME),3)),3)
    if A.want("gtja_032"):
        put("gtja_032", -1 * ts_sum(rank(correlation(rank(h), rank(v), 3)), 3))
    # 033: ((((-1*TSMIN(LOW,5))+DELAY(TSMIN(LOW,5),5))*RANK(((SUM(RET,240)-SUM(RET,20))/220)))*TSRANK(VOLUME,5))
    if A.want("gtja_033"):
        put("gtja_033", ((-1 * ts_min(l, 5) + delay(ts_min(l, 5), 5)) *
                         rank((ts_sum(ret, 240) - ts_sum(ret, 20)) / 220)) * ts_rank(v, 5))
    # 034: MEAN(CLOSE,12)/CLOSE
    if A.want("gtja_034"):
        put("gtja_034", ts_mean(c, 12) / c)
    # 035: (MIN(RANK(DECAYLINEAR(DELTA(OPEN,1),15)),RANK(DECAYLINEAR(CORR(VOLUME,OPEN*0.65+OPEN*0.35,17),7)))*-1)
    if A.want("gtja_035"):
        put("gtja_035", -1 * np.minimum(rank(decay_linear(delta(o, 1), 15)).values,
                                        rank(decay_linear(correlation(v, o, 17), 7)).values) * 1.0)
    # 036: RANK(SUM(CORR(RANK(VOLUME),RANK(VWAP),6),2))
    if A.want("gtja_036"):
        put("gtja_036", rank(ts_sum(correlation(rank(v), rank(vwap), 6), 2)))
    # 037: (-1*RANK((SUM(OPEN,5)*SUM(RET,5))-DELAY((SUM(OPEN,5)*SUM(RET,5)),10)))
    if A.want("gtja_037"):
        put("gtja_037", -1 * rank(ts_sum(o, 5) * ts_sum(ret, 5) - delay(ts_sum(o, 5) * ts_sum(ret, 5), 10)))
    # 038: (-1*RANK((SUM(HIGH,20)/20))<HIGH)?(-1*DELTA(HIGH,2)):0
    if A.want("gtja_038"):
        put("gtja_038", _df(np.where((ts_sum(h, 20) / 20).values < h.values,
                                     (-1 * delta(h, 2)).values, 0.0), c))
    # 039: RANK(DECAYLINEAR(DELTA(CLOSE,2),8))-RANK(DECAYLINEAR(CORR(VWAP*0.3+OPEN*0.7,SUM(MEAN(VOLUME,180),37),14),12))
    if A.want("gtja_039"):
        put("gtja_039", rank(decay_linear(delta(c, 2), 8)) -
            rank(decay_linear(correlation(vwap * 0.3 + o * 0.7, ts_sum(ts_mean(v, 180), 37), 14), 12)))
    # 040: SUM(CLOSE>DELAY(CLOSE,1?VOLUME:0),26)/SUM(VOLUME,26)*100
    if A.want("gtja_040"):
        put("gtja_040", ts_sum((c > delay(c, 1)).astype(float) * v, 26) / ts_sum(v, 26) * 100)
    # 041: RANK(MAX(DELTA(VWAP,3),5))*-1
    if A.want("gtja_041"):
        put("gtja_041", -1 * rank(ts_max(delta(vwap, 3), 5)))
    # 042: (-1*RANK(STD(HIGH,10)))*CORR(HIGH,VOLUME,10)
    if A.want("gtja_042"):
        put("gtja_042", -1 * rank(ts_std(h, 10)) * correlation(h, v, 10))
    # 043: SUM((CLOSE>DELAY(CLOSE,1)?VOLUME:(CLOSE<DELAY(CLOSE,1)?-VOLUME:0)),6)
    if A.want("gtja_043"):
        put("gtja_043", ts_sum(_df(np.where(c.values > delay(c, 1).values, v.values,
                                            np.where(c.values < delay(c, 1).values, -v.values, 0.0)), c), 6))
    # 044: TSRANK(DECAYLINEAR(CORR(LOW,MEAN(VOLUME,10),7),6),4)+TSRANK(DECAYLINEAR(DELTA(VWAP,3),10),15)
    if A.want("gtja_044"):
        put("gtja_044", ts_rank(decay_linear(correlation(l, ts_mean(v, 10), 7), 6), 4) +
            ts_rank(decay_linear(delta(vwap, 3), 10), 15))
    # 045: RANK(DELTA(CLOSE*0.6+OPEN*0.4,1))*RANK(CORR(VWAP,MEAN(VOLUME,150),15))
    if A.want("gtja_045"):
        put("gtja_045", rank(delta(c * 0.6 + o * 0.4, 1)) * rank(correlation(vwap, ts_mean(v, 150), 15)))
    # 046: 均量上涨占比 SMA
    m46 = ts_mean(v, 3)
    up = (m46 > delay(m46, 1)).astype(float)
    dn = (m46 < delay(m46, 1)).astype(float)
    su, sd = SMA(up, 20, 1), SMA(dn, 20, 1)
    if A.want("gtja_046"):
        put("gtja_046", (su / (su + sd)) * 100)
    # 047: SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100,9,1)
    if A.want("gtja_047"):
        put("gtja_047", SMA((ts_max(h, 6) - c) / (ts_max(h, 6) - ts_min(l, 6)) * 100, 9, 1))
    # 048: -1*((RANK(((SIGN((CLOSE-DELAY(CLOSE,1)))+SIGN((DELAY(CLOSE,1)-DELAY(CLOSE,2))))+SIGN((DELAY(CLOSE,2)-DELAY(CLOSE,3))))))*SUM(VOLUME,5))/SUM(VOLUME,20)
    sg = sign(c - delay(c, 1)) + sign(delay(c, 1) - delay(c, 2)) + sign(delay(c, 2) - delay(c, 3))
    if A.want("gtja_048"):
        put("gtja_048", -1 * (rank(sg) * ts_sum(v, 5)) / ts_sum(v, 20))
    # 049: SUM(((HIGH+LOW)>=(DELAY(HIGH,1)+DELAY(LOW,1))?0:MAX(ABS(HIGH-DELAY(HIGH,1)),ABS(LOW-DELAY(LOW,1)))),12)/(SUM(...,12)+SUM((HIGH+LOW)<=(...)?0:MAX(...),12))
    hl = h + l
    hl1 = delay(h, 1) + delay(l, 1)
    mx = np.maximum(abs_(h - delay(h, 1)).values, abs_(l - delay(l, 1)).values)
    a49 = ts_sum(_df(np.where(hl.values >= hl1.values, 0.0, mx), c), 12)
    b49 = ts_sum(_df(np.where(hl.values <= hl1.values, 0.0, mx), c), 12)
    if A.want("gtja_049"):
        put("gtja_049", a49 / (a49 + b49))
    # 050: SUM(((HIGH+LOW)<=(DELAY(HIGH,1)+DELAY(LOW,1))?0:MAX(ABS(HIGH-DELAY(HIGH,1)),ABS(LOW-DELAY(LOW,1)))),12)/...
    if A.want("gtja_050"):
        put("gtja_050", b49 / (a49 + b49))
    # 051: SUM(((HIGH+LOW)<=(DELAY(HIGH,1)+DELAY(LOW,1))?0:MAX(ABS(HIGH-DELAY(HIGH,1)),ABS(LOW-DELAY(LOW,1)))),12)/... 同50
    if A.want("gtja_051"):
        put("gtja_051", b49 / (a49 + b49))
    # 052: SUM(MAX(0,HIGH-DELAY((HIGH+LOW+CLOSE)/3,1)),26)/SUM(MAX(0,DELAY((H+L+C)/3,1)-LOW),26)*100
    tp = (h + l + c) / 3
    if A.want("gtja_052"):
        put("gtja_052", ts_sum(_df(np.maximum(0, (h - delay(tp, 1)).values), c), 26) /
            ts_sum(_df(np.maximum(0, (delay(tp, 1) - l).values), c), 26) * 100)
    # 053: COUNT(CLOSE>DELAY(CLOSE,1),12)/12*100
    if A.want("gtja_053"):
        put("gtja_053", COUNT(c > delay(c, 1), 12) / 12 * 100)
    # 054: -1*RANK((STD(ABS(CLOSE-OPEN),10)+(CLOSE-OPEN))+CORR(CLOSE,OPEN,10))
    if A.want("gtja_054"):
        put("gtja_054", -1 * rank(ts_std(abs_(c - o), 10) + (c - o) + correlation(c, o, 10)))
    # 055: SUM(16*(CLOSE-DELAY(CLOSE,1)+(CLOSE-OPEN)/2+DELAY(CLOSE,1)-DELAY(OPEN,1))/
    #         (DELAY(CLOSE,1)-DELAY(OPEN,1)+1),6)  -- 研报原式为加权的资金流向强度
    _gap = delay(c, 1) - delay(o, 1)
    _num = 16 * (delta(c, 1) + (c - o) / 2 + _gap)
    if A.want("gtja_055"):
        put("gtja_055", ts_sum(_num / (_gap + 1), 6))
    # 056: 0-RANK((SUM(OPEN,5)*SUM(RET,5))-DELAY((SUM(OPEN,5)*SUM(RET,5)),10))
    if A.want("gtja_056"):
        put("gtja_056", -rank(ts_sum(o, 5) * ts_sum(ret, 5) - delay(ts_sum(o, 5) * ts_sum(ret, 5), 10)))
    # 057: SMA((CLOSE-TSMIN(LOW,9))/(TSMAX(HIGH,9)-TSMIN(LOW,9))*100,3,1)
    if A.want("gtja_057"):
        put("gtja_057", SMA(k28, 3, 1))
    # 058: COUNT(CLOSE>DELAY(CLOSE,1),20)/20*100
    if A.want("gtja_058"):
        put("gtja_058", COUNT(c > delay(c, 1), 20) / 20 * 100)
    # 059: SUM((CLOSE=DELAY(CLOSE,1)?0:CLOSE-(CLOSE>DELAY(CLOSE,1)?MIN(LOW,DELAY(CLOSE,1)):MAX(HIGH,DELAY(CLOSE,1)))),20)
    if A.want("gtja_059"):
        put("gtja_059", ts_sum(_df(c.values - part, c), 20))
    # 060: SUM(((CLOSE-LOW)-(HIGH-CLOSE))/(HIGH-LOW)*VOLUME,20)
    if A.want("gtja_060"):
        put("gtja_060", ts_sum(((c - l) - (h - c)) / (h - l) * v, 20))
    # 061: MAX(RANK(DECAYLINEAR(DELTA(VWAP,1),12)),RANK(DECAYLINEAR(RANK(CORR(LOW,MEAN(VOLUME,80),8)),17)))*-1
    if A.want("gtja_061"):
        put("gtja_061", -1 * np.maximum(rank(decay_linear(delta(vwap, 1), 12)).values,
                                        rank(decay_linear(rank(correlation(l, ts_mean(v, 80), 8)), 17)).values))
    # 062: -1*CORR(HIGH,RANK(VOLUME),5)
    if A.want("gtja_062"):
        put("gtja_062", -1 * correlation(h, rank(v), 5))
    # 063: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),6,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),6,1)*100
    dc = c - delay(c, 1)
    if A.want("gtja_063"):
        put("gtja_063", SMA(_df(np.maximum(dc.values, 0), c), 6, 1) / SMA(abs_(dc), 6, 1) * 100)
    # 064: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1)*100
    if A.want("gtja_064"):
        put("gtja_064", SMA(_df(np.maximum(dc.values, 0), c), 12, 1) / SMA(abs_(dc), 12, 1) * 100)
    # 065: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1)*100
    if A.want("gtja_065"):
        put("gtja_065", SMA(_df(np.maximum(dc.values, 0), c), 24, 1) / SMA(abs_(dc), 24, 1) * 100)
    # 066: (CLOSE-MEAN(CLOSE,6))/MEAN(CLOSE,6)*100
    if A.want("gtja_066"):
        put("gtja_066", (c - ts_mean(c, 6)) / ts_mean(c, 6) * 100)
    # 067: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),24,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),24,1)*100
    if A.want("gtja_067"):
        put("gtja_067", SMA(_df(np.maximum(dc.values, 0), c), 24, 1) / SMA(abs_(dc), 24, 1) * 100)
    # 068: SMA(((HIGH+LOW)/2-(DELAY(HIGH,1)+DELAY(LOW,1))/2)*(HIGH-LOW)/VOLUME,15,2)
    if A.want("gtja_068"):
        put("gtja_068", SMA(((h + l) / 2 - (delay(h, 1) + delay(l, 1)) / 2) * (h - l) / v, 15, 2))
    # 069: DTM/DBM 比值
    dtm = _df(np.maximum((o - delay(o, 1)).values, 0), c)
    dbm = _df(np.maximum((delay(o, 1) - o).values, 0), c)
    s69 = ts_sum(dtm, 20) / (ts_sum(dtm, 20) + ts_sum(dbm, 20)) * 100
    if A.want("gtja_069"):
        put("gtja_069", s69)
    # 070: STD(AMOUNT,6)
    if A.want("gtja_070"):
        put("gtja_070", ts_std(amt, 6))
    # 071: (CLOSE-MEAN(CLOSE,24))/MEAN(CLOSE,24)*100
    if A.want("gtja_071"):
        put("gtja_071", (c - ts_mean(c, 24)) / ts_mean(c, 24) * 100)
    # 072: SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100,15,1)
    if A.want("gtja_072"):
        put("gtja_072", SMA((ts_max(h, 6) - c) / (ts_max(h, 6) - ts_min(l, 6)) * 100, 15, 1))
    # 073: -1*TSRANK(DECAYLINEAR(DECAYLINEAR(CORR(CLOSE,VOLUME,10),16),4),5)
    if A.want("gtja_073"):
        put("gtja_073", -1 * ts_rank(decay_linear(decay_linear(correlation(c, v, 10), 16), 4), 5))
    # 074: RANK(CORR(SUM(LOW*0.35+VWAP*0.65,20),SUM(MEAN(VOLUME,40),20),7))+RANK(CORR(RANK(VWAP),RANK(VOLUME),6))
    if A.want("gtja_074"):
        put("gtja_074", rank(correlation(ts_sum(l * 0.35 + vwap * 0.65, 20), ts_sum(ts_mean(v, 40), 20), 7)) +
            rank(correlation(rank(vwap), rank(v), 6)))
    # 075: COUNT(CLOSE>OPEN,50)/50*100
    if A.want("gtja_075"):
        put("gtja_075", COUNT(c > o, 50) / 50 * 100)
    # 076: STD(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)/MEAN(ABS((CLOSE/DELAY(CLOSE,1)-1))/VOLUME,20)
    rr = abs_(c / delay(c, 1) - 1) / v
    if A.want("gtja_076"):
        put("gtja_076", ts_std(rr, 20) / ts_mean(rr, 20))
    # 077: MIN(RANK(DECAYLINEAR((HIGH+LOW)/2+HIGH-(VWAP+HIGH),20)),RANK(DECAYLINEAR(CORR((HIGH+LOW)/2,MEAN(VOLUME,40),3),5)))
    if A.want("gtja_077"):
        put("gtja_077", np.minimum(rank(decay_linear((h + l) / 2 + h - (vwap + h), 20)).values,
                                   rank(decay_linear(correlation((h + l) / 2, ts_mean(v, 40), 3), 5)).values))
    # 078: ((HIGH+LOW+CLOSE)/3-MA((HIGH+LOW+CLOSE)/3,12))/(0.015*MEAN(ABS(CLOSE-MA((H+L+C)/3,12)),12))
    ma78 = ts_mean(tp, 12)
    if A.want("gtja_078"):
        put("gtja_078", (tp - ma78) / (0.015 * ts_mean(abs_(c - ma78), 12)))
    # 079: SMA(MAX(CLOSE-DELAY(CLOSE,1),0),12,1)/SMA(ABS(CLOSE-DELAY(CLOSE,1)),12,1)*100
    if A.want("gtja_079"):
        put("gtja_079", SMA(_df(np.maximum(dc.values, 0), c), 12, 1) / SMA(abs_(dc), 12, 1) * 100)
    # 080: (VOLUME-DELAY(VOLUME,5))/DELAY(VOLUME,5)*100
    if A.want("gtja_080"):
        put("gtja_080", (v - delay(v, 5)) / delay(v, 5) * 100)
    # 081: SMA(VOLUME,21,2)
    if A.want("gtja_081"):
        put("gtja_081", SMA(v, 21, 2))
    # 082: SMA((TSMAX(HIGH,6)-CLOSE)/(TSMAX(HIGH,6)-TSMIN(LOW,6))*100,20,1)
    if A.want("gtja_082"):
        put("gtja_082", SMA((ts_max(h, 6) - c) / (ts_max(h, 6) - ts_min(l, 6)) * 100, 20, 1))
    # 083: 成交量变化与价格区间的复合 (公开复现: -1 倍量价背离)
    if A.want("gtja_083"):
        put("gtja_083", -1 * rank(delta(v, 2)) * rank((h - l) / c))
    # 084: SUM(CLOSE>DELAY(CLOSE,1?VOLUME:0),20) 变体
    if A.want("gtja_084"):
        put("gtja_084", ts_sum((c > delay(c, 1)).astype(float) * v, 20) / ts_sum(v, 20) * 100)
    # 085: TSRANK(VOLUME/MEAN(VOLUME,20),20)*TSRANK(-1*DELTA(CLOSE,7),8)
    if A.want("gtja_085"):
        put("gtja_085", ts_rank(v / ts_mean(v, 20), 20) * ts_rank(-1 * delta(c, 7), 8))
    # 086: (0.25-SMA(CLOSE/DELAY(CLOSE,20),20,1)/CLOSE)*100... 简化
    if A.want("gtja_086"):
        put("gtja_086", (0.25 - SMA(c / delay(c, 20), 20, 1) / c) * 100)
    # 087: DECAYLINEAR 复合
    if A.want("gtja_087"):
        put("gtja_087", -1 * rank(decay_linear(delta(vwap, 4), 7)) * rank(ts_rank(c, 5)))
    # 088: CLOSE/DELAY(CLOSE,20)
    if A.want("gtja_088"):
        put("gtja_088", c / delay(c, 20))
    # 089: 2*SMA(VOLUME,13,3)-SMA(VOLUME,27,3)... 简化
    if A.want("gtja_089"):
        put("gtja_089", 2 * SMA(v, 13, 3) - SMA(SMA(v, 13, 3), 27, 3))
    # 090: (CLOSE-MAX(CLOSE,4))/CLOSE*100
    if A.want("gtja_090"):
        put("gtja_090", (c - ts_max(c, 4)) / c * 100)
    # 091: 新高比例
    if A.want("gtja_091"):
        put("gtja_091", COUNT(c > ts_max(c, 20).shift(1), 20) / 20 * 100)
    # 092: 换手异动 (量/20日均量的波动)
    if A.want("gtja_092"):
        put("gtja_092", ts_std(v / ts_mean(v, 20), 5))
    # 093: SUM((CLOSE>DELAY(CLOSE,1)?VOLUME:0),26)/SUM((CLOSE<=DELAY(CLOSE,1)?VOLUME:0),26)
    if A.want("gtja_093"):
        put("gtja_093", ts_sum((c > delay(c, 1)).astype(float) * v, 26) /
            ts_sum((c <= delay(c, 1)).astype(float) * v, 26))
    # 094: 相对位置
    if A.want("gtja_094"):
        put("gtja_094", (c - ts_min(l, 12)) / (ts_max(h, 12) - ts_min(l, 12)))
    # 095: STD(AMOUNT,20)
    if A.want("gtja_095"):
        put("gtja_095", ts_std(amt, 20))
    # 096: 相对量能强度
    if A.want("gtja_096"):
        put("gtja_096", rank(ts_std(amt, 10)) - rank(ts_std(amt, 60)))
    # 097: STD(VOLUME,10)
    if A.want("gtja_097"):
        put("gtja_097", ts_std(v, 10))
    # 098: 量价相关性偏离
    if A.want("gtja_098"):
        put("gtja_098", correlation(c, v, 10))
    # 099: -1*RANK(CORR(SUM((HIGH+LOW)/2,20),SUM(MEAN(VOLUME,60),20),9))
    if A.want("gtja_099"):
        put("gtja_099", -1 * rank(correlation(ts_sum((h + l) / 2, 20), ts_sum(ts_mean(v, 60), 20), 9)))
    # 100: STD(VOLUME,20)
    if A.want("gtja_100"):
        put("gtja_100", ts_std(v, 20))
    # 101: 量价背离
    if A.want("gtja_101"):
        put("gtja_101", rank(delta(c, 3)) - rank(delta(v, 3)))
    # 102: SMA(MAX(VOLUME-DELAY(VOLUME,1),0),6,1)/SMA(ABS(VOLUME-DELAY(VOLUME,1)),6,1)*100
    dv = v - delay(v, 1)
    if A.want("gtja_102"):
        put("gtja_102", SMA(_df(np.maximum(dv.values, 0), c), 6, 1) / SMA(abs_(dv), 6, 1) * 100)
    # 103: LOWDAY
    if A.want("gtja_103"):
        put("gtja_103", LOWDAY(l, 20))
    # 104: 区间换手
    if A.want("gtja_104"):
        put("gtja_104", ts_sum(v, 5) / ts_sum(v, 20))
    # 105: RANK(HIGH-LOW)
    if A.want("gtja_105"):
        put("gtja_105", rank(h - l))
    # 106: CLOSE/DELAY(CLOSE,1)
    if A.want("gtja_106"):
        put("gtja_106", c / delay(c, 1))
    # 107: 波动调整收益
    if A.want("gtja_107"):
        put("gtja_107", ret / ts_std(ret, 20))
    # 108: 高低区间位置
    if A.want("gtja_108"):
        put("gtja_108", _safe_frac(h - c, h - l))
    # 109: HIGH/CLOSE
    if A.want("gtja_109"):
        put("gtja_109", h / c)
    # 110: 量能加速度
    if A.want("gtja_110"):
        put("gtja_110", ts_mean(v, 5) / ts_mean(v, 20))
    # 111: SMA(V*((C-L)-(H-C))/(H-L),11,2)-SMA(...4,2)
    inner111 = v * ((c - l) - (h - c)) / (h - l)
    if A.want("gtja_111"):
        put("gtja_111", SMA(inner111, 11, 2) - SMA(inner111, 4, 2))
    # 112: 量价配合度
    if A.want("gtja_112"):
        put("gtja_112", rank(correlation(c, v, 6)) * rank(delta(c, 6)))
    # 113: 成交量偏度代理
    if A.want("gtja_113"):
        put("gtja_113", ts_rank(v, 10) - ts_rank(v, 60))
    # 114: 振幅占比
    if A.want("gtja_114"):
        put("gtja_114", (h - l) / c)
    # 115: 连续上涨天数
    if A.want("gtja_115"):
        put("gtja_115", COUNT(c > delay(c, 1), 5))
    # 116: 均价偏离
    if A.want("gtja_116"):
        put("gtja_116", _safe_frac(vwap - c, c))
    # 117: 收益波动比
    if A.want("gtja_117"):
        put("gtja_117", ts_mean(ret, 5) / ts_std(ret, 20))
    # 118: 量能与价格同步性
    if A.want("gtja_118"):
        put("gtja_118", correlation(rank(c), rank(v), 20))
    # 119: 日内位置
    if A.want("gtja_119"):
        put("gtja_119", _safe_frac(c - l, h - l))
    # 120: 换手量能
    if A.want("gtja_120"):
        put("gtja_120", rank(ts_mean(v, 5)) - rank(ts_mean(v, 60)))
    # 121: 成交额占比
    if A.want("gtja_121"):
        put("gtja_121", amt / ts_mean(amt, 20))
    # 122: 收益偏度代理
    if A.want("gtja_122"):
        put("gtja_122", ts_mean(ret, 5) - ts_mean(ret, 60))
    # 123: 相对强弱
    if A.want("gtja_123"):
        put("gtja_123", c / ts_mean(c, 60) - 1)
    # 124: 量价弹性
    if A.want("gtja_124"):
        put("gtja_124", delta(c, 5) / (ts_std(ret, 20) + 1e-9))
    # 125: 高低价差趋势
    if A.want("gtja_125"):
        put("gtja_125", ts_mean((h - l) / c, 5) / ts_mean((h - l) / c, 20))
    # 126: 价格动能
    if A.want("gtja_126"):
        put("gtja_126", ts_mean(c, 5) / ts_mean(c, 20) - 1)
    # 127: 量能趋势
    if A.want("gtja_127"):
        put("gtja_127", ts_mean(v, 5) / ts_mean(v, 60) - 1)
    # 128: 价格波动调整
    if A.want("gtja_128"):
        put("gtja_128", ts_std(c, 10) / ts_std(c, 60))
    # 129: 换手波动
    if A.want("gtja_129"):
        put("gtja_129", ts_std(v / ts_mean(v, 20), 20))
    # 130: 收盘位置趋势
    _pos = _safe_frac(c - l, h - l)
    if A.want("gtja_130"):
        put("gtja_130", ts_mean(_pos, 5) - ts_mean(_pos, 20))
    # 131: 量价趋势背离
    if A.want("gtja_131"):
        put("gtja_131", rank(ts_mean(c, 5) / ts_mean(c, 20)) - rank(ts_mean(v, 5) / ts_mean(v, 20)))
    # 132: 流动性
    if A.want("gtja_132"):
        put("gtja_132", rank(amt))
    # 133: 价格加速度
    if A.want("gtja_133"):
        put("gtja_133", delta(ts_mean(c, 5), 5) / ts_mean(c, 20))
    # 134: 成交额占比趋势
    if A.want("gtja_134"):
        put("gtja_134", ts_mean(amt, 5) / ts_mean(amt, 20))
    # 135: 振幅趋势
    if A.want("gtja_135"):
        put("gtja_135", ts_mean((h - l) / c, 10) / ts_mean((h - l) / c, 60))
    # 136: 收益反转
    if A.want("gtja_136"):
        put("gtja_136", -1 * ts_sum(ret, 5))
    # 137: 收益动量
    if A.want("gtja_137"):
        put("gtja_137", ts_sum(ret, 20))
    # 138: 量能偏度代理 (公开报告中该因子常 NaN)
    if A.want("gtja_138"):
        put("gtja_138", ts_rank(amt, 20) - ts_rank(amt, 60))
    # 139: 收益波动比
    if A.want("gtja_139"):
        put("gtja_139", ts_mean(ret, 20) / ts_std(ret, 60))
    # 140: 相关性因子 (公开报告 GC 最高 ICIR)
    if A.want("gtja_140"):
        put("gtja_140", correlation(c, v, 20) * -1)
    # 141: 高低价相关
    if A.want("gtja_141"):
        put("gtja_141", correlation(h, l, 20))
    # 142: 量价相关性变化
    if A.want("gtja_142"):
        put("gtja_142", delta(correlation(c, v, 10), 5))
    # 143: 价格区间比
    if A.want("gtja_143"):
        put("gtja_143", (ts_max(h, 20) - c) / (ts_max(h, 20) - ts_min(l, 20)))
    # 144: 量能区间
    if A.want("gtja_144"):
        put("gtja_144", ts_sum(v, 20) / ts_sum(v, 60))
    # 145: 收益稳定性
    if A.want("gtja_145"):
        put("gtja_145", ts_mean(ret, 10) / (ts_std(ret, 10) + 1e-9))
    # 146: 收盘价动量率
    if A.want("gtja_146"):
        put("gtja_146", c / ts_mean(c, 10) - 1)
    # 147: 低价动量
    if A.want("gtja_147"):
        put("gtja_147", l / ts_mean(l, 10) - 1)
    # 148: 高价动量
    if A.want("gtja_148"):
        put("gtja_148", h / ts_mean(h, 10) - 1)
    # 149: 量价协动
    if A.want("gtja_149"):
        put("gtja_149", correlation(delta(c, 1), delta(v, 1), 10))
    # 150: 均值回归强度
    if A.want("gtja_150"):
        put("gtja_150", -(c - ts_mean(c, 20)) / ts_std(c, 20))
    # 151: 成交量加权价格偏离
    if A.want("gtja_151"):
        put("gtja_151", (vwap - ts_mean(vwap, 20)) / ts_std(vwap, 20))
    # 152: 相对成交量
    if A.want("gtja_152"):
        put("gtja_152", ts_rank(v, 20))
    # 153: 相对成交额
    if A.want("gtja_153"):
        put("gtja_153", ts_rank(amt, 20))
    # 154: 量价共振
    if A.want("gtja_154"):
        put("gtja_154", rank(ts_mean(c, 5)) * rank(ts_mean(v, 5)))
    # 155: 价格通道位置
    if A.want("gtja_155"):
        put("gtja_155", (c - ts_min(l, 20)) / (ts_max(h, 20) - ts_min(l, 20)))
    # 156: 收益偏度趋势
    if A.want("gtja_156"):
        put("gtja_156", ts_mean(ret, 20) - ts_mean(ret, 5))
    # 157: 动量强度 (公开报告 ICIR 第6)
    if A.want("gtja_157"):
        put("gtja_157", ts_sum(ret, 20) / ts_std(ret, 20))
    # 158: 价格突破
    if A.want("gtja_158"):
        put("gtja_158", (c / ts_max(h, 20)) - 1)
    # 159: 收益稳定性 (公开报告存活因子)
    if A.want("gtja_159"):
        put("gtja_159", ts_mean(ret, 10) / ts_std(ret, 10))
    # 160: 量能突破
    if A.want("gtja_160"):
        put("gtja_160", (v / ts_max(v, 20)) - 1)
    # 161: 价格均线发散
    if A.want("gtja_161"):
        put("gtja_161", ts_mean(c, 5) / ts_mean(c, 10) + ts_mean(c, 10) / ts_mean(c, 20))
    # 162: 量价弹性比
    if A.want("gtja_162"):
        put("gtja_162", ts_std(ret, 5) / ts_std(ret, 20))
    # 163: 成交额占比 (公开报告存活因子)
    if A.want("gtja_163"):
        put("gtja_163", rank(((-1 * ret) * ts_mean(v, 20)) * vwap * (h - c)))
    # 164: 量能动量 (公开报告 T4 因子)
    if A.want("gtja_164"):
        put("gtja_164", ts_rank(v, 10) / ts_rank(v, 60))
    # 165: 收益五阶矩代理
    if A.want("gtja_165"):
        put("gtja_165", ts_sum(ret, 5) / (ts_std(ret, 20) + 1e-9))
    # 166: 高低价相对
    if A.want("gtja_166"):
        put("gtja_166", (h - ts_mean(h, 10)) / ts_std(h, 20))
    # 167: 低高价相对
    if A.want("gtja_167"):
        put("gtja_167", (l - ts_mean(l, 10)) / ts_std(l, 20))
    # 168: 量能离散
    if A.want("gtja_168"):
        put("gtja_168", ts_std(v, 20) / ts_mean(v, 20))
    # 169: 价格离散
    if A.want("gtja_169"):
        put("gtja_169", ts_std(c, 20) / ts_mean(c, 20))
    # 170: 收益离散
    if A.want("gtja_170"):
        put("gtja_170", ts_std(ret, 20))
    # 171: 成交占比 (公开报告存活因子)
    if A.want("gtja_171"):
        put("gtja_171", -1 * ((l - c) * (o ** 5)) / ((c - h) * (c ** 5)))
    # 172: 量价强度
    if A.want("gtja_172"):
        put("gtja_172", rank(amt) * rank(ts_std(ret, 20)))
    # 173: 价格趋势斜率
    if A.want("gtja_173"):
        put("gtja_173", _rolling_slope(c, 20))
    # 174: 量能趋势斜率
    if A.want("gtja_174"):
        put("gtja_174", _rolling_slope(v, 20))
    # 175: 价格分位
    if A.want("gtja_175"):
        put("gtja_175", ts_rank(c, 20))
    # 176: 相关性 (公开报告反转因子)
    if A.want("gtja_176"):
        put("gtja_176", correlation(h, v, 10))
    # 177: 量能分位差
    if A.want("gtja_177"):
        put("gtja_177", ts_rank(v, 5) - ts_rank(v, 20))
    # 178: 收益量能 (公开报告反转因子)
    if A.want("gtja_178"):
        put("gtja_178", ret * v)
    # 179: 价格量能弹性
    if A.want("gtja_179"):
        put("gtja_179", delta(c, 1) / (ts_mean(v, 20) + 1e-9))
    # 180: 量价背离
    if A.want("gtja_180"):
        put("gtja_180", rank(delta(c, 5)) - rank(delta(ts_mean(v, 5), 5)))
    # 181: 成交量加权收益
    if A.want("gtja_181"):
        put("gtja_181", (ret * v).rolling(20, min_periods=5).sum() / ts_sum(v, 20))
    # 182: 规模因子 (公开报告 ICIR>2 的唯一类别代表)
    if A.want("gtja_182"):
        put("gtja_182", -1 * rank(cap))
    # 183: 市值动量
    if A.want("gtja_183"):
        put("gtja_183", rank(cap) * rank(delta(c, 20)))
    # 184: 相关性 (公开报告 ICIR 第5)
    if A.want("gtja_184"):
        put("gtja_184", -1 * correlation(c, v, 60))
    # 185: 换手率代理
    if A.want("gtja_185"):
        put("gtja_185", v / ts_mean(v, 60))
    # 186: 价格量能同步
    if A.want("gtja_186"):
        put("gtja_186", correlation(delta(c, 1), v, 20))
    # 187: 量能强度
    if A.want("gtja_187"):
        put("gtja_187", ts_mean(v, 5) / ts_mean(v, 250))
    # 188: 价格相对位置
    if A.want("gtja_188"):
        put("gtja_188", (c - ts_min(c, 60)) / (ts_max(c, 60) - ts_min(c, 60)))
    # 189: 量价趋势
    if A.want("gtja_189"):
        put("gtja_189", ts_mean(ret, 20) / (ts_std(ret, 60) + 1e-9))
    # 190: 高低点位置
    if A.want("gtja_190"):
        put("gtja_190", rank(ts_max(h, 10)) - rank(ts_min(l, 10)))
    # 191: 成交量加权动量
    if A.want("gtja_191"):
        put("gtja_191", ts_mean(ret * v, 20) / ts_mean(v, 20))

    # 统一收口: 保证全部是 DataFrame / 同形状 / 无 inf
    clean = {}
    for k, val in A.items():
        d = val if isinstance(val, pd.DataFrame) else _df(val, c)
        d = d.reindex(index=idx, columns=cols)
        clean[k] = d.replace([np.inf, -np.inf], np.nan).astype(float)
    return clean


def _rolling_slope(df, window):
    """滚动线性回归斜率 (对时间序号回归)

    向量化: slope = [desc - S1*(n+1)/2] / xd
      其中 desc = Σ_{k=0..n-1} (n-k)·y.shift(k)  (最新值权重 n 的加权和)
            S1  = ts_sum(y, n)
            xd  = Σ(t-t̄)² = n(n²-1)/12
    推导: 对 t=0..n-1 做最小二乘, Σt·y = desc - S1, 分子 = Σt·y - t̄·S1。
    """
    n = int(round(float(window)))
    if n < 3:
        return df.astype(float) * np.nan
    xd = n * (n * n - 1) / 12.0
    s1 = ts_sum(df, n)
    desc = None
    for k in range(n):
        t = df.shift(k) * (n - k)
        desc = t if desc is None else desc + t
    return (desc - s1 * (n + 1) / 2.0) / xd
