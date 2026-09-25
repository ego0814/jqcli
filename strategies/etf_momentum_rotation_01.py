# ETF动量轮动-01 v1
# ============================================================
# 【已废弃 / DEPRECATED】2026-09-25
# 失败策略：夏普 -0.007 / 回撒 35.04%
# 扩池检验也证明动量方向放弃
# 归档：records/etf_momentum_rotation_01.md
# 不再维护
# ============================================================

# 规格书：specs/etf_momentum_rotation_01.md
# 成本：specs/costs/standard.md（ETF 免印花税，故 close_tax=0）
# 逻辑：每周第一个交易日开盘，用前一交易日的前复权收盘价计算 20 日动量，
#       持有动量最强的一只；三只动量全为负则空仓持现金。

from jqdata import *

UNIVERSE = ["510500.XSHG", "510300.XSHG", "159915.XSHE"]
BENCHMARK = "000300.XSHG"
LOOKBACK = 20
MIN_AVG_MONEY = 50000000
COMMISSION = 0.0000854
MIN_COMMISSION = 5
SLIPPAGE = 0.0005


def initialize(context):
    set_benchmark(BENCHMARK)
    set_option("use_real_price", True)
    # 聚宽按品种类型取成本配置，ETF 归属不确定，故 stock 与 fund 同时设置。
    for cost_type in ("stock", "fund"):
        set_order_cost(OrderCost(
            open_tax=0,
            close_tax=0,
            open_commission=COMMISSION,
            close_commission=COMMISSION,
            min_commission=MIN_COMMISSION,
        ), type=cost_type)
    set_slippage(PriceRelatedSlippage(SLIPPAGE))
    g.target = None
    g.target_ready = False
    g.attempt_date = None
    run_weekly(_generate_signal, weekday=1, time="open")
    run_daily(_execute_target, time="open")


def _generate_signal(context):
    frame = _price_frame(context.previous_date)
    current_data = get_current_data()
    candidates = _filter_candidates(frame, current_data)
    scores = _momentum_scores(frame, candidates)
    target = None
    if scores:
        best = max(scores, key=lambda code: scores[code])
        if scores[best] >= 0:
            target = best
    log.info("调仓信号 候选=%s 动量=%s 目标=%s", candidates, scores, target)
    g.target = target
    g.target_ready = True
    g.attempt_date = None
    _execute_target(context)


def _execute_target(context):
    if not g.target_ready:
        return
    today = context.current_dt.date()
    if g.attempt_date == today:
        return
    g.attempt_date = today

    current_data = get_current_data()
    blocked_sell = False
    for code in list(context.portfolio.positions.keys()):
        if code == g.target:
            continue
        position = context.portfolio.positions[code]
        if position.total_amount <= 0:
            continue
        unit = current_data[code]
        if unit.paused or _limit_blocked(unit, "sell"):
            log.warning("停牌或跌停无法卖出，顺延到下一交易日重试 %s", code)
            blocked_sell = True
            continue
        order_target_value(code, 0)

    if g.target is None:
        g.target_ready = blocked_sell
        return

    if blocked_sell:
        log.warning("持仓未能全部卖出，本轮暂不买入，顺延到下一交易日重试 %s", g.target)
        return

    unit = current_data[g.target]
    if unit.paused or _limit_blocked(unit, "buy"):
        log.warning("停牌或涨停无法买入，顺延到下一交易日重试 %s", g.target)
        return

    order_target_value(g.target, context.portfolio.total_value)
    g.target_ready = False


def _price_frame(end_date):
    return get_price(
        UNIVERSE,
        end_date=end_date,
        frequency="1d",
        fields=["close", "money"],
        count=LOOKBACK + 1,
        fq="pre",
        panel=False,
        fill_paused=False,
    )


def _filter_candidates(frame, current_data):
    candidates = []
    for code in UNIVERSE:
        if current_data[code].paused:
            log.info("剔除停牌标的 %s", code)
            continue
        rows = frame[frame["code"] == code].sort_values("time")
        closes = rows["close"].dropna()
        if len(closes) < LOOKBACK + 1:
            log.info("剔除价格数据不足标的 %s", code)
            continue
        money = rows["money"].dropna()
        if len(money) == 0:
            log.info("剔除无成交额数据标的 %s", code)
            continue
        avg_money = money.mean()
        if avg_money < MIN_AVG_MONEY:
            log.info("剔除流动性不足标的 %s 日均成交额=%.0f", code, avg_money)
            continue
        candidates.append(code)
    return candidates


def _momentum_scores(frame, candidates):
    scores = {}
    for code in candidates:
        rows = frame[frame["code"] == code].sort_values("time")
        closes = rows["close"].dropna()
        if len(closes) < LOOKBACK + 1:
            continue
        base = closes.iloc[0]
        if base <= 0:
            continue
        scores[code] = closes.iloc[-1] / base - 1
    return scores


def _limit_blocked(unit, side):
    price = getattr(unit, "day_open", None)
    if not price:
        return False
    if side == "buy":
        limit = getattr(unit, "high_limit", None)
        return bool(limit) and price >= limit
    limit = getattr(unit, "low_limit", None)
    return bool(limit) and price <= limit
