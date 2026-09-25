# EP 单因子策略 v1
# 规格书：specs/ep_strategy_v1.md
# 数据：/predictions/ep/predictions_ep_v1.csv（列：date,symbol,value）
import io
import pandas as pd
from jqdata import *

PREDICTION_PATH = "/predictions/ep/predictions_ep_v1.csv"
BENCHMARK = "000985.XSHG"
TOP_N = 50
TOP_N_REDUCED = 30
MAX_WEIGHT = 0.05
MIN_LISTED_DAYS = 60
MIN_AVG_AMOUNT = 50000000
LOOKBACK_AMOUNT = 20
REBALANCE_MONTHS = (1, 4, 7, 10)
COMMISSION = 0.0000854
MIN_COMMISSION = 5
SLIPPAGE = 0.0005
CANDIDATE_MULTIPLIER = 3


def initialize(context):
    set_benchmark(BENCHMARK)
    set_option("use_real_price", True)
    for cost_type in ("stock", "fund"):
        set_order_cost(OrderCost(
            open_tax=0,
            close_tax=0.0005,
            open_commission=COMMISSION,
            close_commission=COMMISSION,
            min_commission=MIN_COMMISSION,
        ), type=cost_type)
    set_slippage(PriceRelatedSlippage(SLIPPAGE))
    g.predictions = _load_predictions()
    g.last_rebalance_month = None
    g.hold_n = TOP_N
    run_daily(_check_rebalance, time="open")


def _load_predictions():
    """读取预测值表，返回 {date: {symbol: value}}；失败时返回空字典（降级：不调仓）。"""
    try:
        content = read_file(PREDICTION_PATH)
        if content is None:
            raise ValueError("read_file 返回 None")
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        raw = pd.read_csv(io.StringIO(content))
    except Exception as exc:
        log.warning("预测值表读取失败：%s", exc)
        return {}
    raw["date"] = raw["date"].astype(str)
    table = {}
    for date, group in raw.groupby("date"):
        table[date] = dict(zip(group["symbol"], group["value"]))
    log.info("预测值表加载完成：%d 个调仓日", len(table))
    return table


def _check_rebalance(context):
    today = context.current_dt.date()
    if today.month not in REBALANCE_MONTHS:
        return
    prior = context.previous_date
    if (prior.year, prior.month) == (today.year, today.month):
        return  # 本月已有交易日，今天不是本月第一个交易日
    key = (today.year, today.month)
    if g.last_rebalance_month == key:
        return
    g.last_rebalance_month = key
    _rebalance(context, today)


def _rebalance(context, today):
    scores = _scores_for(today)
    if not scores:
        log.warning("当期预测值表缺失（%s），本期不调仓，保持上期持仓", today)
        return
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    candidates = [s for s, _ in ranked[: g.hold_n * CANDIDATE_MULTIPLIER]]
    current_data = get_current_data()
    tradable = _filter_tradable(candidates, context, current_data)
    target = tradable[: g.hold_n]

    for code in list(context.portfolio.positions.keys()):
        position = context.portfolio.positions[code]
        if position.total_amount <= 0 or code in target:
            continue
        try:
            unit = current_data[code]
        except Exception:
            log.warning("持仓代码在当前时点不可用，跳过卖出：%s", code)
            continue
        if _blocked_sell(unit):
            log.warning("跌停或停牌无法卖出，顺延到下次调仓：%s", code)
            continue
        order_target_value(code, 0)

    if not target:
        log.warning("本期无可买标的，保持现金")
        return
    value_each = context.portfolio.total_value / len(target)
    cap = context.portfolio.total_value * MAX_WEIGHT
    value_each = min(value_each, cap)
    for code in target:
        if _blocked_buy(current_data[code]):
            log.warning("涨停或停牌无法买入，跳过：%s", code)
            continue
        order_target_value(code, value_each)


def _scores_for(today):
    key = str(today)
    if key in g.predictions:
        return g.predictions[key]
    earlier = [d for d in g.predictions if d <= key]
    if not earlier:
        return {}
    return g.predictions[max(earlier)]


def _filter_tradable(candidates, context, current_data):
    today = context.current_dt.date()
    kept = []
    for code in candidates:
        try:
            unit = current_data[code]
        except Exception:
            log.warning("代码在当前时点不可用，跳过：%s", code)
            continue
        if unit.paused or _blocked_buy(unit):
            continue
        info = get_security_info(code)
        if info is not None and info.start_date is not None:
            listed_days = (today - info.start_date).days
            if listed_days < MIN_LISTED_DAYS:
                continue
        hist = attribute_history(code, LOOKBACK_AMOUNT, "1d", ["money"], df=False)
        money = hist.get("money")
        if money is None or len(money) == 0 or float(pd.Series(money).mean()) < MIN_AVG_AMOUNT:
            continue
        kept.append(code)
    return kept


def _blocked_buy(unit):
    price = getattr(unit, "day_open", None)
    high = getattr(unit, "high_limit", None)
    if not price or not high:
        return False
    return price >= high


def _blocked_sell(unit):
    price = getattr(unit, "day_open", None)
    low = getattr(unit, "low_limit", None)
    if not price or not low:
        return False
    return price <= low
