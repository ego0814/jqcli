# -*- coding: utf-8 -*-
"""
Alpha101 因子库 (WorldQuant / Kakushadze 2016)
=============================================
公式来源: arXiv:1601.00991 附录
输入约定:
  open/high/low/close/vwap/returns 均为 宽表 (index=date, columns=code)
  vwap 若缺失, 用 (high+low+close)/3 近似 (与原论文注释一致)
  cap = 总市值
  ind  = 行业分类 (用于 IndNeutralize, 本实现用行业哑变量回归残差)

⚠️ 关于 volume 的口径 (易错点):
  原论文的 volume 指**成交额(美元)**, adv{d} = 过去 d 日 volume 的均值。
  本项目数据里 volume 是**股数**、amount 是**成交额(元)**, 所以:
    - adv{d} = ts_mean(amount, d)                <- 成交额口径
    - 原式中的 volume 一律用 D["volume_dollar"]  <- 与 adv 同口径
  若 adv 用成交额、而 volume 用股数, 量级会差 ~3 个数量级,
  任何 adv 与 volume 的比较(如 alpha007 的 adv20 < v)都会退化为常量。"""
import numpy as np
import pandas as pd

from ops import (abs_, correlation, covariance, decay_linear, delay, delta,
                 greater_than, less_than, log, rank, scale, sign, signed_power,
                 ts_argmax, ts_argmin, ts_max, ts_mean, ts_min, ts_product,
                 ts_rank, ts_std, ts_sum)


def build_adv(amount, d):
    """adv{d}: 过去 d 日平均成交额 (与论文的 dollar volume 同口径)"""
    return ts_mean(amount, d)


class _StopFactors(Exception):
    """分段运行: 算满指定数量后中止 (用于把长任务切成多个短进程)"""
    pass


class _StreamDict(dict):
    """因子容器: 支持流式回调 / 按需计算 (want) / 分段中止 (stop_after)

    want(k) 为 False 时该因子被整段跳过, **完全不参与计算** ——
    这是断点续传能真正省时间的关键 (仅靠回调跳过无法省下计算量)。
    """

    def __init__(self, cb=None, want=None, stop_after=None):
        super().__init__()
        self._cb = cb
        self._want = want
        self._stop_after = stop_after
        self._n = 0

    def want(self, k):
        return (self._want is None) or (str(k) in self._want)

    def __setitem__(self, k, v):
        if not self.want(k):
            return
        self._n += 1
        if self._cb is not None:
            self._cb(k, v)
        else:
            super().__setitem__(k, v)
        if self._stop_after and self._n >= self._stop_after:
            raise _StopFactors()


def industry_neutralize(x, ind_dummies):
    """对行业哑变量做横截面回归取残差 (IndNeutralize)"""
    # ind_dummies: DataFrame 与 x 同结构, 值为行业代码 -> 用 group demean 近似
    out = x.copy()
    return out.sub(out.groupby(ind_dummies.iloc[0], axis=1).transform("mean"), axis=0)


def make_alphas(D, stream_cb=None, want=None, stop_after=None):
    """
    D: dict, 包含 open, high, low, close, volume, vwap, returns, amount, cap
    返回: dict {alpha_id: DataFrame}
    """
    o, h, l, c = D["open"], D["high"], D["low"], D["close"]
    # 论文的 volume 是成交额(美元); 这里统一用成交额, 与 adv{d} 同口径
    v, vwap, ret = D.get("volume_dollar", D["volume"]), D["vwap"], D["returns"]
    amt, cap = D["amount"], D["cap"]

    adv5, adv10 = build_adv(amt, 5), build_adv(amt, 10)
    adv15, adv20 = build_adv(amt, 15), build_adv(amt, 20)
    adv30, adv40 = build_adv(amt, 30), build_adv(amt, 40)
    adv50, adv60 = build_adv(amt, 50), build_adv(amt, 60)
    adv81, adv120 = build_adv(amt, 81), build_adv(amt, 120)
    adv150, adv180 = build_adv(amt, 150), build_adv(amt, 180)

    A = _StreamDict(stream_cb, want=want, stop_after=stop_after)

    # --- 001-020 ---
    if A.want("alpha001"):
        A["alpha001"] = (-1 * correlation(rank(ts_argmax(signed_power(
            ts_std(ret, 20).where(ret < 0, c), 2), 5)), rank(c), 5))
    if A.want("alpha002"):
        A["alpha002"] = (-1 * correlation(rank(delta(log(v), 2)), rank((c - o) / o), 6))
    if A.want("alpha003"):
        A["alpha003"] = (-1 * correlation(rank(o), rank(v), 10))
    if A.want("alpha004"):
        A["alpha004"] = (-1 * ts_rank(rank(l), 9))
    if A.want("alpha005"):
        A["alpha005"] = (rank(o - ts_sum(vwap, 10) / 10) * (-1 * abs_(rank(c - vwap))))
    if A.want("alpha006"):
        A["alpha006"] = (-1 * correlation(o, v, 10))
    if A.want("alpha007"):
        A["alpha007"] = ((adv20 < v).astype(float) * (-1 * ts_rank(abs_(delta(c, 7)), 60)) * sign(delta(c, 7)))
    if A.want("alpha008"):
        A["alpha008"] = (-1 * rank(((ts_sum(o, 5) * ts_sum(ret, 5)) - delay(ts_sum(o, 5) * ts_sum(ret, 5), 10))))
    if A.want("alpha009"):
        A["alpha009"] = pd.DataFrame(
            np.where((0 < ts_min(delta(c, 1), 5)).values, delta(c, 1).values,
                     np.where((ts_max(delta(c, 1), 5) < 0).values, delta(c, 1).values,
                              (-1 * delta(c, 1)).values)),
            index=c.index, columns=c.columns)
    if A.want("alpha010"):
        A["alpha010"] = pd.DataFrame(
            np.where((0 < ts_min(delta(c, 1), 4)).values, delta(c, 1).values,
                     np.where((ts_max(delta(c, 1), 4) < 0).values, delta(c, 1).values,
                              (-1 * delta(c, 1)).values)),
            index=c.index, columns=c.columns)
    if A.want("alpha011"):
        A["alpha011"] = ((rank(ts_max(vwap - c, 3)) + rank(ts_min(vwap - c, 3))) * rank(delta(v, 3)))
    if A.want("alpha012"):
        A["alpha012"] = (sign(delta(v, 1)) * (-1 * delta(c, 1)))
    if A.want("alpha013"):
        A["alpha013"] = (-1 * rank(covariance(rank(c), rank(v), 5)))
    if A.want("alpha014"):
        A["alpha014"] = ((-1 * rank(delta(ret, 3))) * correlation(o, v, 10))
    if A.want("alpha015"):
        A["alpha015"] = (-1 * ts_sum(rank(correlation(rank(h), rank(v), 3)), 3))
    if A.want("alpha016"):
        A["alpha016"] = (-1 * rank(covariance(rank(h), rank(v), 5)))
    if A.want("alpha017"):
        A["alpha017"] = (((-1 * rank(ts_rank(c, 10))) * rank(delta(delta(c, 1), 1))) *
                         rank(ts_rank(v / adv20, 5)))
    if A.want("alpha018"):
        A["alpha018"] = (-1 * rank(((ts_std(abs_(c - o), 5) + (c - o)) + correlation(c, o, 10))))
    if A.want("alpha019"):
        A["alpha019"] = ((-1 * sign(((c - delay(c, 7)) + delta(c, 7)))) *
                         (1 + rank((1 + ts_sum(ret, 250)))))
    if A.want("alpha020"):
        A["alpha020"] = (((-1 * rank(o - delay(h, 1))) * rank(o - delay(c, 1))) *
                         rank(o - delay(l, 1)))

    # --- 021-040 ---
    if A.want("alpha021"):
        A["alpha021"] = pd.DataFrame(
            np.where((ts_mean(c, 8) + ts_std(c, 8)).lt(c).values, -1.0,
                     np.where(ts_mean(c, 2).lt(ts_mean(c, 8)).values, 1.0, -1.0)),
            index=c.index, columns=c.columns)
    if A.want("alpha022"):
        A["alpha022"] = (-1 * (delta(correlation(h, v, 5), 5) * correlation(o, c, 5)))
    if A.want("alpha023"):
        A["alpha023"] = pd.DataFrame(
            np.where(ts_mean(h, 20).lt(h).values, (-1 * delta(h, 2)).values, 0.0),
            index=c.index, columns=c.columns)
    if A.want("alpha024"):
        A["alpha024"] = pd.DataFrame(
            np.where((delta(ts_mean(c, 100), 100) / delay(c, 100)).le(0.05).values,
                     (-1 * (c - ts_min(c, 100))).values, (-1 * delta(c, 3)).values),
            index=c.index, columns=c.columns)
    if A.want("alpha025"):
        A["alpha025"] = rank((((-1 * ret) * adv20) * vwap) * (h - c))
    if A.want("alpha026"):
        A["alpha026"] = (-1 * ts_max(correlation(ts_rank(v, 5), ts_rank(h, 5), 5), 3))
    if A.want("alpha027"):
        A["alpha027"] = pd.DataFrame(
            np.where(rank(ts_mean(correlation(rank(v), rank(vwap), 6), 2)).lt(0.5).values, -1.0, 1.0),
            index=c.index, columns=c.columns)
    if A.want("alpha028"):
        A["alpha028"] = (scale(((correlation(adv20, l, 5) + ((h + l) / 2)) - c)))
    if A.want("alpha029"):
        A["alpha029"] = (ts_min(ts_product(rank(rank(scale(log(ts_sum(
            ts_min(rank(rank(-1 * rank(delta(c, 5)))), 2), 1))))), 1), 5) +
                         ts_rank(delay((-1 * rank(delta(c, 5))), 6), 5))
    if A.want("alpha030"):
        A["alpha030"] = (((1.0 - rank(((sign((c - delay(c, 1))) + sign((delay(c, 1) - delay(c, 2)))) +
                                        sign((delay(c, 2) - delay(c, 3)))))) * ts_sum(v, 5)) / ts_sum(v, 20))
    if A.want("alpha031"):
        A["alpha031"] = ((rank(rank(rank(decay_linear(-1 * rank(rank(delta(c, 10))), 10)))) +
                          rank(-1 * delta(c, 3))) + sign(scale(correlation(adv20, l, 12))))
    if A.want("alpha032"):
        A["alpha032"] = (scale(((ts_sum(c, 7) / 7) - c)) + (20 * scale(correlation(vwap, delay(c, 5), 230))))
    if A.want("alpha033"):
        A["alpha033"] = rank((-1 * ((1 - (o / c)) ** 1)))
    if A.want("alpha034"):
        A["alpha034"] = rank(((1 - rank((ts_std(ret, 2) / ts_std(ret, 5)))) +
                              (1 - rank(delta(c, 1)))))
    if A.want("alpha035"):
        A["alpha035"] = ((ts_rank(v, 32) * (1 - ts_rank((c + h) - l, 16))) *
                         (1 - ts_rank(ret, 32)))
    if A.want("alpha036"):
        A["alpha036"] = (((((2.21 * rank(correlation((c - o), delay(v, 1), 15))) +
                            (0.7 * rank((o - c)))) + (0.73 * rank(ts_rank(delay(-1 * ret, 6), 5)))) +
                          rank(abs_(correlation(vwap, adv20, 6)))) +
                         (0.6 * rank((((ts_mean(c, 200) / 200) - o) * (c - o)))))
    if A.want("alpha037"):
        A["alpha037"] = (rank(correlation(delay((o - c), 1), c, 200)) + rank((o - c)))
    if A.want("alpha038"):
        A["alpha038"] = ((-1 * rank(ts_rank(c, 10))) * rank((c / o)))
    if A.want("alpha039"):
        A["alpha039"] = ((-1 * rank((delta(c, 7) * (1 - rank(decay_linear((v / adv20), 9)))))) *
                         (1 + rank(ts_sum(ret, 250))))
    if A.want("alpha040"):
        A["alpha040"] = ((-1 * rank(ts_std(h, 10))) * correlation(h, v, 10))

    # --- 041-060 ---
    if A.want("alpha041"):
        A["alpha041"] = (((h * l) ** 0.5) - vwap)
    if A.want("alpha042"):
        A["alpha042"] = (rank((vwap - c)) / rank((vwap + c)))
    if A.want("alpha043"):
        A["alpha043"] = (ts_rank((v / adv20), 20) * ts_rank((-1 * delta(c, 7)), 8))
    if A.want("alpha044"):
        A["alpha044"] = (-1 * correlation(h, rank(v), 5))
    if A.want("alpha045"):
        A["alpha045"] = (-1 * ((rank((ts_mean(delay(c, 5), 20)) * correlation(c, v, 2)) *
                                rank(correlation(ts_sum(c, 5), ts_sum(c, 20), 2)))))
    if A.want("alpha046"):
        A["alpha046"] = pd.DataFrame(
            np.where(
                (((delay(c, 20) - delay(c, 10)) / 10) - ((delay(c, 10) - c) / 10)).gt(0).values, -1.0,
                np.where((((delay(c, 20) - delay(c, 10)) / 10) - ((delay(c, 10) - c) / 10)).eq(0).values,
                         1.0, (-1 * (c - delay(c, 1))).values)),
            index=c.index, columns=c.columns)
    if A.want("alpha047"):
        A["alpha047"] = ((((rank((1 / c)) * v) / adv20) * ((h * rank((h - c)) / (ts_mean(h, 5) / 5)) -
                                                            rank((vwap - c)))))
    if A.want("alpha048"):
        A["alpha048"] = (-1 * ((rank((correlation(delta(c, 1), delta(delay(c, 1), 1), 250)) *
                                      delta(c, 1) / c)) /
                               (ts_sum((delta(c, 1) / delay(c, 1)) ** 2, 250)) ** 0.5))
    if A.want("alpha049"):
        A["alpha049"] = pd.DataFrame(
            np.where(((((delay(c, 20) - delay(c, 10)) / 10) - ((delay(c, 10) - c) / 10)) < -0.1).values,
                     -1.0, (-1 * (c - delay(c, 1))).values),
            index=c.index, columns=c.columns)
    if A.want("alpha050"):
        A["alpha050"] = (-1 * ts_max(rank(correlation(rank(v), rank(vwap), 5)), 5))
    if A.want("alpha051"):
        A["alpha051"] = pd.DataFrame(
            np.where(((((delay(c, 20) - delay(c, 10)) / 10) - ((delay(c, 10) - c) / 10)) < -0.05).values,
                     -1.0, (-1 * (c - delay(c, 1))).values),
            index=c.index, columns=c.columns)
    if A.want("alpha052"):
        A["alpha052"] = ((((-1 * ts_min(l, 5)) + delay(ts_min(l, 5), 5)) *
                          rank(((ts_sum(ret, 240) - ts_sum(ret, 20)) / 220))) * ts_rank(v, 5))
    if A.want("alpha053"):
        A["alpha053"] = (-1 * delta((((c - l) - (h - c)) / (c - l)), 9))
    if A.want("alpha054"):
        A["alpha054"] = (-1 * ((l - c) * (o ** 5)) / ((l - h) * (c ** 5)))
    if A.want("alpha055"):
        A["alpha055"] = (-1 * correlation(rank(((c - ts_min(l, 12)) /
                                                (ts_max(h, 12) - ts_min(l, 12)))), rank(v), 6))
    if A.want("alpha056"):
        A["alpha056"] = (0 - (1 * (rank((ts_sum(ret, 10) / ts_sum(ts_sum(ret, 2), 3))) *
                                   rank((ret / vwap)))))
    if A.want("alpha057"):
        A["alpha057"] = (0 - (1 * ((c - vwap) / decay_linear(rank(ts_argmax(c, 30)), 2))))
    # 注: 原式 alpha058 第一项为 correlation(0.1, v, d)*0, 恒等于 0, 直接省略
    if A.want("alpha058"):
        A["alpha058"] = (-1 * ts_rank(decay_linear(correlation(vwap, v, 4), 8), 6))
    if A.want("alpha059"):
        A["alpha059"] = (0 - (1 * ((2 * scale(rank(((((vwap * 0.728317) + (vwap * (1 - 0.728317))) - vwap)))) -
                                    scale(rank(abs_(correlation(vwap, adv50, 4))))))))
    if A.want("alpha060"):
        A["alpha060"] = (0 - (1 * ((2 * scale(rank(((((vwap * 0.716) + (vwap * (1 - 0.716))) -
                                                      vwap)))) -
                                    scale(rank(abs_(correlation(vwap, adv150, 9))))))))

    # --- 061-080 ---
    if A.want("alpha061"):
        A["alpha061"] = (rank((vwap - ts_min(vwap, 16.1219))) < rank(correlation(vwap, adv180, 17.9282))).astype(float)
    if A.want("alpha062"):
        A["alpha062"] = ((rank(correlation(vwap, ts_sum(adv20, 22.4101), 9.91009)) <
                          rank(((rank(o) + rank(o)) < (rank((h + l) / 2) + rank(h)))))
                         .astype(float) * -1)
    if A.want("alpha063"):
        A["alpha063"] = ((rank(decay_linear(delta(correlation(
            ((o * 0.318108) + (c * (1 - 0.318108))), ts_sum(adv180, 37.7057), 13.657), 3.18132), 6)) -
            rank(decay_linear(rank(decay_linear(scale(signed_power(
                ((h - l) / (ts_sum(c, 5) / 5)), 1.0)), 3)), 6))) * -1)
    if A.want("alpha064"):
        A["alpha064"] = ((rank(correlation(ts_sum(((o * 0.178404) + (l * (1 - 0.178404))), 12.7054),
                                            ts_sum(adv120, 12.7054), 16.6208)) <
                          rank(delta(((((h + l) / 2) * 0.178404) + (vwap * (1 - 0.178404))), 3.69741)))
                         .astype(float) * -1)
    if A.want("alpha065"):
        A["alpha065"] = ((rank(correlation(((o * 0.00817205) + (vwap * (1 - 0.00817205))),
                                           ts_sum(adv60, 8.6911), 6.40374)) <
                          rank((o - ts_min(o, 13.635)))).astype(float) * -1)
    if A.want("alpha066"):
        A["alpha066"] = (((rank(decay_linear(delta(vwap, 3.51013), 7.23052)) +
                           ts_rank(decay_linear(((((l * 0.96633) + (l * (1 - 0.96633))) - vwap) /
                                                 (o - ((h + l) / 2))), 11.4157), 6.72611)) * -1))
    if A.want("alpha067"):
        A["alpha067"] = ((rank((h - ts_min(h, 2.14593))) **
                          rank(correlation(ts_rank(vwap, 2.6), ts_rank(adv15, 4), 3))) * -1)
    if A.want("alpha068"):
        A["alpha068"] = ((ts_rank(correlation(rank(h), rank(adv15), 8.91644), 13.9333) <
                          rank(delta((c * 0.518371) + (c * (1 - 0.518371)), 1.06157))).astype(float) * -1)
    if A.want("alpha069"):
        A["alpha069"] = ((rank(ts_max(delta(vwap, 2.72412), 4.79344)) **
                          ts_rank(correlation(((c * 0.490655) + (vwap * (1 - 0.490655))), adv20, 4.92416), 9.0615)) * -1)
    if A.want("alpha070"):
        A["alpha070"] = ((rank(delta(vwap, 1.29456)) **
                          ts_rank(correlation(c, adv50, 17.8256), 17.9171)) * -1)
    if A.want("alpha071"):
        A["alpha071"] = np.maximum(
            ts_rank(decay_linear(correlation(ts_rank(c, 3.43976), ts_rank(adv180, 12.0647), 18.0175), 4.20501), 15.6948),
            ts_rank(decay_linear(correlation(rank(l), rank(adv180), 16.6492), 10.4161), 14.9238))
    if A.want("alpha072"):
        A["alpha072"] = (rank(decay_linear(correlation(((h + l) / 2), adv40, 8.93345), 10.1519)) /
                         rank(decay_linear(correlation(ts_rank(vwap, 3.72469), ts_rank(v, 18.5188), 6.86671), 2.95011)))
    if A.want("alpha073"):
        A["alpha073"] = (np.maximum(rank(decay_linear(delta(vwap, 4.72775), 2.91864)),
                                    ts_rank(decay_linear(((delta(((o * 0.147155) + (l * (1 - 0.147155))), 2.03608) /
                                                           ((o * 0.147155) + (l * (1 - 0.147155)))) * -1), 3.33829), 16.7411)) * -1)
    if A.want("alpha074"):
        A["alpha074"] = ((rank(correlation(c, ts_sum(adv30, 37.4843), 15.1365)) <
                          rank(correlation(rank((h * 0.0261661 + l * (1 - 0.0261661))), rank(v), 11.4791)))
                         .astype(float) * -1)
    if A.want("alpha075"):
        A["alpha075"] = (rank(correlation(vwap, v, 4.24304)) <
                         rank(correlation(rank(l), rank(adv50), 12.4413))).astype(float)
    if A.want("alpha076"):
        A["alpha076"] = (np.maximum(rank(decay_linear(delta(vwap, 1.24383), 11.8259)),
                                    ts_rank(decay_linear(ts_rank(correlation(rank(l), rank(adv81), 8.14941), 19.569), 17.1543), 19.383)) * -1)
    if A.want("alpha077"):
        A["alpha077"] = np.minimum(rank(decay_linear(((((h + l) / 2) + h) - (vwap + h)), 20.0451)),
                                   rank(decay_linear(correlation(((h + l) / 2), adv40, 3.1614), 5.64125)))
    if A.want("alpha078"):
        A["alpha078"] = (rank(ts_sum(((l * 0.352233) + (vwap * (1 - 0.352233))), 19.7428)) **
                         rank(ts_sum(correlation(rank(vwap), rank(v), 6.83313), 5.77492)))
    if A.want("alpha079"):
        A["alpha079"] = (rank(delta(industry_proxy(c, o, vwap), 0.60733)) **
                         ts_rank(correlation(ts_rank(vwap, 3.60973), ts_rank(adv150, 9.18637), 14.6644), 15.1344))
    if A.want("alpha080"):
        A["alpha080"] = ((rank(sign(delta(proxy_ohlc(c, o, h, l), 4.24304))) **
                          ts_rank(correlation(proxy_ohlc(h, l, c, o), adv10, 5.38344), 4.04894))
                         * -1)

    # --- 081-101 ---
    if A.want("alpha081"):
        A["alpha081"] = ((rank(log(ts_product(rank(rank(correlation(vwap, ts_sum(adv10, 49.6054), 8.47743)) ** 4), 14.9655))) <
                          rank(correlation(rank(vwap), rank(v), 5.79307))).astype(float) * -1)
    if A.want("alpha082"):
        A["alpha082"] = (np.minimum(rank(decay_linear(delta(o, 1.46063), 14.8717)),
                                    ts_rank(decay_linear(correlation(proxy_ohlc_hl(h, l), adv20, 6.25), 6.75), 6.09)) * -1)
    if A.want("alpha083"):
        A["alpha083"] = ((rank(delay(((h - l) / (ts_sum(c, 5) / 5)), 2)) *
                          rank(rank(v))) / (((h - l) / (ts_sum(c, 5) / 5)) / (vwap - c)))
    if A.want("alpha084"):
        A["alpha084"] = (signed_power(ts_rank((vwap - ts_max(vwap, 15.3217)), 20.7127),
                                      delta(c, 4.96796)))
    if A.want("alpha085"):
        A["alpha085"] = (rank(correlation(((h * 0.876703) + (c * (1 - 0.876703))), adv30, 9.61331)) **
                         rank(correlation(ts_rank(((h + l) / 2), 3.70596), ts_rank(v, 10.1595), 7.11408)))
    if A.want("alpha086"):
        A["alpha086"] = ((ts_rank(correlation(c, ts_sum(adv20, 14.7444), 6.00049), 20.4195) <
                          rank(((o + c) - (vwap + o)))).astype(float) * -1)
    if A.want("alpha087"):
        A["alpha087"] = (np.maximum(rank(decay_linear(delta(proxy_oc(o, c, vwap), 4.5), 7.0)),
                                    ts_rank(decay_linear(correlation(ts_rank(c, 5.0), ts_rank(adv81, 10.0), 6.0), 5.0), 8.0)) * -1)
    if A.want("alpha088"):
        A["alpha088"] = np.minimum(
            rank(decay_linear(((rank(o) + rank(l)) - (rank(h) + rank(c))), 8.06882)),
            ts_rank(decay_linear(correlation(ts_rank(c, 8.44728), ts_rank(adv60, 20.6966), 8.01266), 6.65053), 2.61957))
    if A.want("alpha089"):
        A["alpha089"] = (ts_rank(decay_linear(correlation(((l * 0.967285) + (l * (1 - 0.967285))), adv10, 6.94279), 5.51607), 3.79744) -
                         ts_rank(decay_linear(delta(proxy_oc(o, c, vwap), 3.48158), 10.1466), 15.3012))
    if A.want("alpha090"):
        A["alpha090"] = ((rank((c - ts_max(c, 4.66719))) **
                          ts_rank(correlation(adv40, l, 2.0), 3.0)) * -1)
    if A.want("alpha091"):
        A["alpha091"] = ((ts_rank(decay_linear(decay_linear(correlation(rank(c), rank(adv30), 4.0), 4.0), 13.0), 12.0) -
                          rank(decay_linear(correlation(rank(vwap), rank(v), 5.0), 3.0))) * -1)
    if A.want("alpha092"):
        A["alpha092"] = (np.minimum(
            ts_rank(decay_linear(((((h + l) / 2) + c) < (l + o)).astype(float), 14.7221), 18.8683),
            ts_rank(decay_linear(correlation(rank(l), rank(adv30), 7.58555), 6.94024), 6.80584)))
    if A.want("alpha093"):
        A["alpha093"] = (ts_rank(decay_linear(correlation(proxy_oc(o, c, vwap), adv81, 17.4193), 19.848), 7.54455) /
                         rank(decay_linear(delta(((c * 0.524434) + (vwap * (1 - 0.524434))), 2.77377), 16.2664)))
    if A.want("alpha094"):
        A["alpha094"] = ((rank((vwap - ts_min(vwap, 11.5783))) **
                          ts_rank(correlation(ts_rank(vwap, 19.6462), ts_rank(adv60, 4.02992), 18.0926), 2.70756)) * -1)
    if A.want("alpha095"):
        A["alpha095"] = (rank((o - ts_min(o, 12.4105))) <
                         ts_rank(correlation(ts_rank(((h + l) / 2), 19.1351), ts_rank(adv40, 12.8742), 5.15255), 11.7584)).astype(float)
    if A.want("alpha096"):
        A["alpha096"] = (np.maximum(
            ts_rank(decay_linear(correlation(rank(vwap), rank(v), 3.83878), 4.16783), 8.38151),
            ts_rank(decay_linear(ts_argmax(correlation(ts_rank(c, 7.45404), ts_rank(adv60, 4.13242), 3.65459), 12.6556), 14.0365), 13.4143)) * -1)
    if A.want("alpha097"):
        A["alpha097"] = ((rank(decay_linear(delta(proxy_oc(l, h, vwap), 0.17166), 5.18115)) <
                          ts_rank(decay_linear(ts_rank(correlation(c, ts_sum(adv60, 21.7241), 3.0), 7.99648), 11.0689), 16.368)) * -1).astype(float)
    if A.want("alpha098"):
        A["alpha098"] = (rank(decay_linear(correlation(vwap, ts_sum(adv5, 26.4719), 4.58418), 7.18088)) -
                         rank(decay_linear(ts_rank(ts_argmin(correlation(rank(o), rank(adv15), 20.8187), 8.62571), 6.95668), 8.07206)))
    if A.want("alpha099"):
        A["alpha099"] = ((rank(correlation(ts_sum(((h + l) / 2), 19.8975), ts_sum(adv60, 19.8975), 8.8136)) <
                          rank(correlation(l, v, 6.28259))).astype(float) * -1)
    if A.want("alpha100"):
        A["alpha100"] = (0 - (1 * (((1.5 * scale(industry_proxy2(v, rank(adv20)))) -
                                     scale(correlation(c, rank(adv20), 5))) * (v / vwap))))
    if A.want("alpha101"):
        A["alpha101"] = ((c - o) / ((h - l) + 0.001))

    # 统一收口: 保证全部是 DataFrame / 同形状 / 无 inf
    # (流式模式下 A 已被回调消费, 此处为空)
    clean = {}
    for k, v in A.items():
        d = v if isinstance(v, pd.DataFrame) else pd.DataFrame(
            np.asarray(v, dtype=float), index=c.index, columns=c.columns)
        d = d.reindex(index=c.index, columns=c.columns)
        clean[k] = d.replace([np.inf, -np.inf], np.nan).astype(float)
    return clean


# ---- 辅助: 因公式需要但与主输入不同的组合 ----
def industry_proxy(c, o, vwap):
    return vwap


def proxy_ohlc(c, o, h, l):
    return (h + l) / 2


def proxy_ohlc_hl(h, l):
    return (h + l) / 2


def proxy_oc(o, c, vwap):
    return (o + c) / 2


def industry_proxy2(v, ranked_adv):
    return (v - ranked_adv) / ranked_adv
