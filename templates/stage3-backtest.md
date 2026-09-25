使用 jqcli skill，工作目录 D:\project\jqcli。

编译已通过，执行正式回测。

回测参数（阶段0已确认，不允许修改）：
- 策略ID：
- 区间：
- 初始资金：
- 频率：day
- 执行时点：（提交前必须核对规格书与回测产物 manifest 的 execution_timing 字段是否一致）

========== 提交前检查 ==========

聚宽账号并行回测上限为 2。

提交回测时，如果返回 code 20000（当前可并行回测数已达 2 个）：

- 不要重新提交同一回测
- 等待现有回测完成后再提交
- 不要为了腾出并行额度而删除已有回测记录
- 用 backtest ls <策略ID> 查看当前运行中的回测状态

执行：
.\.venv\Scripts\jqcli.exe backtest run <策略ID> --start <开始> --end <结束> --capital <资金> --freq day --wait

不要加 --use-credit。

========== 执行回测后的注意事项 ==========

jqcli backtest run --wait 返回 failed 时，不要重新提交。原因：

- 这是 CLI 等待判定问题，回测实际状态可能已经是 done
- 用 backtest show <回测ID> 轮询确认终态
- 只有 show 返回明确失败，才考虑修改代码重跑
- 重新提交会新建回测记录，并占用并行额度

正确流程：

1. backtest run --wait 返回后，记录回测 ID
2. 用 backtest show <回测ID> 轮询到状态为 done 或 failed
3. 状态为 done 才进入下一步，状态为 failed 才检查日志

========== 分段回测（如规格书定义了样本内外） ==========

如果规格书定义了样本内/样本外区间，需要提交三次回测：

1. 主回测：全区间（规格书的评估区间）
2. 样本内回测：规格书的样本内区间
3. 样本外回测：规格书的样本外区间

三次必须串行提交，不能同时提交。原因：

- 聚宽并行上限为 2，同时提交三次第三次会被拒
- 每次提交后等待完成，再提交下一次

每次回测完成后：

- 单独记录回测 ID
- 分别执行 backtest stats 和 backtest logs --error
- 主回测、样本内、样本外三段结果分开报告

聚宽 stats 不提供分年度拆分，必须分段跑才能得到样本内外指标。

拿到回测ID后：
.\.venv\Scripts\jqcli.exe --non-interactive --format json backtest stats <回测ID>
.\.venv\Scripts\jqcli.exe --non-interactive --format json backtest logs <回测ID> --error
.\.venv\Scripts\jqcli.exe --non-interactive --format json backtest logs <回测ID> --all

报告：

- 主回测：区间、收益率、年化、夏普、最大回撤、基准收益、日志错误摘要
- 样本内（如适用）：同上
- 样本外（如适用）：同上
- 三段回测 ID 分别列出
- 确认所有回测终态为 done，而不是 --wait 返回的中间态
