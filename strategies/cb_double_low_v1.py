# -*- coding: utf-8 -*-
"""CB 双低策略 v1

规格书：specs/cb_double_low_v1.md（v1.4；本地待验证）
成本契约：specs/costs/standard.md（v1.2，CB 段：保守口径）

逻辑：每月首个交易日，在可投池内按双低值（转债价 + 转股溢价率×100）升序取
      最小的 50% 分位，等权买入，单只上限 5%，月度内不调仓。
      7 条剔除规则见 _eligible_mask（含强赎双重防御与回售风险）。

数据：预测值表 /predictions/cb/cb_predictions_v1.csv
      列：date, symbol, double_low_value, premium_rate, cb_close, pool_ok,
          redeem_exclude, days_to_stop, put_risk, credit_ok
      由 factor_lab/data/make_cb_predictions.py 生成（本地）。调仓时点 = T 日收盘，取 date=T 的精确行；
      执行时点须与预测表口径一致：本文件按 T 日收盘调仓，未经聚宽编译与回测验证。
"""

import io

import pandas as pd

# ---------------------------------------------------------------------------
# 1. 常量（全部来自规格书 v1.2）
# ---------------------------------------------------------------------------
PREDICTION_PATH = "/predictions/cb/cb_predictions_v1.csv"
BENCHMARK = "000985.XSHG"          # 平台展示用基准；契约基准 = 可投池等权（阶段 4 外部对照）

TOP_QUANTILE = 0.50                # 双低最小的 50% 分位
MAX_WEIGHT = 0.05                  # 单只权重上限
HYSTERESIS_QUANTILE = 0.60
WEIGHT_BAND = 0.02
MIN_PRICE = 80.0                   # 规则 6：价格下限
HIGH_PRICE = 130.0                 # 规则 4：价格上限
HIGH_PRICE_PREMIUM = 2.0           # 规则 4：该价格下的溢价率上限（%）
PREMIUM_FLOOR = 0.0                # 规则 3：折价债剔除（溢价率 <= 0）
MIN_DAYS_TO_STOP = 30              # 规则 5：距 conv_stop_date/delist_date 天数下限

COMMISSION = 0.0002                # 万 2（standard.md v1.2 CB 段）
MIN_COMMISSION = 1                 # 1 元
SLIPPAGE = 0.002                   # 千 2
COST_BUFFER = 0.995                # 留 0.5% 现金缓冲，覆盖佣金+滑点（ETF v1 的教训）

ENABLE_REBALANCE = True            # False => 只建仓一次（买入持有对照，阶段 4 用）
SKIP_LIMIT_UP = True               # 涨停不追买，避免委托失败噪音


# ---------------------------------------------------------------------------
# 2. 初始化
# ---------------------------------------------------------------------------
def initialize(context):
    set_benchmark(BENCHMARK)
    set_option("use_real_price", True)
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0,
        open_commission=COMMISSION,
        close_commission=COMMISSION,
        min_commission=MIN_COMMISSION,
    ), type="fund")
    set_slippage(PriceRelatedSlippage(SLIPPAGE))

    g.predictions = _load_predictions()
    g.last_rebalance_month = None
    g.did_initial_build = False
    # 信号与成交统一在 T 日收盘：预测表 date=T 行由 T 日收盘特征算出，
    # 与本地回测（先计 T 日收益、再按 T 日收盘调仓）口径一致。
    run_daily(_check_rebalance, time="close")


def _load_predictions():
    """读预测值表 -> {date: DataFrame}；失败返回空字典（降级：不调仓）。"""
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
    if "credit_ok" not in raw.columns:
        log.warning("预测值表缺少信用排雷列，停止使用旧表")
        return {}
    raw["date"] = raw["date"].astype(str)
    table = {d: g for d, g in raw.groupby("date")}
    log.info("预测值表加载完成：%d 个调仓日，%d 行", len(table), len(raw))
    return table


# ---------------------------------------------------------------------------
# 3. 调仓触发：每月首个交易日
# ---------------------------------------------------------------------------
def _check_rebalance(context):
    today = context.current_dt.date()
    prior = context.previous_date
    if (prior.year, prior.month) == (today.year, today.month):
        return  # 本月已有交易日，今天不是本月第一个交易日
    key = (today.year, today.month)
    if g.last_rebalance_month == key:
        return
    if not ENABLE_REBALANCE and g.did_initial_build:
        return
    _rebalance(context, today)
    g.last_rebalance_month = key


# ---------------------------------------------------------------------------
# 4. 调仓主体
# ---------------------------------------------------------------------------
def _rebalance(context, today):
    board = _board_for(today)
    if board is None or board.empty:
        log.warning("当期预测值表缺失（%s），本期不调仓", today)
        return

    # 临时诊断：打印前 3 个可投池代码的 get_current_data 字段（定位 CB 停牌误判）
    current_data = get_current_data()
    sample_codes = board[board["pool_ok"] == 1]["symbol"].head(3).tolist()
    for code in sample_codes:
        try:
            unit = current_data[code]
            log.info("[诊断] %s: paused=%s, last_price=%s, day_open=%s, "
                     "high_limit=%s, low_limit=%s, name=%s",
                     code,
                     getattr(unit, "paused", "N/A"),
                     getattr(unit, "last_price", "N/A"),
                     getattr(unit, "day_open", "N/A"),
                     getattr(unit, "high_limit", "N/A"),
                     getattr(unit, "low_limit", "N/A"),
                     getattr(unit, "name", "N/A"))
        except Exception as e:
            log.warning("[诊断] %s: 访问失败 %s", code, e)

    eligible = board[_eligible_mask(board)].sort_values("double_low_value")
    pool_size = int(board["pool_ok"].sum())
    if eligible.empty:
        log.warning("本期无可投标的（可投池 %d 只）", pool_size)
        return

    n_keep = max(1, int(len(eligible) * TOP_QUANTILE))
    buy_codes = eligible.head(n_keep)["symbol"].tolist()
    keep_n = max(1, int(len(eligible) * HYSTERESIS_QUANTILE))
    keep_set = set(eligible.head(keep_n)["symbol"])
    held_in = [code for code in context.portfolio.positions
               if code in keep_set and code not in buy_codes]
    target_codes = buy_codes + held_in
    weight = min(1.0 / len(target_codes), MAX_WEIGHT) * COST_BUFFER
    log.info("本期：可投池 %d 只 -> 通过剔除 %d 只 -> 取 40%% 分位 %d 只，目标权重 %.3f%%",
             pool_size, len(eligible), len(target_codes), weight * 100)

    current_data = get_current_data()

    # 4.1 卖出不在目标内的持仓
    for code in list(context.portfolio.positions.keys()):
        position = context.portfolio.positions[code]
        if position.total_amount <= 0 or code in target_codes:
            continue
        try:
            unit = current_data[code]
        except Exception:
            log.warning("持仓代码在当前时点不可用，跳过卖出：%s", code)
            continue
        if _limit_down(unit):
            log.warning("跌停/无法卖出，顺延到下次调仓：%s", code)
            continue
        order_target_value(code, 0)

    # 4.2 买入/调整到目标权重
    value_each = context.portfolio.total_value * weight
    bought = 0
    for code in target_codes:
        try:
            unit = current_data[code]
        except Exception:
            log.warning("代码在当前时点不可用，跳过：%s", code)
            continue
        # CB 的 paused 字段不可靠（聚宽对 CB 返回真值），改用价格有效性判定
        if _is_suspended(unit):
            log.info("停牌跳过：%s", code)
            continue
        if SKIP_LIMIT_UP and _limit_up(unit):
            log.info("涨停跳过：%s", code)
            continue
        current = context.portfolio.positions.get(code)
        if current is not None and context.portfolio.total_value > 0:
            current_weight = current.value / context.portfolio.total_value
            if abs(current_weight - weight) <= WEIGHT_BAND:
                continue
        order_target_value(code, value_each)
        bought += 1

    g.did_initial_build = True
    log.info("本期下单 %d 只（目标 %d 只）", bought, len(target_codes))


# ---------------------------------------------------------------------------
# 5. 规则实现
# ---------------------------------------------------------------------------
def _board_for(today):
    """只接受当日的精确预测行；缺失则不下单（不使用过期信号）。"""
    return g.predictions.get(str(today))


def _eligible_mask(board):
    """规格书 7 条剔除规则（预测值表已带全部字段）。

    1/2. 强赎双重防御第一层：AkShare 快照（已公告强赎 / 计数 >= 12）-> redeem_exclude
    3.   代理规则：折价债（溢价率 <= 0）
    4.   代理规则：价格 > 130 且溢价率 < 2%
    5.   代理规则：距 conv_stop_date/delist_date < 30 日
    6.   可投池（流动性 >= 500 万）与价格下限 80 元
    7.   回售风险（最后两计息年度 + 正股价 < 转股价×75% + 转债价 > 103）
    """
    mask = (board["pool_ok"] == 1) & (board["redeem_exclude"] == 0) & (board["put_risk"] == 0)
    mask &= board["premium_rate"] > PREMIUM_FLOOR
    mask &= ~((board["cb_close"] > HIGH_PRICE) & (board["premium_rate"] < HIGH_PRICE_PREMIUM))
    mask &= board["days_to_stop"] >= MIN_DAYS_TO_STOP
    mask &= board["cb_close"] >= MIN_PRICE
    mask &= board["credit_ok"] == 1
    return mask


def _is_suspended(unit):
    """CB 停牌判定：paused 与价格双重检查。

    聚宽对 CB 的 paused 字段不可靠（实测全部返回真值），所以：
    - 价格完全缺失（last_price 与 day_open 都为 None）-> 视为停牌
    - paused=True 但价格有效 -> CB 字段缺失场景，不视为停牌
    - 其它情况 -> 用 paused 判断
    """
    paused = getattr(unit, "paused", False)
    last_price = getattr(unit, "last_price", None)
    day_open = getattr(unit, "day_open", None)
    if not last_price and not day_open:
        return True
    if paused and (last_price or day_open):
        return False
    return bool(paused)


def _limit_up(unit):
    price = getattr(unit, "day_open", None)
    if not price:
        price = getattr(unit, "last_price", None)
    high = getattr(unit, "high_limit", None)
    if not price or not high:
        return False
    return price >= high


def _limit_down(unit):
    price = getattr(unit, "day_open", None)
    if not price:
        price = getattr(unit, "last_price", None)
    low = getattr(unit, "low_limit", None)
    if not price or not low:
        return False
    return price <= low
