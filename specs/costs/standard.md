# 标准交易成本契约

版本：v1.2
日期：2026-09-16
适用范围：所有在 jqcli + 聚宽环境回测的 A 股 + ETF 策略

## 一、来源

本契约对齐 Quant Lab Phase 5 TransactionCostModel，作为 Quant Lab 和 jqcli 两边共用
的成本口径。任何策略规格书不单独定义成本，统一引用本文件。

## 二、费用明细

| 项目 | 值 | 说明 |
|---|---|---|
| Commission（佣金） | 0.0000213 | 万分之 0.213 |
| Handling fee（经手费） | 0.0000341 | 万分之 0.341 |
| Regulatory fee（证管费） | 0.0000200 | 万分之 0.2 |
| Transfer fee（过户费） | 0.0000100 | 万分之 0.1 |
| 合并佣金率 | 0.0000854 | 上述四项加总，用于聚宽 set_order_cost |
| Sell stamp duty（印花税） | 0.0005 | 千分之 0.5，仅卖出 |
| Minimum commission | 5 CNY | 每笔最低 5 元 |
| Slippage | 0.0005 | 千分之 0.5，单边 |
| Lot size | 100 股 | 一手 |
| Volume cap | 10% | 单笔不超过日成交量 10% |

## 三、聚宽映射

聚宽 set_order_cost 不支持分项费用，需合并：

```python
set_order_cost(OrderCost(
    open_tax=0,
    close_tax=0.0005,
    open_commission=0.0000854,
    close_commission=0.0000854,
    min_commission=5,
), type="stock")
set_slippage(PriceRelatedSlippage(0.0005))
```

> 实际券商费率见 `broker_actual.md`（合并万1）。本契约万0.854 偏乐观
> 约 17%；股票端集中持仓路径已排除（EP/reversal 均不部署），对
> 部署结论无实际影响。

## 四、聚宽无法直接实现的部分

1. Volume cap 10%：聚宽不自动执行，需要策略代码手动检查单笔下单不超过
   日成交量 10%。策略若未实现，需在规格书注明“volume cap 未实现”。

2. 印花税分段：A股印花税在 2023-08-28 从 0.001 下调至 0.0005。聚宽
   set_order_cost 是全局设置，无法按日期动态改。所有跨越该日期的回测，
   统一用 0.0005，需在规格书注明“2023-08-28 前印花税被低估 0.0005”。

## 五、版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-09-16 | 初始版本，对齐 Quant Lab Phase 5 |
| v1.1 | 2026-09-21 | 增补 ETF 成本口径（免印花税、type="fund"） |
| v1.2 | 2026-09-21 | 新增 CB（可转债）成本口径：保守值（万2佣金 + 最低1元 + 0.2% 滑点） |

## ETF 成本口径

ETF 与股票成本差异：

- 印花税：ETF 免印花税（close_tax = 0）
- 佣金：与股票同（0.0000854）
- 最低佣金：5 元
- 滑点：与股票同（0.0005）

聚宽设置：

```python
set_order_cost(OrderCost(
    open_tax=0,
    close_tax=0,
    open_commission=0.0000854,
    close_commission=0.0000854,
    min_commission=5,
), type="fund")
```

注意：type="fund" 用于 ETF/LOF；type="stock" 用于股票。

> 实际券商费率见 `broker_actual.md`（万0.5）。本契约万0.854 偏保守，
> ETF 池等权历史回测按契约值跑，数字方向偏保守。

## CB（可转债）成本口径

CB 与股票/ETF 的差异（**保守口径**，2026-09-21 定档）：

- 印花税：免（close_tax = 0）
- 佣金：0.0002（万 2）
- 最低佣金：1 元
- 滑点：0.002（千分之 2，单边）

聚宽设置：

```python
set_order_cost(OrderCost(
    open_tax=0,
    close_tax=0,
    open_commission=0.0002,
    close_commission=0.0002,
    min_commission=1,
), type="fund")
set_slippage(PriceRelatedSlippage(0.002))
```

理由：双低策略选出的转债多为中小盘，实际滑点高于大盘转债与 ETF，
0.1% 的乐观口径会低估成本。保守口径下月度调仓年成本约 5.3%，
作为真实成本上限使用；阶段 4 会做乐观/中性/保守/极端四情景敏感性测试。

> 实际券商费率见 `broker_actual.md`（万0.5 / 沪1 / 深0）。本契约值
> 仍作为保守上限保留——双低策略选出的转债多为中小盘，实际滑点可能
> 高于大盘转债，保守值用于压力测试。

注：`type="fund"` 用于 ETF/LOF/可转债（聚宽不提供单独的 bond 类型）。

### 佣金最低值按市场区分（2026-09-21 修订）

- 沪市（11 开头）：最低 **1 元**
- 深市（12 开头）：**0（无最低）**

**聚宽 `set_order_cost` 不支持按代码区分最低佣金**，回测时需在策略代码里手工处理，
或在本地回测中按代码区分（逐笔 `max(成交额 × 佣金率, 该市场最低佣金)`）。

影响（CB 双低策略实测，样本外保守情景）：统一按 1 元最低佣金会低估收益 0.52pp/年
（+8.86% → +9.39%）；极端情景（最低 5 元）低估 2.38pp/年（+4.65% → +7.03%）。
