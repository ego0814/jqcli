# reversal 归档值不可追溯（2026-09-25）

## 结论

**reversal 的 IC 归档值标记为不可追溯。** 不影响其"IC 有效、alpha 不可投"的结论，
也不影响部署路径（reversal 本就在部署路径之外）。

## 证据

| 文件 | mtime |
|---|---|
| factor_lab/output/factor_values/reversal.parquet | 2026-09-20 19:41:03 |
| factor_lab/output/factor_values/reversal_ic_configs.parquet | 2026-09-20 18:46:24 |
| 差值 | 54 分 39 秒（约 55 分钟） |

**归档 IC 早于当前因子文件生成**，说明归档值对应的是上一版 reversal.parquet。

## 备份与历史检索

| 检索项 | 结果 |
|---|---|
| Git 跟踪 factor_lab/ | 0 个文件（整个目录未入库） |
| `git log -- factor_lab/output/factor_values/reversal.parquet` | 无输出 |
| local/backups/ | 只有 3 份 2026-09-22 文档，无 reversal.parquet |
| 全库 `*backup*` / `*.bak` | 仅 trade_cal.parquet.bak、AGENTS.md.bak |
| 其它 reversal.parquet 副本 | 无 |

## 影响

2026-09-25 的 PIT 修复重算中，reversal 的受控配置（1_baseline 等 5 项）
自身就出现 0.22%~1.78% 的漂移，而 config 6 的变化是 IC 0.045517 → 0.047447、
ICIR 0.510498 → 0.489578（-4.10%）。该变化落在自身漂移带附近，
**不能归因到 PIT 修复**，因为缺少归档值对应的那一版因子文件。

要定论需先重建同版本 reversal.parquet，再复算对比。本轮不做强行重算。

## 教训

研究产物必须在归档前固化输入文件的哈希与 mtime；归档 IC 引用的因子文件版本必须可追溯。
建议写入 AGENTS.md「八、版本管理」。

## 相关

- 对照表：local/data/ic_pit_20260925/comparison.md（第十五节）
- 归档：records/reversal/reversal_v1.md