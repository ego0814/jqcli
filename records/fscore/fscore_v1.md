# F-score 归档：v1

## 版本信息

- 版本：v1
- 日期：2026-09-20
- 状态：已归档（放弃）
- 来源：Piotroski (2000) + Quant Lab 迁移

## 数据产物路径

| 产物 | 路径 | 大小 |
| --- | --- | --- |
| Tushare 财报 | factor_lab/data/cache/fin_*.parquet | 48 分片 / 37,191,006 字节（35.5 MB） |
| Panel | factor_lab/output/factor_values/fscore_panel.parquet | 761,616 行 / 40,709,087 字节（38.8 MB） |
| F-score 因子表 | factor_lab/output/factor_values/fscore.parquet | 610,937 行 / 607,894 字节（593.6 KB） |
| IC 结果 | factor_lab/output/factor_values/fscore_ic.parquet | 114 行 / 15,810 字节（15.4 KB） |

| 六配置 IC | output/factor_values/fscore_ic_configs.parquet | 6 行 / 6,804 字节 |
| 分行业 IC | output/factor_values/fscore_ic_by_industry.parquet | 87 行 / 10,289 字节 |
| 分行业 IC 明细 | output/factor_values/fscore_ic_by_industry_detail.parquet | 8,959 行 / 99,821 字节 |
| 收益缓存 | output/factor_values/ic_returns_cache.parquet | 500,363 行 / 4,495,618 字节 |

| 分市值组 IC | output/factor_values/fscore_ic_by_mv_group.parquet |
| IC 衰减 | output/factor_values/fscore_ic_decay.parquet |
| 样本内外 | output/factor_values/fscore_ic_oos.parquet |
| 多周期收益缓存 | output/factor_values/ic_multi_returns.parquet |
| 可交易性审计 | output/factor_values/fscore_tradability_audit.parquet |

| ST 分支实测脚本 | data/audit_st_branch.py |

| 多周期收益缓存 | output/factor_values/fscore_multi_cache.parquet |

## 关键指标

| 指标 | 值 |
| --- | --- |
| 区间 | 2017-03 至 2026-08 |
| IC 记录数 | 114 |
| RankIC 均值 | 0.0051 |
| ICIR | 0.079 |
| t 统计量 | 0.85 |
| IC > 0 比例 | 56.14% |
| Q5-Q1 多空 | -0.062% |
| 平均横截面股票数 | 3,923 |

## 三次独立验证

| 验证 | RankIC | ICIR | t | 结论 |
| --- | --- | --- | --- | --- |
| 原始 | 0.0051 | 0.079 | 0.85 | 不显著 |
| 市值正交化 | 0.0098 | 0.088 | 0.93 | 不显著 |
| ΔF 切换月 | -0.0089 | -0.077 | -0.33 | 方向为负 |

正交化与 ΔF 两项为 100 只股票小样本口径（113 / 30 个月记录），仅作为方向性复核；主判定以全量 114 个月的原始 IC 为准。

分层单调性：Q1 0.928% > Q5 0.866%，无序，Q5-Q1 = -0.062%（胜率 50.0%）。

## 六种处理配置复核

见 failures/fscore_v1_2026-09-20.md 的详细表格。
结论：F-score 正式结案。

## 与 Quant Lab 研究的一致性

Quant Lab 的 FAR 研究（2022-2026）独立发现：

- F-score 效应 = -0.17%/月（负向）
- 交互效应 ≈ 0

两次独立研究指向同一结论。

## 相关产物

- 因子定义：specs/factors/f_score_spec.md
- 适配层规格书：specs/factors/adapter_spec.md
- PIT 门禁规格书：specs/factors/pit_gate_spec.md
- 适配层代码：factor_lab/data/build_panel.py
- 因子计算：factor_lab/data/compute_fscore.py
- IC 分析：factor_lab/data/compute_ic.py
- 正交化复核：factor_lab/data/compute_ic_neutralized.py
- ΔF 复核：factor_lab/data/compute_ic_delta.py

## 下一步

放弃 F-score。转向其他因子方向。