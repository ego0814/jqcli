from jqdata import *
# ============================================================
# 【已废弃 / DEPRECATED】2026-09-25
# 失败策略：v2 长区间 -61.51% / 夏普 -0.564 / 回撒 79.55%
# 归档：failures/ai_momentum_300_v2_2026-09-16.md
# 不再维护
# ============================================================



INDEX = "000300.XSHG"
LOOKBACK = 20
TARGET_COUNT = 5


def initialize(context):
    set_benchmark(INDEX)
    set_option("use_real_price", True)
    set_order_cost(OrderCost(
        close_tax=0.001,
        open_commission=0.0003,
        close_commission=0.0003,
        min_commission=5,
    ), type="stock")
    set_slippage(FixedSlippage(0.002))
    run_weekly(rebalance, weekday=1, time="open")


def rebalance(context):
    candidates = _eligible_stocks(get_index_stocks(INDEX))
    momentum = _momentum(candidates, context.previous_date)
    selected = [stock for stock, _ in sorted(momentum.items(), key=lambda item: item[1], reverse=True)[:TARGET_COUNT]]

    for stock in context.portfolio.positions:
        if stock not in selected:
            order_target_value(stock, 0)

    if not selected:
        return

    target_value = context.portfolio.total_value / len(selected)
    for stock in selected:
        order_target_value(stock, target_value)


def _eligible_stocks(stocks):
    current_data = get_current_data()
    return [
        stock
        for stock in stocks
        if not current_data[stock].paused and not current_data[stock].is_st
    ]


def _momentum(stocks, end_date):
    if not stocks:
        return {}

    prices = get_price(
        stocks,
        end_date=end_date,
        frequency="1d",
        fields=["close"],
        count=LOOKBACK + 1,
        fq="pre",
        panel=False,
        fill_paused=False,
    )
    result = {}
    for stock, data in prices.groupby("code"):
        closes = data["close"].dropna()
        if len(closes) == LOOKBACK + 1 and closes.iloc[0] > 0:
            result[stock] = closes.iloc[-1] / closes.iloc[0] - 1
    return result
