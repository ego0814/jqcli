# 券商实际费率确认（待用户填写）

填写日期：
券商：
账户类型：
资金规模：

## 可转债费率

| 项 | 值 | 来源 |
|---|---|---|
| 沪市佣金 | ? | 客服确认 |
| 深市佣金 | ? | 客服确认 |
| 沪市最低佣金 | ? | 客服确认 |
| 深市最低佣金 | ? | 客服确认 |
| 滑点估计 | ? | 基于成交额/流动性 |

## 执行链路

| 项 | 值 |
|---|---|
| 是否支持批量下单 | ? |
| 是否有 QMT 权限 | ? |
| QMT 是否支持 CB | ? |
| 其它执行方式 | ? |

## 基于实际费率的重跑命令

拿到上面的费率后，把 `?` 替换成实际值，执行：

```powershell
cd D:\project\jqcli\factor_lab
.\.venv\Scripts\python.exe -u .\data\backtest_cb_double_low.py `
  --commission <沪市佣金率> `
  --min-commission-sh <沪市最低佣金> `
  --min-commission-sz <深市最低佣金> `
  --slippage <滑点> `
  --top-quantile 0.50 --hysteresis-quantile 0.60 --weight-band 0.02 `
  --tag actual
```

**⚠️ 必须带最后那行三个策略参数**：脚本的默认 `--top-quantile` 是 **0.40**（v1 口径），
而最终归档口径是 **Top 50% + 缓冲带 60% + 权重带 2%（v3_fixed）**。
只传成本参数会静默跑成 v1，结果不可与归档对比。

参考示例（若券商费率就是当前保守口径：万2 / 沪1元 / 深0 / 滑点千2）：

```powershell
.\.venv\Scripts\python.exe -u .\data\backtest_cb_double_low.py `
  --commission 0.0002 --min-commission-sh 1 --min-commission-sz 0 --slippage 0.002 `
  --top-quantile 0.50 --hysteresis-quantile 0.60 --weight-band 0.02 `
  --tag actual
```

## 对照基准（保守口径 v3_fixed，样本外）

| 指标 | 值 |
|---|---|
| 年化 | +9.39% |
| 超额（vs 可投池等权） | +5.76% |
| 夏普（rf=0） | 0.675 |
| 最大回撤 | −14.64% |
| 换手率（单边/次调仓） | 37.80% |
| 超额 IR / t | 0.94 / +1.75 |
| 极端成本情景年化 | +7.03% |

若实际费率下的结果低于上述基准，差额即为真实费率相对保守假设的成本差异。

## 重跑后需要同步的文件

1. `specs/costs/standard.md` 的 **CB 段**：把保守口径更新为实际费率（含沪/深最低佣金区分）
2. `records/cb_double_low/cb_double_low_v1.md` 的 **成本敏感性** 章节：用实际费率结果替换/新增情景
3. 重新生成报告：`python factor_lab/data/make_report.py --strategy cb_double_low --scenario actual`

## 说明

- 本文件是**脚手架**：费率确认前不执行任何重跑，不修改任何代码或归档。
- 费率来源必须是**客服/柜台书面确认**，不要用第三方估算；滑点按你自己的成交记录估。
- 实测参考：券商费率若与"保守口径"（万2 + 沪1元 + 深0 + 滑点千2）差异不大，
  策略结论不会变化 —— 当前保守情景与乐观情景的样本外差异仅约 0.5pp（见归档成本敏感性表）。
