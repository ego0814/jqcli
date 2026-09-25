# -*- coding: utf-8 -*-
"""ETF 池等权策略 v1

规格书：specs/etf_pool_v1.md（v1.1，九维度已确认）
成本契约：specs/costs/standard.md（v1.1，ETF 段：免印花税、type="fund"）

逻辑：等权持有 7 只低相关 ETF，每季度首个交易日再平衡，不做轮动。
执行：调仓日开盘下单调仓；QDII 溢价过滤用 T-1 收盘价与 T-1 单位净值（避免前视）。
"""

# ---------------------------------------------------------------------------
# 1. 常量（全部来自规格书，修改任何一项都必须同步规格书）
# ---------------------------------------------------------------------------
POOL = [
    "510500.XSHG",  # 中证500
    "512800.XSHG",  # 银行
    "513100.XSHG",  # 纳指（QDII）
    "513050.XSHG",  # 中概互联（QDII）
    "518880.XSHG",  # 黄金
    "162411.XSHE",  # 华宝油气（QDII）
    "511260.XSHG",  # 十年国债
]
QDII = ("513100.XSHG", "513050.XSHG", "162411.XSHE")

REBALANCE_MONTHS = (1, 4, 7, 10)     # 每季度首个交易日
TARGET_WEIGHT = 1.0 / len(POOL)      # 等权 14.29%
MAX_WEIGHT = 0.20                    # 单标的权重上限，超出部分持现金
PREMIUM_LIMIT = 0.02                 # QDII 溢价上限（T-1 口径）
MAX_QDII_SKIP = 1                    # 单期最多跳过 1 只 QDII

COMMISSION = 0.0000854               # 与股票同（standard.md）
MIN_COMMISSION = 5                   # 每笔最低 5 元
SLIPPAGE = 0.0005                    # 单边滑点

# 对照开关（规格书"三、回测契约"：对照 = 等权池买入持有）
# True  = 每季度再平衡（主策略）
# False = 只在首个可调仓日建仓，之后不再调仓（阶段 4 的"买入持有"对照组合）
ENABLE_REBALANCE = True

# 规格书未要求、但按 AGENTS.md 反模式清单（未过滤涨跌停）补的防御项：
# 涨停时不下买单，避免产生"委托失败"噪音日志。若与规格书冲突，以规格书为准。
SKIP_LIMIT_UP = True

BENCHMARK = "000985.XSHG"            # 聚宽平台展示用基准（契约对照见规格书）


# ---------------------------------------------------------------------------
# 2. 初始化
# ---------------------------------------------------------------------------
def initialize(context):
    set_benchmark(BENCHMARK)
    set_option("use_real_price", True)
    # ETF 口径：免印花税（close_tax=0），type="fund"
    # 依据：specs/costs/standard.md v1.1「ETF 成本口径」
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0,
        open_commission=COMMISSION,
        close_commission=COMMISSION,
        min_commission=MIN_COMMISSION,
    ), type="fund")
    set_slippage(PriceRelatedSlippage(SLIPPAGE))

    g.last_rebalance_month = None
    g.did_initial_build = False
    run_daily(_check_rebalance, time="open")


# ---------------------------------------------------------------------------
# 3. 调仓触发：每季度首个交易日
# ---------------------------------------------------------------------------
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
    if not ENABLE_REBALANCE and g.did_initial_build:
        return  # 买入持有对照：只建仓一次
    g.last_rebalance_month = key
    _rebalance(context)


# ---------------------------------------------------------------------------
# 4. 调仓主体
# ---------------------------------------------------------------------------
def _rebalance(context):
    current_data = get_current_data()
    available = []
    missing = []
    for code in POOL:
        try:
            unit = current_data[code]
        except Exception:
            log.warning("标的在当前时点不可用，视为数据缺失：%s", code)
            missing.append(code)
            continue
        if getattr(unit, "paused", False):
            log.warning("标的停牌，视为数据缺失：%s", code)
            missing.append(code)
            continue
        available.append(code)
    if not available:
        log.warning("全部 ETF 无可用数据，本期不调仓")
        return
    if missing:
        log.info("数据缺失跳过（权重重分摊给其它）：%s", ",".join(missing))

    # QDII 溢价过滤：T-1 收盘价 vs T-1 单位净值，单期最多跳过 MAX_QDII_SKIP 只
    premium_skipped = []
    for code in available:
        if code not in QDII:
            continue
        if len(premium_skipped) >= MAX_QDII_SKIP:
            break
        premium = _premium_t1(context, code)
        if premium is None:
            log.warning("无法取得 %s 的 T-1 净值，本期该标的不做溢价过滤", code)
            continue
        if premium > PREMIUM_LIMIT:
            premium_skipped.append(code)
            log.info("%s T-1 溢价 %.2f%% > %.0f%%，本期跳过买入（该部分持现金）",
                     code, premium * 100, PREMIUM_LIMIT * 100)

    investable = [c for c in available if c not in premium_skipped]
    if not investable:
        log.warning("本期全部可投标的均被跳过，保持现金")
        return

    # 数据缺失的权重在剩余标的间分摊；溢价跳过的权重持现金（不参与分摊）
    # 乘 0.995 留 0.5% 现金缓冲：7 个买单合计 100% 市值时，
    # 佣金+滑点会导致最后 1-2 笔资金不足（编译记录 63333109 有 5 次）
    weight = min(1.0 / len(available), MAX_WEIGHT) * 0.995
    log.info("本期目标权重 %.2f%%（可投 %d 只 / 溢价跳过 %d 只 / 数据缺失 %d 只）",
             weight * 100, len(investable), len(premium_skipped), len(missing))

    # 4.1 卖出：不在目标内的持仓（含被溢价跳过的 QDII、以及已剔除的标的）
    for code in list(context.portfolio.positions.keys()):
        position = context.portfolio.positions[code]
        if position.total_amount <= 0 or code in investable:
            continue
        try:
            unit = current_data[code]
        except Exception:
            log.warning("持仓标的不可用，跳过卖出：%s", code)
            continue
        if _limit_down(unit):
            log.warning("跌停无法卖出，顺延到下次调仓：%s", code)
            continue
        order_target_value(code, 0)

    # 4.2 买入/调整到目标权重
    # 下单 API：用 order_target_value，本回测环境未提供 order_target_percent
    # （编译记录 63332982 的 NameError 证据），目标市值 = 总资产 × 目标权重
    value_each = context.portfolio.total_value * weight
    for code in investable:
        try:
            unit = current_data[code]
        except Exception:
            log.warning("标的下单前不可用，跳过：%s", code)
            continue
        if SKIP_LIMIT_UP and _limit_up(unit):
            log.warning("涨停无法买入，跳过：%s", code)
            continue
        order_target_value(code, value_each)

    g.did_initial_build = True


# ---------------------------------------------------------------------------
# 5. 工具函数
# ---------------------------------------------------------------------------
def _premium_t1(context, code):
    """T-1 溢价率 = (T-1 收盘价 - T-1 单位净值) / T-1 单位净值；取不到返回 None。

    收盘价用不复权口径（单位净值是面值口径，前复权价会被分红调整，不能直接比）。
    """
    prev = context.previous_date
    prev_close = _prev_close_unadjusted(code)
    prev_nav = _prev_unit_nav(code, prev)
    if prev_close is None or prev_nav is None:
        return None
    return (prev_close - prev_nav) / prev_nav


def _prev_close_unadjusted(code):
    try:
        hist = attribute_history(code, 1, "1d", ["close"], df=False, fq=None)
    except Exception:
        return None
    closes = hist.get("close") if isinstance(hist, dict) else None
    if closes is None or len(closes) == 0:
        return None
    value = float(closes[-1])
    if value != value or value <= 0:
        return None
    return value


def _prev_unit_nav(code, prev_date):
    try:
        nav = get_extras("unit_net_value", [code], start_date=prev_date, end_date=prev_date, df=True)
    except Exception:
        return None
    if nav is None or len(nav) == 0 or code not in nav.columns:
        return None
    series = nav[code].dropna()
    if len(series) == 0:
        return None
    value = float(series.iloc[-1])
    if value != value or value <= 0:
        return None
    return value


def _limit_up(unit):
    price = getattr(unit, "day_open", None)
    if not price:
        price = getattr(unit, "last_price", None)  # 回退
    high = getattr(unit, "high_limit", None)
    if not price or not high:
        return False
    return price >= high


def _limit_down(unit):
    price = getattr(unit, "day_open", None)
    if not price:
        price = getattr(unit, "last_price", None)  # 回退
    low = getattr(unit, "low_limit", None)
    if not price or not low:
        return False
    return price <= low
