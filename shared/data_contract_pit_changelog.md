# 数据契约变更记录（PIT / ASOF）

本文件记录会影响历史结论可复现性的 PIT 语义变更。变更后必须重算对应结论，
不能直接沿用旧的 IC / 回测数字。

## 2026-09-25 · 股票可交易性筛选改为 PIT

**变更文件**：`factor_lab/analysis/tradability.py`

**旧行为（存在前视偏差）**

- `windows()` 返回 T+1 起的 lookback_days 个交易日。
- `filter_tradable()` 用"下期首日涨停 / 下期末日跌停 / 下期停牌天数 / 下期日均成交额"
  决定某个 (T, symbol) 是否进入 IC 样本。
- 后果：`compute_ic_processed.py` 的 `6_mv+industry+tradable` 配置在计算 T 日 IC 之前
  已经使用了 T+1 之后的信息筛样本，IC / ICIR 被系统性高估。

**新行为（只使用 T 日及之前）**

- `windows()` 改为 T 日及之前 lookback_days 个交易日（含 T）。
- 剔除原因：`NO_WINDOW` / `NO_PRICE_T` / `SUSPENDED_T` / `SUSPEND_HISTORY` / `ILLIQUID`。
- 窗口内没有行情行的日子按未观测处理并计入停牌天数，因此新上市标的在攒满完整
  窗口前不进入研究样本。
- 执行可行性诊断（T+1 涨停买不进 / 期末跌停卖不出）仍保留向前看语义，改由新增的
  `future_windows()` 提供，仅供 `tradability_audit()` 使用。

**需要重算的结论**

- `factor_lab/output/` 下所有 `6_mv+industry+tradable` 配置的 IC / ICIR / t。
- 以该配置作为最终入选依据的因子结论（AGENTS.md 中"可交易性过滤默认开"相关数字）。
- EP、reversal 的分位扫描与可投池超额结论若经过该过滤，需一并复核。

**未受影响**

- 涨跌停阈值口径（含 ST 5% 分支）不变。
- CB 路径不调用本函数，走 `apply_pipeline` 的 `asset == "cb"` 分支。
- 本地 CB 回测与 ETF 回测不依赖本函数。

**验证**

- `python factor_lab/analysis/tradability.py` → 38 项自测，ALL PASS。
- 回归用例：T+1 涨停的标的在 PIT 口径下必须仍保留在样本内。
- 集成冒烟：`apply_pipeline` 的股票 PIT 分支与 CB 分支均按预期保留 A.SZ。