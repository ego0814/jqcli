# CB 双低 9-30 首次运行操作清单

> 目标交易日：2026-09-30（9 月最后一个交易日；10 月首个交易日为 2026-10-08）

## 自动步骤（run_monthly.ps1 完成）

1. [0/3] cb_daily / cb_basic / cb_redeem_jsl 增量刷新
2. [0/3b] cb_rating 增量刷新
3. [0/3c] ST 掩码链（px → namechange → st_mask）
4. [1/3] monthly_signal（若 cb_convert 未覆盖信号日，会在第 1 道门禁退出 2）

前 3 步任一步失败，run_monthly.ps1 直接 exit 2，不会发布正式信号。

## 人工步骤（在 run_monthly 之前或之后）

5. cb_convert 抓取 + 解码：

```powershell
cd D:\project\jqcli
.\.venv\Scripts\jqcli.exe --non-interactive --format json research exec `
    --file local/scripts/fetch_cb_convert_research.py --yes --execution-timeout 300
# 从 JSON 取 stdout 的 YEAR= / B64: 两行写入
#   factor_lab\data\cache\_cb_convert_2026.b64.txt
D:\project\jqcli\factor_lab\.venv\Scripts\python.exe factor_lab\data\download_cb_convert.py --decode 2026
```

校验：`cb_convert_2026.parquet` 的 max(date) 必须等于 2026-09-30。

## 四道门禁（monthly_signal.py）

| # | 门禁 | 由谁满足 |
|---|---|---|
| 1 | 信号当日有 PIT 溢价率 | 人工步骤 5 |
| 2 | cb_rating / cb_redeem_jsl / cb_basic 的 mtime ≥ 信号日 | 自动 1 / 2 |
| 3 | st_mask 最新日期 ≥ 信号日 | 自动 3 |
| 4 | cb_daily 有信号当日行情 | 自动 1 |

## 2026-09-25 验证状态

- cb_convert 最新 2026-09-24（09-25 至 09-27 休市，09-28 / 29 / 30 为未来交易日，当日不可得）
- cb_daily 最新 2026-09-23
- st_mask 最新 2026-09-24
- monthly_signal --date 2026-09-30 试跑：第 1 道门禁退出 2，未生成任何文件
- 补跑窗口：10-01 ~ 10-03（run_monthly.ps1 -MaxCatchupDays 3）
