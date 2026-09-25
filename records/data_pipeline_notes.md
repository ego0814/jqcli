# 数据管道特性记录（研究层缓存）

本文记录 2026-09-25 修复/排查过程中实测到的数据管道行为与坑，供后续维护引用。
涉及的缓存都在 `factor_lab/data/cache/`。

## 一、px 分片（Tushare 全 A 行情）

- 按年分片：`px_<year>.parquet` 与 `basic_<year>.parquet`（2015~2026）
- 入口：`factor_lab/data/step1_download.py`；`START` 默认 20150101，`END` 默认当天（2026-09-25 起由硬编码 `20260918` 改为动态），可用 `--start` / `--end` 覆盖
- **增量语义**：`--incremental` 补的是「该年交易日历中、分片里缺失的日期」，不是只补 `max(trade_date)+1`。因此内部空洞也能自愈
- 合并规则：`concat` + `drop_duplicates(['ts_code','trade_date'], keep='last')` + 按 `(trade_date, ts_code)` 排序；**只追加，不覆盖历史**
- 空响应不再静默：单日重试 3 次；日期 `<` 今天则记为失败并在结尾 `exit 1`；日期 `>=` 今天只 warn（当日数据可能尚未发布），下次运行重试
- 实测坑：`pro.daily('20260922')` 曾瞬时返回空，旧代码静默吞掉，`px_2026` 出现永久空洞；只按 `max+1` 补数永远补不回来

## 二、名称变更与 ST 掩码

- `factor_lab/data/step0_namechange.py`：拉 1990~2026 全市场 `namechange` → `cache/namechange.parquet`
- `factor_lab/data/step0b_st_mask.py`：按名称区间逐日重建 `cache/st_mask.parquet`（index=trade_date，columns=ts_code，True 表示 ST / PT / 退市整理）
- **ST 掩码的日期网格来自 px 分片**：px 不到信号日，ST 掩码就不到信号日
- 实测坑：`step0_namechange.py` 原实现把「某年返回 0 条」当成正常。2017 年返回 0 条时静默落盘，`namechange` 少 649 行（13,575 而非 14,224 行），ST 掩码连带失真：
  - 风险名称区间 2,941 → 3,009
  - 生效区间数 1,471 → 1,539
  - ST 股票×日 534,286（3.21%）→ 562,050（3.38%）
  - 每日 ST 只数最小值 13 → 43（13 显然是错的）
  - 现改为：单年最多 4 次尝试（含空响应重试），任一为空则在写盘前 `SystemExit`，**不落盘部分数据**

## 三、cb_convert（聚宽 PIT 转股价 / 转股溢价率）

- 来源：聚宽研究环境的 `jqdata.bond.CONBOND_DAILY_CONVERT`（经 `jqdata.bond.run_query` + `jqdata.query`；Tushare 无法替代）
- 通道：
  1. `jqcli research exec --file local/scripts/fetch_cb_convert_research.py --yes` 在远端抓取
  2. 远端只往 stdout 打两行协议：`YEAR=<年> ROWS=<n> MAXWIN=<n> FIRST=<日期> LAST=<日期>` 与 `B64:<gzip+base64 的 CSV>`
  3. 两行写入 `cache/_cb_convert_<年>.b64.txt`
  4. `python factor_lab/data/download_cb_convert.py --decode <年>` 解成 `cache/cb_convert_<年>.parquet`
- CSV 契约：`code,date,convert_price,convert_premium_rate`，日期 `YYYY-MM-DD`，code 为 6 位无后缀
- **单次查询上限 5000 行**（实测：每个自然月窗口都精确返回 5000 行并被截断）→ 抓取器必须按月开窗，并在命中上限时递归对半切。2026 年实测 23 次查询 / 7 次切分
- **解码是整体覆盖写**：只回传尾部日期会让该年历史整段消失。刷新必须整年抓取（本次抓 2026-01-01 ~ 2026-09-30，得 54,727 行 / 396 只）
- **数据可变（重要）**：2026-09-21 的快照与 2026-09-25 重抓对比，2026-09-01 之前 100% 不变，但 266 行溢价率发生变化（全部落在 2026-09-01 之后），其中 2 行转股价被回溯改写：
  - `118027` 于 2026-09-08：13.12 → 8.68（典型转股价下修）
  - `111022` 于 2026-09-11：22.39 → 22.19
  - 含义 1：转股价下修会回溯改写历史
  - 含义 2：**最新一天的数据是暂定值**，隔几日重抓会被修正 → 信号日当天必须重抓一次
- 抓取器在 kernel 内**不要** `raise SystemExit`：会被 IPython 记成错误输出，jqcli 报 `status=error`，即使抓取本身成功

## 四、调度与门禁

- `factor_lab/data/run_monthly.ps1` 顺序：`[0/3]` cb 行情 → `[0/3b]` cb_rating → `[0/3c]` ST 掩码链（`step1_download --incremental` → `step0_namechange` → `step0b_st_mask`）→ 门禁汇总 → `[1/3]` monthly_signal → `[2/3]` monthly_review；任一刷新步骤失败即 `exit 2`
- `factor_lab/data/monthly_signal.py` 的四道门禁：
  1. 信号当日有 PIT 溢价率（L100-102）
  2. `cb_rating` / `cb_redeem_jsl` / `cb_basic` 的 mtime ≥ 信号日（L109-113）
  3. `st_mask` 最新日期 ≥ 信号日（L119-121）
  4. `cb_daily` 有信号当日行情（无显式门禁，缺则为 0 只标的）
- `cb_convert` **不在自动链路内**（依赖聚宽研究环境登录态），只能人工在信号日补抓

## 五、已知未解决项

- `px_merged.parquet`（约 209 MB）未随 px 分片刷新，而 `factor_lab/data/compute_ic_processed.py` 优先读它 → 分片与 merged 可能不一致；需要时另跑 `factor_lab/data/merge_px.py`
- `factor_lab/data/quantile_alpha_scan.py` 的流动性窗口用 `min_periods=1`，与 `monthly_signal.py` 的完整 20 交易日口径不一致
