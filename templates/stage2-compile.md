使用 jqcli skill，工作目录 D:\project\jqcli。

用户已确认策略代码和参数。

执行编译验证：
.\.venv\Scripts\jqcli.exe backtest run <策略ID> --start <开始> --end <结束> --capital <资金> --freq day --compile --wait

不要加 --use-credit。

报告：编译成功/失败。如果失败，输出聚宽返回的编译错误信息。
