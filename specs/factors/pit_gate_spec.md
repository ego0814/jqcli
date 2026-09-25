# F-score PIT 门禁规格书

版本：v1
日期：2026-09-19
状态：已确认
上游：适配层产出的 panel
下游：F-score 因子有效性检验

## 一、背景

F-score 的 PIT 风险有三层，不是一层：

| 层 | 问什么 | 严重度 |
| --- | --- | --- |
| 数据层 | 财报何时可见 | 影响样本量 |
| 对齐层 | prior 期是否可见 | 影响 5 个组件 |
| 计算层 | 代码有无 lookahead | 影响全部 |

只处理数据层是不够的：财报可见不等于 prior 期可用，F3/F5/F6/F8/F9 依赖上一年度行，若 prior 期尚未公布却被使用，等于用了一年前的"未来数据"。

只处理计算层会漏掉 prior 期错配：截断不变性检验只看代码是否读了 T 之后的数据，无法发现"prior 期选了错误的那一行"。

三层必须分别有判据、分别有阴性对照，最后合成单一 PIT_STATUS。

## 二、数据层 PIT：PRACTICAL

判据：

对每个 rebalance 日 T：
只喂 `ann_date <= T` 的财报 → F-score 计算
与全量数据算出的 F-score 在 T 处必须一致

处理范围：

- 用 pubDate（`ann_date`）过滤
- 不处理 revision chain（同一报告期多次公告）
- 不处理 availability timestamp
- 状态标记：`PRACTICAL_PIT_APPLIED`

判定细节（对齐 `factors\f_score\asof_filter.py` 的现行实现）：

- 可见日 = `min(ann_date, f_ann_date)`，两者都缺 → 不可见
- 闭区间：`可见日 <= T`
- 失败关闭：不可见字段的 `status` 必须是 `UNAVAILABLE`、`value` 必须是 `None`，禁止插值或前向填充

## 三、对齐层 PIT：prior period 可见性

判据：

对每个 rebalance 日 T：
`resolve_prior_period` 返回的 `prior.row`
其 `ann_date` 必须 <= T
否则 F3/F5/F6/F8/F9 应为 UNAVAILABLE

现行实现（`factors\step5_6_fscore\prior_period_resolver.py`）：

1. `prior_annual_period(period_end_date)`：取当前报告期年份减一，拼成 `{year-1}-12-31`；无法解析则返回 None
2. 在 `rows_by_symbol_period[(symbol, prior_period)]` 中筛出 `rebalance_date <= T` 的候选
3. 候选按 `rebalance_date` 降序排序，取第一条作为 `prior.row`
4. 无候选 → `PriorPeriodResult(None, period, "PRIOR_PERIOD_NOT_VISIBLE")`

组件侧二次把关（`fscore_components._visible`）：字段必须 `status == "AVAILABLE"`、`value` 非空、且 `min(source_ann_date, source_f_ann_date) <= T`，三者缺一即该组件 `UNAVAILABLE`。

验证方法：

构造一组数据：当期财报已公布，prior 期财报未公布。

预期：

- F1/F2/F4 有值（它们只用当期）
- F3/F5/F6/F8/F9 = UNAVAILABLE
- `total_score_8 = None`
- `complete = False`

该用例已落地并通过：`factor_lab\tests\test_fscore_smoke.py` 用例 1 的"股票 C"分支（`prior.reason` 断言为 `PRIOR_PERIOD_NOT_VISIBLE`，5 个组件 `value=None` / `status=UNAVAILABLE`，`components_available=3`、`components_unavailable=5`、`incomplete_reasons=['F3','F5','F6','F8','F9']`）。

## 四、计算层 PIT：截断不变性

判据（F-score 专用版，不是原版）：

对每个 rebalance 日 T：
`data_truncated = panel[panel['rebalance_date'] <= T]`
`fscore_at_T_truncated = compute_fscore(data_truncated, T)`
`fscore_at_T_full = compute_fscore(panel, T)`
两者必须相等

原理：

如果 F-score 代码用了未来信息（比如 `shift(-1)`，或者用了 T 之后的数据），截断后结果会变。

比对口径：

- 逐组件比对 `value` 与 `status`（`AVAILABLE` 的 0/1 必须完全一致）
- 比对 `aggregated.total_score_8` 与 `complete`
- 比对 `F7_PROXY.value`
- 任何一项不等即判 FAIL，并记录首个差异的 (symbol, T, component)

与既有门禁的关系：`factor_lab\gates\pit_gate.py` 已经对 292 个技术因子实现了同一原理的截断不变性检验（截断点 K=452，样本 730 个交易日 × 150 只票，报告 `max_diff` 与 `nan_mismatch`）。F-score 版本需要把"截面宽表"换成"panel 行集合"，判据本身不变。该门禁的阴性对照目前 10/10 正确。

## 五、阴性对照

每层都要配阴性对照。

| 层 | 注入方式 | 门禁应表现 |
| --- | --- | --- |
| 数据层 | 把财报的 `ann_date` 改成早于实际公告日 | 门禁应抓出：标注该 T 为 DATA_PIT=FAIL，并列出被提前可见的字段 |
| 对齐层 | 把 prior period 换成未来的财报（例如把 `{year-1}-12-31` 行替换为 `{year}-12-31`） | 门禁应抓出：F3/F5/F6/F8/F9 在 T 处出现"应为 UNAVAILABLE 却有值"，判 ALIGNMENT_PIT=FAIL |
| 计算层 | 在 F-score 计算里注入 `shift(-1)`（或等价地用 T 之后的行参与 T 时刻的计算） | 门禁应抓出：截断后结果与全量结果不等，判 COMPUTE_PIT=FAIL |

阴性对照全部通过 → 门禁本身有效。

既有参照：`gates\pit_gate.py` 的 6 个 LEAK 注入（`LEAK_shift_neg5`、`LEAK_fullsample_zscore`、`LEAK_bfill_volume`、`LEAK_center_rolling`、`LEAK_future_return`、`LEAK_fullsample_rank`）全部被抓出，4 个 CLEAN 注入全部放行，10/10 正确，说明截断不变性这类判据在本项目里是可实现的。

## 六、PIT_STATUS 定义

汇总状态：

```text
DATA_PIT      = PRACTICAL
ALIGNMENT_PIT = CHECKED
COMPUTE_PIT   = TRUNCATION_INVARIANCE_PASSED
PIT_STATUS    = PRACTICAL_VALIDATED
```

任何一层 FAIL → `PIT_STATUS = FAIL`。

状态取值集合：`PRACTICAL_VALIDATED` / `FAIL`。分层状态取值集合：`PRACTICAL` / `CHECKED` / `TRUNCATION_INVARIANCE_PASSED` / `FAIL` / `NA`（该层无适用样本时）。

## 七、验证输出格式

对每个 rebalance 日 T，输出：

| T | DATA_PIT | ALIGNMENT_PIT | COMPUTE_PIT | 状态 |
| --- | --- | --- | --- | --- |

汇总：

- 通过日数 / 总日数
- 失败明细（每条含：T、层、symbol、组件名、预期、实际、证据行号）

落盘建议：`factor_lab\output\pit_gate\fscore_pit_report.csv`（逐日）+ `fscore_pit_summary.json`（汇总）。两者均为纯输出产物，不参与计算。

## 八、已知未处理

- 财报修正链（同一报告期多次公告，只记录不选边）
- 财报单位不一致（万股 / 股、百分数 / 小数）
- 部分股票数据缺失（NaN 语义不区分"未披露"与"科目不存在"）

这些属于 STRICT PIT 范围，本规格书采用 PRACTICAL 标准。

## 九、与适配层的关系

- 适配层负责产出 panel（数据层 PIT 已应用）：字段映射、ASOF 过滤、ST 过滤。
- PIT 门禁负责验证 panel 和 F-score 实现（三层一起验证）。
- 两者通过 `panel_schema` 定义的格式对接：`PANEL_FIELDS`（19 字段）、`FIELD_KEYS`（8 键）、行级 10 个必需键。
- 适配层的 `asof` 段落（`asof_cutoff` / `asof_eligible` / `announcement_date` / `availability_date`）是门禁做数据层判定的**唯一输入**；门禁不重新解析 Tushare 原始字段。
- ST 过滤同理：门禁只读取适配层产出的 `row_status` 与 `drop_reasons`，不重新读 `st_mask.parquet`。

## 十、设计点确认（2026-09-19）

| # | 设计点 | 确认结果 |
|---|---|---|
| 1 | 数据层可见日 | f_ann_date 优先，缺失退回 ann_date（闭区间 `<=`） |
| 2 | pit_status 取值 | `PRACTICAL_PIT_APPLIED`（默认）/ `STRICT_PIT_APPLIED`；任一层 FAIL → `PIT_STATUS=FAIL` |
| 3 | partial_score | 行级可选键，值 = 可用项得分 / 可用项数；`complete=False` 且有可用项时由 F-score 层写入 |
| 4 | ST 行 | 保留为 DROPPED 且带 `drop_reasons`，门禁按 `row_status` 过滤，不重新读 st_mask |
| 5 | 常量硬编码 | 已参数化（`expected_row_count` / `expected_drop_count`，默认 None 不检查），门禁可对任意规模面板生效 |
| 6 | 2026 半年度 | 2026 年只有 Q1/Q2 两期（下载截止 2026-09-18），门禁对缺期不判 FAIL，按"该期尚未公告"处理 |

以上已落实到代码：`panel_schema.py`（pit_status / partial_score 校验）、`asof_filter.py`（可见日优先级）、`fscore_panel_builder.py`（常量参数化 + pit_status / partial_score 输出）。