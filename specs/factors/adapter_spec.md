# 适配层规格书

版本：v2
日期：2026-09-19
状态：已确认
上游：Tushare 缓存（factor_lab\data\cache\）
下游：F-score 组件（factor_lab\factors\f_score\）

## 一、目标

把 Tushare 缓存数据转换为 panel_schema 定义的格式，供 F-score 组件消费。

本规格书只管**数据层 PIT**（字段映射 + ASOF 过滤 + ST 过滤）。计算层和对齐层的 PIT 见 pit_gate_spec.md。

范围之外：不改动 F-score 计算逻辑，不做财报 revision chain 对齐，不做因子有效性检验。

## 二、输入

缓存目录：`factor_lab\data\cache\`。以下为 2026-09-19 实测 schema（用 pyarrow 只读元数据与首行组，未读全量）。

| 文件 | 行数 | 列数 | 关键列 | 日期范围 |
|---|---:|---:|---|---|
| `px_YYYY.parquet` | 1293893（2024） | 11 | ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount | 20240102 ~ 20241231 |
| `basic_YYYY.parquet` | 1293893（2024） | 18 | ts_code, trade_date, close, turnover_rate, turnover_rate_f, volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_share, float_share, free_share, total_mv, circ_mv | 20240102 ~ 20241231 |
| `namechange.parquet` | 14215 | 6 | ts_code, name, start_date, end_date, ann_date, change_reason | 19901201 ~ 20260922 |
| `st_mask.parquet` | 2848 | 5824 | 布尔矩阵；index = trade_date（`__index_level_0__`），columns = 5823 个 ts_code | 20150105 ~ 20260918 |
| `stock_basic.parquet` | 5904 | 10 | ts_code, symbol, name, area, industry, cnspell, market, list_date, act_name, act_ent_type | — |
| `trade_cal.parquet` | 4279 | 4 | exchange, cal_date, is_open, pretrade_date | — |

**关键结论：缓存只覆盖行情与估值，不含任何财报三表字段。** 19 个 panel 字段中只有 1 个（`total_share`）能从现有缓存直接取得，其余 18 个必须新增下载 Tushare 财报接口（见第三节）。

## 三、字段映射表

单位说明：`元` = 人民币元；`%` = 百分数；`倍` = 无量纲比率。

| Tushare 源字段 | panel 字段 | 单位转换 | 缺失值处理 | status 赋值规则 |
|---|---|---|---|---|
| `income.n_income`（未下载） | n_income | 元 → 元（不转换） | NaN → value=None | 非空且公告日 ≤ T → AVAILABLE；NaN → UNAVAILABLE |
| `cashflow.n_cashflow_act`（未下载） | n_cashflow_act | 元 → 元 | 同上 | 同上 |
| `income.oper_cost`（未下载） | oper_cost | 元 → 元 | 同上 | 同上 |
| `income.revenue`（未下载） | revenue | 元 → 元 | 同上 | 同上 |
| `income.total_revenue`（未下载） | total_revenue | 元 → 元 | 同上 | 同上 |
| `balancesheet.total_assets`（未下载） | total_assets | 元 → 元 | 同上 | 同上 |
| `balancesheet.total_liab`（未下载） | total_liab | 元 → 元 | 同上 | 同上 |
| `balancesheet.total_cur_assets`（未下载） | total_cur_assets | 元 → 元 | 同上 | 同上 |
| `balancesheet.total_cur_liab`（未下载） | total_cur_liab | 元 → 元 | 同上 | 同上 |
| `basic_YYYY.total_share`（**已在缓存**）或 `balancesheet.total_share` | total_share | **待确认**：daily_basic 为万股，balancesheet 口径可能为股 | NaN → value=None（不做 ffill） | 非空 → AVAILABLE；与 `balancesheet.total_share` 不一致 → CONFLICT |
| `fina_indicator.roe`（未下载） | roe | % → %（保留百分数） | NaN → value=None | 非空且公告日 ≤ T → AVAILABLE |
| `fina_indicator.roa`（未下载） | roa | % → %（保留百分数） | 同上 | 同上 |
| `fina_indicator.roe_waa`（未下载） | roe_waa | % → % | 同上 | 同上 |
| `fina_indicator.netprofit_margin`（未下载） | netprofit_margin | % → % | 同上 | 同上 |
| `fina_indicator.grossprofit_margin`（未下载） | grossprofit_margin | % → % | 同上 | 同上 |
| `fina_indicator.assets_turn`（未下载） | assets_turn | 倍 → 倍（不转换） | 同上 | 同上 |
| `fina_indicator.eps`（未下载） | eps | 元 → 元 | 同上 | 同上 |
| `fina_indicator.bps`（未下载） | bps | 元 → 元 | 同上 | 同上 |
| `fina_indicator.netprofit_yoy`（未下载） | netprofit_yoy | % → % | 同上 | 同上 |

字段名与 Tushare 财报接口一一同名，`panel_schema.PANEL_FIELDS` 的 19 个名字恰好等于 `income`(4) + `balancesheet`(5) + `cashflow`(1) + `fina_indicator`(9) 的字段并集。

单位证据（来自代码，不是猜测）：`fscore_panel_builder._reference_checks` 把 `roa` 与 `grossprofit_margin` 按**百分数**处理（除以 100 后再比较），而 `assets_turn` 按**无量纲比值**处理（不除 100）：
`{"F3": ("roa", ..., True), "F8": ("grossprofit_margin", None, True), "F9": ("assets_turn", ..., False)}`。适配层必须与此口径一致，否则 `cross_check_divergence_count` 会误报。

## 四、FIELD_KEYS 映射

`panel_schema.FIELD_KEYS` 共 8 个键，19 个字段每个都必须全部出现：

| FIELD_KEYS | 填写规则 |
|---|---|
| `value` | 源值；缺失或冲突时为 None |
| `status` | `AVAILABLE` / `UNAVAILABLE` / `CONFLICT`（仅这三个值合法） |
| `unit` | `CNY`（金额）、`SHARE`（股本）、`PERCENT`（比率类，保留百分数）、`RATIO`（assets_turn） |
| `unit_status` | Tushare 文档明确 → `CONFIRMED`；需换算或口径存疑 → `INFERRED_WITH_EVIDENCE`；无法判定 → `UNKNOWN` |
| `source_row_identity` | 形如 `{ts_code}|{period_end_date}|{api}|{field}` 的确定性字符串，用于跨源比对与溯源 |
| `source_update_flag` | 同报告期多次公告时标记 `ORIGINAL` / `UPDATED`（当前 PRACTICAL 范围只记录，不做 revision 选择） |
| `source_ann_date` | Tushare `ann_date`（`YYYYMMDD` 或 `YYYY-MM-DD` 均可，下游会归一化） |
| `source_f_ann_date` | Tushare `f_ann_date`（实际公告日）；缺失时与 `source_ann_date` 同值 |

## 五、行级结构

按 `panel_schema.validate_row` 的 10 个必需键：

| 行级键 | 类型 | 填写规则 |
|---|---|---|
| `symbol` | str | Tushare `ts_code` |
| `rebalance_date` | str | 调仓日 T；必须 ≥ 该行财报的可见日 |
| `period_kind` | str | 财报期类型，年报用 `ANNUAL` |
| `period_end_date` | str | 报告期结束日（如 `2023-12-31`） |
| `financial` | Mapping | 19 个字段，每字段 8 个 FIELD_KEYS |
| `price` | Mapping | `close_raw` ← `px_YYYY.close`（**不复权**原始价）；`close_raw_date` ← 对应 `trade_date` |
| `asof` | Mapping | `asof_cutoff` = `rebalance_date`；`asof_eligible` = 布尔；`announcement_date` / `availability_date` = 可见日 |
| `row_status` | str | `OK` 或 `DROPPED` |
| `drop_reasons` | list | 丢弃原因码列表，通过时为 `[]` |
| `fail_closed_conditions` | list | 触发失败关闭的条件列表，通过时为 `[]` |

## 六、ASOF 过滤（数据层 PIT）

实现位置：`factors\f_score\asof_filter.py`。现行规则如下，**按现状记录**：

| 问题 | 现行实现 |
|---|---|
| 用 ann_date 还是 f_ann_date | 两者都取，且取 **min（较早者）** |
| 取 min 还是 max | **min** |
| 闭区间还是开区间 | **闭区间**（`available <= cutoff`） |
| 缺公告日 | `visible_date` 返回 None → 判为不可见，`asof_reason = MISSING_ANNOUNCEMENT_DATE` |
| 不可见 | `asof_reason = ASOF_INELIGIBLE` |
| 日期格式 | 先做 `YYYYMMDD` → `YYYY-MM-DD` 归一化；`None`/空串/`"None"`/`"nan"` 视为缺失 |

12 个 ASOF 受限期：**已确认按单点判定处理**，不存在 12 期枚举。每个调仓日 T 上，财报只有"可见 / 不可见"两种状态：可见日（f_ann_date 优先，缺失退回 ann_date）<= T 即可见，否则整行不产出（失败关闭，不做插值）。

## 七、ST 过滤（数据层 PIT 的一部分）

| 问题 | 规定 |
|---|---|
| 数据来源 | `st_mask.parquet`（已落地的布尔矩阵，index = trade_date，columns = ts_code）；`namechange.parquet` 是其**上游原始来源**，不是策略期的过滤依据 |
| 逐日还原方式 | 以 `(rebalance_date, symbol)` 直接索引 st_mask；为 True 视为当日 ST 状态成立 |
| 在哪一步过滤 | 在 panel 行组装完成、写盘之前 |
| 过滤后形态 | 标记 `row_status = "DROPPED"` 且 `drop_reasons = ["ST"]`，**不物理剔除** |
| 为什么保留 | 下游统计依赖丢弃计数（builder 会核验上游 `SAME_KEY_DIFFERENT_VALUE` 丢弃数 = 617），物理删除会让该核验失效 |
| namechange 的作用 | 校验用：用 `start_date`/`end_date`/`ann_date` 重建 ST 区间，与 st_mask 抽查比对；不一致时以 st_mask 为准并记录 |

## 八、已知风险

- **单位不一致**：`total_share` 在 daily_basic 是万股、在 balancesheet 可能是股；比率类字段是百分数还是小数，若与 `_reference_checks` 的口径不一致会造成 `divergence_count` 误报。
- **缺失值语义不明**：Tushare 的 NaN 可能是"未披露"也可能是"该科目不存在"（如银行无 oper_cost），当前一律按 UNAVAILABLE 处理，不做区分。
- **财报修正未处理**：同一报告期多次公告时只记录 `source_update_flag`，不选边、不回溯替换（PRACTICAL 范围之外）。
- **股票池覆盖不全**：st_mask 覆盖 20150105 起，px/basic 按年分片；2015 年之前的回测区间无数据支撑。
- **ASOF 取 min 偏宽松**：取较早公告日会让数据更早可见，理论上存在轻微前视风险（见待确认项）。

## 九、验证方法

1. **最小样本验证**：取 1 只股票、2 期财报（prior = 2022-12-31，current = 2023-12-31），手工构造期望值，断言适配层产物能通过 `panel_schema.validate_row` 且字段值与手工表一致。
2. **三层门禁验证**：按 `pit_gate_spec.md` 的数据层 / 对齐层 / 计算层判据逐层验证，三层全过才允许进入因子有效性检验。
3. **回归对照**：适配层重构后，同一批 (symbol, rebalance_date) 的 panel 行必须与重构前逐字段一致，否则视为破坏性变更。

## 十、设计点确认（2026-09-19）

| # | 设计点 | 确认结果 |
|---|---|---|
| 1 | ASOF 可见日 | **f_ann_date 优先，缺失退回 ann_date**（原 min 规则已废弃） |
| 2 | pit_status 行级键 | 新增，允许值 `PRACTICAL_PIT_APPLIED` / `STRICT_PIT_APPLIED`，默认 `PRACTICAL_PIT_APPLIED` |
| 3 | partial_score | 新增可选行级键；值 = 可用项得分 / 可用项数；用于银行股等部分可用诊断 |
| 4 | total_share 权威源 | **balancesheet.total_share**（股），不使用 daily_basic 的万股口径 |
| 5 | 比率字段口径 | roe/roa/roe_waa/netprofit_margin/grossprofit_margin/netprofit_yoy 保留百分数；assets_turn 为无量纲比值 |
| 6 | 金额单位 | 元（CNY），不做换算 |
| 7 | unit_status | 统一 `CONFIRMED` |
| 8 | source_row_identity | 格式 `{ts_code}*{end_date}*{interface}` |
| 9 | source_update_flag | 空字符串 |
| 10 | ST 处理 | 保留为 `row_status=DROPPED` + `drop_reasons=["ST"]`，不物理剔除 |
| 11 | 硬编码常量 | `expected_row_count` / `expected_drop_count` 参数化，默认 None 表示不检查 |
| 12 | rebalance 日历 | **每月第一个交易日**（trade_cal is_open=1），报告取该日的 ASOF 最新可见期 |

说明：第 12 条为本轮实现所采用的规则（原指令中"每月第一个交易日"与"年报公告日 + 固定延迟"两案并列），如与预期不符请指出，改动仅涉及 `build_panel.py` 的 `rebalance_dates()`。