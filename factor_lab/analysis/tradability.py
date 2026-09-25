# -*- coding: utf-8 -*-
r"""可交易性过滤：涨跌停、停牌、流动性。

本模块服务两类用途，必须区分，否则会产生前视偏差：

1. 研究样本筛选（filter_tradable）——决定某个 (信号日 T, symbol) 是否进入
   IC / 分层统计。只能用 T 日收盘及之前已知的信息：T 日及之前 lookback_days
   个交易日的停牌与成交额。禁止用 T+1 之后的行情筛样本。
2. 执行可行性诊断（tradability_audit）——检查按 T 日信号在 T+1 执行时是否会
   遇到涨停买不进 / 跌停卖不出。这类检查必须看未来，但只能用于诊断和执行
   假设，不能用来筛 IC 样本。

涨跌停判定采用"区间"口径而非">= 阈值"：
    涨停 = |pct_chg - 阈值| <= margin
    跌停 = |pct_chg + 阈值| <= margin
原因：ST 股上限 5%，若用 >= 判定，10% 的涨幅（对该板块不可能发生）会被误判为涨停。
区间口径下 主板 9.9% → 涨停、9.5% → 否；创业板 19.9% → 涨停、9.9% → 否；
ST 4.9% → 涨停、9.9% → 否，符合各板块实际规则。

自测：python tradability.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_MARGIN = 0.002
BOARD_LIMITS = {
    "创业板": 0.20,
    "科创板": 0.20,
    "北交所": 0.30,
    "主板": 0.10,
}
ST_LIMIT = 0.05


def board_type_of(market: str | None) -> str:
    text = str(market or "").strip()
    for name in ("科创板", "创业板", "北交所"):
        if name in text:
            return name
    return "主板"


def limit_threshold(board_type: str, is_st: bool) -> float:
    if is_st:
        return ST_LIMIT
    return BOARD_LIMITS.get(board_type, 0.10)


def is_limit_up(pct_chg, board_type: str = "主板", is_st: bool = False, margin: float = DEFAULT_MARGIN):
    if pct_chg is None or (isinstance(pct_chg, float) and np.isnan(pct_chg)):
        return False
    return abs(float(pct_chg) / 100.0 - limit_threshold(board_type, is_st)) <= margin


def is_limit_down(pct_chg, board_type: str = "主板", is_st: bool = False, margin: float = DEFAULT_MARGIN):
    if pct_chg is None or (isinstance(pct_chg, float) and np.isnan(pct_chg)):
        return False
    return abs(float(pct_chg) / 100.0 + limit_threshold(board_type, is_st)) <= margin


def is_suspended(vol) -> bool:
    if vol is None:
        return True
    try:
        value = float(vol)
    except (TypeError, ValueError):
        return True
    return np.isnan(value) or value == 0


def _calendar(trade_cal) -> list[str]:
    if isinstance(trade_cal, pd.DataFrame):
        frame = trade_cal.copy()
        if "is_open" in frame.columns:
            frame = frame[frame["is_open"].astype(int) == 1]
        column = "cal_date" if "cal_date" in frame.columns else frame.columns[0]
        days = frame[column].astype(str).tolist()
    else:
        days = [str(d) for d in trade_cal]
    out = []
    for raw in days:
        text = raw.replace("-", "")[:8]
        if len(text) == 8 and text.isdigit():
            out.append("{}-{}-{}".format(text[:4], text[4:6], text[6:8]))
    return sorted(set(out))


def windows(cal: list[str], rebalance_dates, lookback_days: int) -> dict:
    """PIT 窗口：T 及之前 lookback_days 个交易日（不含 T 之后）。

    历史不足 lookback_days 个交易日的 T 不出现在返回值中。
    """
    pos = {d: i for i, d in enumerate(cal)}
    out = {}
    for T in rebalance_dates:
        i = pos.get(T)
        if i is None or i + 1 < lookback_days:
            continue
        out[T] = cal[i + 1 - lookback_days:i + 1]
    return out


def future_windows(cal: list[str], rebalance_dates, lookback_days: int) -> dict:
    """执行诊断窗口：T+1 起的 lookback_days 个交易日。

    只有事后诊断（如"T+1 是否涨停买不进"）可以使用；不得用于筛 IC 样本。
    """
    pos = {d: i for i, d in enumerate(cal)}
    out = {}
    for T in rebalance_dates:
        i = pos.get(T)
        if i is None or i + lookback_days >= len(cal):
            continue
        out[T] = cal[i + 1:i + 1 + lookback_days]
    return out


def _board_lookup(stock_basic: pd.DataFrame) -> dict:
    if not {"ts_code", "market"}.issubset(stock_basic.columns):
        return {}
    return {str(r.ts_code): board_type_of(r.market) for r in stock_basic.itertuples(index=False)}


def _st_flags(frame: pd.DataFrame, st_mask: pd.DataFrame | None):
    """每个 (T, symbol) 的 ST 三态：1 = ST，0 = 非 ST，-1 = 未知。

    未命中掩码（日期不在掩码索引、代码不在掩码列、掩码值缺失）一律返回 -1，
    即 fail-closed，不再默认按"非 ST"处理。

    st_mask 为 None 时返回 None，表示调用方没有提供 ST 掩码、未要求评估 ST 维度。
    这与"提供了掩码但查不到"是两件事，不能混同成"全部非 ST"。
    """
    if st_mask is None:
        return None
    flags = np.full(len(frame), -1, dtype=np.int8)
    pos_d = {str(i).replace("-", "")[:8]: k for k, i in enumerate(st_mask.index)}
    pos_s = {str(c): k for k, c in enumerate(st_mask.columns)}
    di = frame["rebalance_date"].map(lambda d: pos_d.get(str(d).replace("-", "")[:8], -1)).to_numpy()
    sj = frame["symbol"].map(lambda s: pos_s.get(str(s), -1)).to_numpy()
    ok = (di >= 0) & (sj >= 0)
    if ok.any():
        raw = st_mask.values[di[ok], sj[ok]]
        resolved = ~pd.isna(raw)
        flags[np.flatnonzero(ok)[resolved]] = raw[resolved].astype(np.int8)
    return flags


def filter_tradable(panel_df: pd.DataFrame, px_df: pd.DataFrame, trade_cal, stock_basic: pd.DataFrame,
                    min_avg_amount: float = 5e7, max_suspend_days: int = 5,
                    lookback_days: int = 21, st_mask: pd.DataFrame | None = None,
                    margin: float = DEFAULT_MARGIN, amount_unit_multiplier: float = 1000.0) -> pd.DataFrame:
    """给 panel 追加 tradable / drop_reason，只使用 T 日及之前的信息。

    剔除原因：
      NO_WINDOW        T 之前不足 lookback_days 个交易日，无法核验流动性
      NO_PRICE_T       T 日无行情行
      SUSPENDED_T      T 日停牌（vol 缺失或为 0）
      SUSPEND_HISTORY  T 日及之前的窗口内停牌（含无行情行）超过 max_suspend_days
      ILLIQUID         同窗口内日均成交额低于 min_avg_amount
      ST_UNKNOWN       提供了 ST 掩码，但该 (T, symbol) 查不到或掩码值缺失（fail-closed）

    关于 ST：st_mask=None 表示调用方未提供掩码，ST 维度不参与筛选（口径与历史一致）；
    提供了掩码却查不到时按 ST_UNKNOWN 剔除，不再默认放行。

    窗口内没有行情行的日子按未观测处理并计入停牌天数，因此新上市标的
    在攒满一个完整窗口之前不会进入研究样本。

    注意：Tushare daily.amount 单位为千元，amount_unit_multiplier 默认 1000 换算成元。
    局限：T+1 才发生的涨跌停 / 停牌在 T 日不可知，本函数不模拟
    "买不进 / 卖不出"；该判断属于执行层诊断（见 tradability_audit）。
    """
    import time as _time
    t0 = _time.time()
    for column in ("vol", "amount"):
        if column not in px_df.columns:
            raise ValueError("px_df 缺少列 {}，无法做 PIT 可交易性过滤".format(column))

    cal = _calendar(trade_cal)
    dates = sorted(panel_df["rebalance_date"].unique())
    win = windows(cal, dates, lookback_days)

    frame = panel_df.copy().reset_index(drop=True)
    frame["_has_window"] = frame["rebalance_date"].isin(win)

    px = px_df.rename(columns={"ts_code": "symbol", "trade_date": "rebalance_date"}).copy()
    px["rebalance_date"] = px["rebalance_date"].map(
        lambda v: _calendar([v])[0] if _calendar([v]) else str(v))
    px = px.drop_duplicates(["symbol", "rebalance_date"], keep="last")

    at_t = px[["symbol", "rebalance_date", "vol"]].rename(columns={"vol": "_t_vol"})
    frame = frame.merge(at_t, on=["symbol", "rebalance_date"], how="left")

    # 一对多映射：同一交易日可能同时落在相邻两个 T 的窗口内，必须都保留
    pairs = [(day, T) for T in dates for day in win.get(T, [])]
    pairs_df = pd.DataFrame(pairs, columns=["rebalance_date", "_T"])
    if pairs_df.empty:
        agg = pd.DataFrame(columns=["symbol", "rebalance_date", "_avg_amount", "_suspend", "_observed"])
    else:
        px2 = px.merge(pairs_df, on="rebalance_date", how="inner")
        px2["_susp"] = px2["vol"].isna() | (px2["vol"] == 0)
        agg = px2.groupby(["symbol", "_T"]).agg(
            _avg_amount=("amount", "mean"), _suspend=("_susp", "sum"),
            _observed=("vol", "size")).reset_index().rename(columns={"_T": "rebalance_date"})
    frame = frame.merge(agg, on=["symbol", "rebalance_date"], how="left")
    frame["_avg_amount"] = pd.to_numeric(frame["_avg_amount"], errors="coerce") * amount_unit_multiplier
    # 窗口内没有行情行的日子 = 未观测，计入停牌天数
    frame["_suspend"] = frame["_suspend"].fillna(0) + (lookback_days - frame["_observed"].fillna(0))

    board_map = _board_lookup(stock_basic)
    frame["_board"] = frame["symbol"].map(board_map).fillna("主板")
    st_state = _st_flags(frame, st_mask)
    if st_state is None:
        # 调用方未提供 ST 掩码：不启用 ST 维度（不因此剔除样本）
        frame["_is_st"] = False
        frame["_st_unknown"] = False
    else:
        frame["_is_st"] = st_state == 1
        frame["_st_unknown"] = st_state < 0

    frame["_no_window"] = ~frame["_has_window"]
    frame["_no_price"] = frame["_t_vol"].isna()
    frame["_today_suspended"] = frame["_t_vol"].notna() & (frame["_t_vol"] == 0)
    frame["_over_suspend"] = frame["_suspend"] > max_suspend_days
    frame["_illiquid"] = frame["_avg_amount"].fillna(0) < min_avg_amount

    reasons = []
    for col, name in (("_no_window", "NO_WINDOW"), ("_no_price", "NO_PRICE_T"),
                      ("_today_suspended", "SUSPENDED_T"), ("_over_suspend", "SUSPEND_HISTORY"),
                      ("_illiquid", "ILLIQUID"), ("_st_unknown", "ST_UNKNOWN")):
        reasons.append(frame[col].fillna(False).map({True: name, False: ""}))

    combined = reasons[0]
    for extra in reasons[1:]:
        combined = combined + np.where((combined != "") & (extra != ""), "|", "") + extra
    frame["drop_reason"] = combined
    frame["tradable"] = frame["drop_reason"] == ""
    out = frame.drop(columns=[c for c in frame.columns if c.startswith("_")])
    print("[timing] filter_tradable(PIT): {:.2f}s rows={:,}".format(_time.time() - t0, len(out)), flush=True)
    return out[list(panel_df.columns) + ["tradable", "drop_reason"]]


def audit_columns(min_avg_amount: float, max_suspend_days: int, lookback_days: int = 21) -> dict:
    """执行诊断用的参数快照，便于日志里记录口径。"""
    return {"min_avg_amount": min_avg_amount, "max_suspend_days": max_suspend_days,
            "lookback_days": lookback_days, "window": "T+1 起（仅诊断，不用于筛样本）"}


def _self_test() -> int:
    checks, failures = 0, []

    def check(name, expected, actual):
        nonlocal checks
        checks += 1
        if expected != actual:
            failures.append("{}: 预期 {} 实际 {}".format(name, expected, actual))

    # ---- 阈值口径 ----
    check("主板 9.9% 涨停", True, is_limit_up(9.9, "主板"))
    check("主板 9.5% 非涨停", False, is_limit_up(9.5, "主板"))
    check("创业板 19.9% 涨停", True, is_limit_up(19.9, "创业板"))
    check("创业板 9.9% 非涨停", False, is_limit_up(9.9, "创业板"))
    check("科创板 19.9% 涨停", True, is_limit_up(19.9, "科创板"))
    check("ST 4.9% 涨停", True, is_limit_up(4.9, "主板", True))
    check("ST 9.9% 非涨停", False, is_limit_up(9.9, "主板", True))
    check("北交所 29.9% 涨停", True, is_limit_up(29.9, "北交所"))
    check("主板 -9.9% 跌停", True, is_limit_down(-9.9, "主板"))
    check("主板 -9.5% 非跌停", False, is_limit_down(-9.5, "主板"))
    check("ST -4.9% 跌停", True, is_limit_down(-4.9, "主板", True))
    check("vol=0 停牌", True, is_suspended(0))
    check("vol=NaN 停牌", True, is_suspended(float("nan")))
    check("vol=1000 正常", False, is_suspended(1000))
    check("market 映射创业板", "创业板", board_type_of("创业板"))
    check("market 映射主板", "主板", board_type_of("主板"))
    check("market 映射北交所", "北交所", board_type_of("北交所"))

    # ---- 窗口口径 ----
    cal = ["2024-01-{:02d}".format(i) for i in range(2, 32)]
    check("PIT 窗口含 T 且不越界",
          ["2024-01-02", "2024-01-03", "2024-01-04"],
          windows(cal, ["2024-01-04"], 3)["2024-01-04"])
    check("PIT 窗口历史不足时跳过", {}, windows(cal, ["2024-01-03"], 3))
    check("执行诊断窗口向前看",
          ["2024-01-05", "2024-01-06"],
          future_windows(cal, ["2024-01-04"], 2)["2024-01-04"])

    # ---- PIT 样本筛选 ----
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    liquid = 100000.0          # 千元 = 1 亿元
    tiny = 1000.0              # 千元 = 100 万元
    rows = []

    def add(symbol, day, pct, vol, amount_kyuan):
        rows.append({"ts_code": symbol, "trade_date": day, "pct_chg": pct,
                     "vol": vol, "amount": amount_kyuan})

    def series(symbol, vols, amounts, pcts, upto=3):
        for i in range(upto):
            add(symbol, days[i], pcts[i], vols[i], amounts[i])

    # A：T+1 涨停，但 T 日信息正常 -> PIT 下必须仍可交易（不得偷看未来）
    series("A.SZ", [1000, 1000, 1000], [liquid] * 3, [1.0, 1.0, 1.0])
    add("A.SZ", days[3], 9.95, 1000, liquid)
    # B：仅 T 日停牌
    series("B.SZ", [1000, 1000, 0], [liquid] * 3, [1.0, 1.0, 1.0])
    # C：窗口内两日停牌（超过 max_suspend_days=1）
    series("C.SZ", [0, 0, 1000], [liquid] * 3, [1.0, 1.0, 1.0])
    # D：T 日无行情行
    series("D.SZ", [1000, 1000], [liquid] * 3, [1.0, 1.0], upto=2)
    # E：只有 T 日一行，窗口内其余日子未观测
    add("E.SZ", days[2], 1.0, 1000, liquid)
    # F：成交额不足
    series("F.SZ", [1000] * 3, [tiny] * 3, [1.0, 1.0, 1.0])
    px = pd.DataFrame(rows)

    symbols = ["A.SZ", "B.SZ", "C.SZ", "D.SZ", "E.SZ", "F.SZ", "G.SZ", "H.SZ"]
    panel = pd.DataFrame({
        "symbol": symbols,
        "rebalance_date": ["2024-01-04"] * 7 + ["2024-01-02"],
    })
    sb = pd.DataFrame({"ts_code": symbols, "market": ["主板"] * len(symbols)})
    res = filter_tradable(panel, px, cal, sb, min_avg_amount=5e7, max_suspend_days=1, lookback_days=3)

    def tradable(symbol):
        return bool(res.loc[res.symbol == symbol, "tradable"].iloc[0])

    def reason(symbol):
        return res.loc[res.symbol == symbol, "drop_reason"].iloc[0]

    check("T+1 涨停不影响 PIT 筛选", True, tradable("A.SZ"))
    check("A 无剔除原因", "", reason("A.SZ"))
    check("T 日停牌被剔除", False, tradable("B.SZ"))
    check("T 日停牌原因", "SUSPENDED_T", reason("B.SZ"))
    check("窗口内停牌过多被剔除", False, tradable("C.SZ"))
    check("窗口内停牌原因", "SUSPEND_HISTORY", reason("C.SZ"))
    check("T 日无行情被剔除", False, tradable("D.SZ"))
    check("T 日无行情原因", "NO_PRICE_T", reason("D.SZ"))
    check("窗口未观测日计入停牌", False, tradable("E.SZ"))
    check("窗口未观测日原因", "SUSPEND_HISTORY", reason("E.SZ"))
    check("流动性不足被剔除", False, tradable("F.SZ"))
    check("流动性原因", "ILLIQUID", reason("F.SZ"))
    check("完全无行情被剔除", False, tradable("G.SZ"))
    check("完全无行情原因", "NO_PRICE_T", str(reason("G.SZ")).split("|")[0])
    check("历史不足被剔除", False, tradable("H.SZ"))
    check("历史不足原因", "NO_WINDOW", str(reason("H.SZ")).split("|")[0])
    check("输出保留原列", True, set(panel.columns).issubset(set(res.columns)))
    check("不泄漏未来列", False, any(c.startswith("_") for c in res.columns))

    # ---- ST 未命中必须 fail-closed ----
    liquid_st = 100000.0
    st_days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    st_syms = ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]
    st_rows = [{"ts_code": sym, "trade_date": d, "pct_chg": 1.0, "vol": 1000, "amount": liquid_st}
               for sym in st_syms for d in st_days]
    px_st = pd.DataFrame(st_rows)
    panel_st = pd.DataFrame({"symbol": st_syms, "rebalance_date": ["2024-01-04"] * len(st_syms)})
    sb_st = pd.DataFrame({"ts_code": st_syms, "market": ["主板"] * len(st_syms)})

    check("_st_flags 未提供掩码返回 None", None, _st_flags(panel_st, None))

    mask_ok = pd.DataFrame({"A.SZ": [False], "B.SZ": [True], "C.SZ": [None]},
                           index=["2024-01-04"]).astype(object)
    tri = _st_flags(panel_st, mask_ok)
    check("_st_flags 三态-已知非ST", 0, int(tri[0]))
    check("_st_flags 三态-已知ST", 1, int(tri[1]))
    check("_st_flags 三态-掩码值缺失为未知", -1, int(tri[2]))
    check("_st_flags 三态-不在掩码列为未知", -1, int(tri[3]))

    res_st = filter_tradable(panel_st, px_st, cal, sb_st, min_avg_amount=5e7,
                             max_suspend_days=5, lookback_days=3, st_mask=mask_ok)
    check("已知非ST不因ST被剔除", True, bool(res_st.loc[res_st.symbol == "A.SZ", "tradable"].iloc[0]))
    check("已知ST不由本函数剔除", True, bool(res_st.loc[res_st.symbol == "B.SZ", "tradable"].iloc[0]))
    check("掩码值缺失按UNKNOWN剔除", False, bool(res_st.loc[res_st.symbol == "C.SZ", "tradable"].iloc[0]))
    check("掩码值缺失原因", "ST_UNKNOWN", res_st.loc[res_st.symbol == "C.SZ", "drop_reason"].iloc[0])
    check("不在掩码列按UNKNOWN剔除", False, bool(res_st.loc[res_st.symbol == "D.SZ", "tradable"].iloc[0]))
    check("不在掩码列原因", "ST_UNKNOWN", res_st.loc[res_st.symbol == "D.SZ", "drop_reason"].iloc[0])

    mask_other_day = pd.DataFrame({sym: [False] for sym in st_syms}, index=["2024-01-03"])
    res_day = filter_tradable(panel_st, px_st, cal, sb_st, min_avg_amount=5e7,
                              max_suspend_days=5, lookback_days=3, st_mask=mask_other_day)
    check("掩码缺该日期按UNKNOWN剔除", False, bool(res_day.loc[res_day.symbol == "A.SZ", "tradable"].iloc[0]))
    check("掩码缺该日期原因", "ST_UNKNOWN", res_day.loc[res_day.symbol == "A.SZ", "drop_reason"].iloc[0])

    res_none = filter_tradable(panel_st, px_st, cal, sb_st, min_avg_amount=5e7,
                               max_suspend_days=5, lookback_days=3)
    check("未提供掩码时不启用ST维度", True, bool(res_none["tradable"].all()))
    check("未提供掩码时无ST_UNKNOWN",
          True, all("ST_UNKNOWN" not in r for r in res_none["drop_reason"]))
    print("tradability 单元测试: {} 项，失败 {}".format(checks, len(failures)))
    for item in failures:
        print("  FAIL", item)
    print("ALL PASS" if not failures else "NOT ALL PASS")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())