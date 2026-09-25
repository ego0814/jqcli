# ETF 池等权 · 实盘执行清单

策略：**7 只 ETF 等权**（`510500 / 512800 / 513100 / 513050 / 518880 / 162411 / 511260`），
**季度再平衡**（1/4/7/10 月首个交易日），**单只上限 20%**。再平衡贡献 −0.18pp/年（几乎为 0）。
状态：已通过六阶段（**资产配置型**）；ETF 端唯一部署策略。
信号脚本：`factor_lab/data/quarterly_etf_signal.py`

## 触发时间

- 每季度首个交易日（1/4/7/10 月）
- 当日 15:00 收盘后，跑信号脚本
- 次日开盘执行

## 信号生成

```powershell
cd D:\project\jqcli
factor_lab\.venv\Scripts\python.exe factor_lab\data\quarterly_etf_signal.py --capital 100000 --date YYYY-MM-DD
```

> 必须用 **`factor_lab\.venv`** 的解释器：脚本依赖 pandas，而仓库根的 `.venv` 没装 pandas
> （实测 `.\.venv\Scripts\python.exe ...` 会直接 `ModuleNotFoundError: No module named pandas`）。
> `run_monthly.ps1` 用的也是 `factor_lab\.venv\Scripts\python.exe`，口径一致。

- `--date` 填**季度首个交易日**；脚本实际使用「≤ 该日、且 7 只都有数据的最近交易日」，
  并在日志里打印实际信号日（数据未刷新到该日时不会硬失败）
- 输出：`factor_lab/output/monthly_signals/etf_pool_<实际信号日>.csv`
- 列：`code, name, current_shares, target_shares, delta_shares, price, target_value, target_weight`
- 首次建仓省略 `--current-holdings`（默认从零建仓）；之后传入持仓 CSV（列 `code,shares`）

### ⚠️ 价格口径（务必不要改错）

脚本从 `etf_extended_daily.parquet` 读**前复权**价，再按 `raw(t) = close(t) × adj_last / adj(t)`
还原成**可成交价**。在文件最后一个交易日上 `adj == adj_last`，还原式退化为 `raw = close`。

**不要改用 `etf_daily.parquet` 的 close 当价格** —— 已用 Tushare 不复权价逐只验证它不是可成交价：

| 标的 | Tushare 不复权 | extended | etf_daily |
|---|---|---|---|
| 510500 | 7.881 | **7.8810** ✅ | 2.6803 ✗（×0.3401） |
| 513100 | 2.233 | **2.2330** ✅ | 11.1692 ✗（×5.0019） |
| 512800 | 0.846 | **0.8460** ✅ | 1.6911 ✗（×1.9989） |
| 511260 | 135.783 | 134.5046（qfq） | 140.0059 ✗ |

用错会直接把股数算错（510500 会多买约 3 倍、513100 会少买约 5 倍）。

## 下单流程

- 时间：次日 **09:30 开盘**
- 方式：市价单（ETF 流动性好，市价可接受）
- 顺序：**先卖后买**（释放资金）
- 检查：确认成交价与信号价的偏差

## 台账记录

- 文件：`factor_lab/output/monthly_signals/etf_pool_TRACKING.md`
- 每季度记录：日期、信号 CSV、实际成交价、滑点、持仓权重

## 停牌/涨跌停处理

- 如果某只 ETF 停牌：保留原仓位，不调
- 如果某只 ETF 涨跌停：延迟到下一交易日

## 511260 粒度问题

- 511260 是池内唯一"高价"标的：**实测 2026-09-18 单价 134.739 元，1 手 = 13,473.9 元**
- 10 万本金下目标金额 14,285.7 元 → **只能买 1 手（100 股）**
- 处理方式：**接受权重偏差**，或把偏差分摊给其他 6 只（加 `--redistribute`）
- 信号脚本每次都会打印该标的的「单价 / 1 手金额 / 目标金额 / 实际股数 / 实际权重 / 偏差pp」
- **实测偏差 −0.81pp**（实际 13.47% vs 目标 14.29%），远低于 10pp 关注线
- 池内其他 6 只单价 0.8~9 元，1 手仅 80~900 元，不存在粒度问题

## 季度检查

- 每季度末检查台账：累计滑点、累计偏差
- 如果偏差持续 > 5%，考虑替代方案

## 首次运行记录（2026-09-26 模拟，未下单）

- 命令：`quarterly_etf_signal.py --capital 100000 --date 2026-09-30`
- 实际信号日：**2026-09-18**（请求日数据尚未覆盖）
- 从零建仓：总买入 **+97,833 元**，无卖出，剩余现金 2,166 元（2.17%）
- 输出：`factor_lab/output/monthly_signals/etf_pool_20260918.csv`
