# 预测值表格式规范

版本：v1
日期：2026-09-20
用途：本地因子研究 → 聚宽策略执行的接口

## 一、格式

CSV 文件，列：

- date：调仓日，格式 YYYY-MM-DD
- symbol：股票代码，格式 000001.XSHE / 600000.XSHG
- value：因子值（越大越好）

## 二、生成规则

1. 来源：factor_lab 的因子表（如 ep.parquet）
2. 只保留 complete=True 的行
3. 因子值做 rank 标准化到 [0, 1]（消除量纲差异）
4. 每行一个 (date, symbol, value)

## 三、文件命名

predictions_{factor}_{version}.csv
例：predictions_ep_v1.csv

## 四、上传路径

聚宽研究平台：/predictions/{factor}/
例：/predictions/ep/predictions_ep_v1.csv

## 五、聚宽策略读取

策略代码通过 read_file 读取，按 date 分组，
每期取 value 最高的 N 只股票。
