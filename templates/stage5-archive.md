使用 jqcli skill，工作目录 D:\project\jqcli。

本轮做归档和版本管理。

策略名：
版本：
策略ID：
代码文件：
规格书：
回测ID：
参数：
结论：通过 / 需修改 / 放弃
失败原因（如果是放弃）：

请在 records\<策略名>.md 中追加一条记录（使用 records/_template.md 的格式）。
如果结论是"放弃"，同时在 failures\<策略名>_<日期>.md 创建归档（使用 failures/_template.md 的格式）。

不要执行任何远端操作。

### 步骤 N：记录执行时点与做敏感性检验

归档必须写明本次结果对应的执行时点（信号日 / 执行日 / 执行价格），
并对相邻时点（T-1 / T / T+1）做敏感性检验，记录年化、夏普、超额 IR/t 的极差；
极差 > 20% 时归档结论必须标注降级，不得以「稳健通过」表述。

### 步骤 N：生成可视化报告

在完成 records/ 和 failures/ 归档后，生成可视化报告：

```powershell
D:\project\jqcli\factor_lab\.venv\Scripts\python.exe D:\project\jqcli\factor_lab\data\make_report.py --strategy <策略名>
```

报告输出到 `records/<策略名>/report_v1.html`。

验证：

```powershell
Test-Path records/<策略名>/report_v1.html
```

要求：

- 报告包含指标卡片、净值曲线、回撤、分年度、月度热力图、换手率、四情景、已知局限
- 报告是自包含 HTML（无外部依赖，plotly.js 内嵌，双击可打开）
- 报告与归档 md 的数据一致（自动从 md 提取门槛判定和已知局限）
- 新策略需先在 `factor_lab/data/make_report.py` 的 STRATEGIES 表登记
