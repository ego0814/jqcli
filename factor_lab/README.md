# factor_lab

模块化因子研究层，与 jqcli 执行层解耦。

## 目录

- data\             数据层（Tushare 封装、缓存、ST 掩码）
- ops\              算子库（来自 WorkBuddy，待移植）
- factors\          因子库（alpha101、gtja191、f_score）
- gates\            门禁（PIT 检查、阴性对照）
- analysis\         IC 分析、稳健性检验、报告
- output\

  - factor_values\  因子值（parquet）
  - predictions\    预测值表（CSV，供聚宽读）

## 移植来源

- WorkBuddy 项目：ops.py、alpha101.py、gtja191.py、pit_gate.py、pit_selftest.py、smoke_test.py
- Quant Lab 项目：F-score 的 9 项计算、Panel Builder、Derived Components、ASOF 过滤

## 移植方式

由用户在 jqcli 外手动复制到对应子目录，Codex 不跨目录访问。