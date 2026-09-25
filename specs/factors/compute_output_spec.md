# 因子值输出规范

## 一、通用要求

每个因子的计算脚本（如 compute_ep.py、compute_reversal.py）
必须同时输出两个版本：

- <因子名>_raw.parquet：原始因子值
- <因子名>_neutralized.parquet：中性化后的因子值

## 二、字段要求

两个版本的字段结构一致：

- symbol
- rebalance_date
- <因子名>_value（或 raw_value / neutralized_value 区分）
- complete
- pit_status

中性化版本额外加一列：

- neutralization_method：如 "mv+industry"

## 三、中性化实现

复用 factor_lab/analysis/neutralize.py 的 neutralize_both。
参数：

- mv_df：从 basic_*.parquet 取 total_mv
- industry_df：从 stock_basic.parquet 取 industry

已知局限：industry 是当前快照，非 PIT。
如需严格 PIT，需接入行业历史序列。

## 四、下游约定

- 因子研究（8 维度）：默认用 neutralized，同时对比 raw
- 预测值表：默认用 neutralized
- 归因分析：用 raw

## 五、检查清单

- [ ] 两个版本都已输出？
- [ ] 字段结构一致？
- [ ] 中性化方法已标注？
