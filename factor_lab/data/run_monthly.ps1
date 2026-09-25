<#
.SYNOPSIS
    CB 双低 · 月度模拟盘自动化（行情刷新 + 信号 + 复盘）

.DESCRIPTION
    在调仓窗口内自动执行四步：
      [0/3] 刷新行情   download_cb.py --incremental（Tushare 增量 + AkShare 强赎快照）
      [0/3c] 刷新 ST 掩码  step1_download.py --incremental -> step0_namechange.py -> step0b_st_mask.py
      [1/3] 生成信号   monthly_signal.py （下期目标清单 + 买卖清单）
      [2/3] 月度复盘   monthly_review.py （差异报告 + 追加台账）

    行情或评级刷新失败时返回非零；不会使用未核实的旧缓存发布正式信号。

    三道守卫（使多次触发可安全重复执行）：
      守卫 0：交易日历覆盖检查 —— 过期则自动调用 refresh_trade_cal.py
      守卫 1：调仓窗口 —— 目标交易日 = 本月最后交易日（已到或已过），
              或上一个自然月的最后交易日（跨月补跑）；
              且 今天 - 目标交易日 <= MaxCatchupDays（默认 3 个自然日）
      守卫 2：目标交易日尚未生成完整的目标和卖出信号文件

    为什么不是"今天必须正好是本月最后交易日"：
      - 22:30 触发时，当日行情可能仍未落库；跨天补跑能等到完整数据
      - 机器在月末当天关机/睡眠时，次日/第三日仍能补跑
      - 超出补跑窗口（> MaxCatchupDays）则跳过，避免用过期行情生成信号

.PARAMETER Force
    跳过守卫 1、守卫 2，强制运行（手动补跑时用）。

.PARAMETER SkipRefresh
    跳过 [0/3] 行情刷新与 [0/3c] ST 掩码刷新；评级刷新不受影响。

.PARAMETER SkipRating
    跳过 [0/3b] 评级刷新（Tushare cb_rating）；与 -SkipRefresh 同时使用即整段刷新跳过。

.PARAMETER MaxCatchupDays
    补跑窗口（自然日），默认 3：目标交易日后 3 天内仍可生成信号。

.PARAMETER Capital
    模拟本金，默认 100000。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\project\jqcli\factor_lab\data\run_monthly.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\project\jqcli\factor_lab\data\run_monthly.ps1 -Force
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\project\jqcli\factor_lab\data\run_monthly.ps1 -MaxCatchupDays 5
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\project\jqcli\factor_lab\data\run_monthly.ps1 -SkipRefresh
#>
param(
    [switch]$Force,
    [switch]$SkipRefresh,
    [switch]$SkipRating,
    [int]$MaxCatchupDays = 3,
    [double]$Capital = 100000
)

$ErrorActionPreference = "Continue"
$ProjectRoot = "D:\project\jqcli\factor_lab"
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SignalDir   = Join-Path $ProjectRoot "output\monthly_signals"
$LogDir      = Join-Path $ProjectRoot "output\factor_values"
$RefreshPy   = Join-Path $ProjectRoot "data\download_cb.py"
$RatingPy    = Join-Path $ProjectRoot "data\fetch_cb_rating.py"
$SignalPy    = Join-Path $ProjectRoot "data\monthly_signal.py"
$ReviewPy    = Join-Path $ProjectRoot "data\monthly_review.py"
$Stamp       = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LogFile     = Join-Path $LogDir "monthly_run_$Stamp.log"
$env:JQCLI_TRADE_CAL   = Join-Path $ProjectRoot "data\cache\trade_cal.parquet"
$env:JQCLI_CB_DAILY    = Join-Path $ProjectRoot "data\cache\cb_daily.parquet"
$env:JQCLI_MAX_CATCHUP = "$MaxCatchupDays"
$RepoRoot        = Split-Path $ProjectRoot -Parent
$Jqcli           = Join-Path $RepoRoot ".venv\Scripts\jqcli.exe"
$AutoLoginPy     = Join-Path $ProjectRoot "data\jq_auto_login.py"
$AutoLoginPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

# 编码：让 python 用 UTF-8 输出，并让 PowerShell 按 UTF-8 解码子进程输出。
# 否则中文在控制台和日志里会变成乱码（实测 PS 5.1 + cp936 locale 的默认组合会双双不匹配）。
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

Write-Host "=== CB 双低月度模拟盘 ===" -ForegroundColor Cyan
Write-Host ("时间: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host "本金: $Capital"
Write-Host ("补跑窗口: " + $MaxCatchupDays + " 天")
Write-Host "日志: $LogFile"

if (-not (Test-Path $PythonExe)) { Write-Host "找不到 python: $PythonExe" -ForegroundColor Red; exit 2 }
New-Item -ItemType Directory -Force -Path $LogDir, $SignalDir | Out-Null

function Get-PlanState {
    $py = @"
import os
from datetime import datetime
import pandas as pd

cal = pd.read_parquet(os.environ['JQCLI_TRADE_CAL'])
days = sorted(cal[cal['is_open'].astype(int) == 1]['cal_date'].astype(str))
today = pd.Timestamp.today().strftime('%Y%m%d')
max_gap = int(os.environ.get('JQCLI_MAX_CATCHUP', '3'))

this_month = [x for x in days if x[:6] == today[:6]]
prev_month = [x for x in days if x[:6] < today[:6]]

if not this_month or not prev_month:
    print('STALE ' + (this_month[-1] if this_month else 'NA'))
else:
    l_this, l_prev = this_month[-1], prev_month[-1]
    # 目标交易日：本月最后交易日若已到/已过就用它，否则用上月最后交易日（跨月补跑）
    target = l_this if today >= l_this else l_prev
    gap = (datetime.strptime(today, '%Y%m%d') - datetime.strptime(target, '%Y%m%d')).days
    print(('RUN ' if gap <= max_gap else 'SKIP ') + target + ' ' + str(gap))
"@
    $out = ($py | & $PythonExe -u - | Out-String).Trim()
    return $out
}

function Get-DailyMax {
    $py = "import os, pandas as pd; d = pd.read_parquet(os.environ['JQCLI_CB_DAILY']); print(str(d['trade_date'].astype(str).max()))"
    $out = ($py | & $PythonExe -u - | Out-String).Trim()
    return $out
}

function Get-StMax {
    $p = Join-Path $ProjectRoot "data\cache\st_mask.parquet"
    $py = "import pandas as pd; d = pd.read_parquet(r'" + $p + "'); print(str(max(str(x) for x in d.index)))"
    $out = ($py | & $PythonExe -u - | Out-String).Trim()
    return $out
}


# 同一台机器的两个调度入口共用命名互斥量，避免同时刷新和发布信号。
$Mutex = New-Object System.Threading.Mutex($false, 'Local\jqcli_cb_monthly')
if (-not $Mutex.WaitOne(0)) {
    Write-Host '[跳过] 另一月度任务正在运行'
    $Mutex.Dispose()
    exit 0
}
try {
# ---------- 守卫 ----------
$verdict = "FORCE"
$target  = ""
$gap     = ""

if (-not $Force) {
    $state = Get-PlanState
    Write-Host ("调仓窗口判定: " + $state)
    if ($state -like "STALE*") {
        Write-Host "交易日历未覆盖本月，尝试自动刷新..." -ForegroundColor Yellow
        & $PythonExe -u (Join-Path $ProjectRoot "data\refresh_trade_cal.py") 2>&1 | Out-Null
        $state = Get-PlanState
        Write-Host ("刷新后调仓窗口判定: " + $state)
    }
    if ($state -like "STALE*") {
        Write-Host "`n[跳过] 交易日历仍未覆盖本月，请手动运行 refresh_trade_cal.py（需要 TUSHARE_TOKEN）" -ForegroundColor Red
        exit 2
    }

    if ($state -notmatch '^(RUN|SKIP) \d{8} -?\d+$') {
        Write-Host ('[错误] 调仓窗口判定失败：' + $state) -ForegroundColor Red
        exit 2
    }
    $parts   = $state.Split(' ')
    $verdict = $parts[0]
    $target  = $parts[1]
    $gap     = $parts[2]

    if ($verdict -eq "SKIP") {
        Write-Host ""
        Write-Host ("[跳过] 当前不在调仓窗口：目标交易日 " + $target + "，距今 " + $gap + " 天（补跑窗口 " + $MaxCatchupDays + " 天）") -ForegroundColor Yellow
        Write-Host "如需强制运行：-Force"
        exit 0
    }
    if ([int]$gap -gt 0) {
        Write-Host ("[补跑] 目标交易日 " + $target + "（距今 " + $gap + " 天，在 " + $MaxCatchupDays + " 天窗口内）") -ForegroundColor Yellow
    }

    # 仅精确目标日算完成；同月较早的试运行信号不能阻挡月末。
    $targetIso = $target.Substring(0,4) + '-' + $target.Substring(4,2) + '-' + $target.Substring(6,2)
    $existing = Join-Path $SignalDir ($targetIso + '.csv')
    $existingSell = Join-Path $SignalDir ($targetIso + '_sell.csv')
    if ((Test-Path $existing) -and (Test-Path $existingSell)) {
        Write-Host ("[补跑] 目标交易日已有完整信号，检查并补写复盘：" + $targetIso)
        & $PythonExe -u $ReviewPy --date $targetIso 2>&1 | Tee-Object -FilePath $LogFile -Append
        $reviewExit = $LASTEXITCODE
        if ($reviewExit -ne 0) {
            Write-Host ("[错误] 复盘补跑失败，exit " + $reviewExit) -ForegroundColor Red
            exit 2
        }
        exit 0
    }
    if ((Test-Path $existing) -or (Test-Path $existingSell)) {
        Write-Host ("[错误] 目标交易日只有部分信号文件：" + $targetIso) -ForegroundColor Red
        exit 2
    }
}

# ---------- [0/3] 刷新行情 ----------
$RefreshOk = $true
if ($SkipRefresh) {
    Write-Host "`n[0/3] 行情刷新：已跳过（-SkipRefresh）" -ForegroundColor Yellow
} else {
    Write-Host "`n[0/3] 刷新行情（Tushare 增量 + AkShare 强赎快照）..." -ForegroundColor Yellow
    & $PythonExe -u $RefreshPy --incremental 2>&1 | Tee-Object -FilePath $LogFile
    if ($LASTEXITCODE -ne 0) {
        $RefreshOk = $false
        Write-Host ("[警告] 行情刷新失败（exit " + $LASTEXITCODE + "），信号将基于现有缓存") -ForegroundColor Red
    }
}
$DailyMax = Get-DailyMax
Write-Host ("行情缓存最新交易日: " + $DailyMax)

# ---------- [0/3b] 刷新可转债评级（失败不阻塞，同行情刷新） ----------
$RatingOk = $true
if ($SkipRating) {
    Write-Host "评级刷新：已跳过（-SkipRating）"
} else {
    Write-Host "`n[0/3b] 刷新可转债评级（Tushare cb_rating 增量）..." -ForegroundColor Yellow
    & $PythonExe -u $RatingPy --incremental 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        $RatingOk = $false
        Write-Host ("[评级] 刷新失败（exit " + $LASTEXITCODE + "），沿用现有 cache\cb_rating.parquet") -ForegroundColor Red
    } else {
        Write-Host "[评级] 刷新完成"
    }
}

# ---------- [0/3b2] 研究平台认证自检（失效则自动登录） ----------
if (-not (Test-Path $Jqcli)) {
    Write-Host ("[错误] 找不到 jqcli：" + $Jqcli) -ForegroundColor Red
    exit 2
}
$authProbe = (& $Jqcli --non-interactive --format json research ls 2>&1 | Out-String)
if ($authProbe -match "not_authenticated") {
    Write-Host "研究平台认证失效，尝试自动登录..." -ForegroundColor Yellow
    if (Test-Path $AutoLoginPy) {
        & $AutoLoginPython $AutoLoginPy 2>&1 | Tee-Object -FilePath $LogFile -Append
        $authProbe = (& $Jqcli --non-interactive --format json research ls 2>&1 | Out-String)
    } else {
        Write-Host ("[错误] 找不到自动登录脚本：" + $AutoLoginPy) -ForegroundColor Red
    }
    if ($authProbe -match "not_authenticated") {
        Write-Host "[错误] 自动登录失败，请手动处理" -ForegroundColor Red
        exit 2
    }
}
Write-Host "研究平台认证: 正常"

# ---------- [0/3c] 刷新研究层 ST 掩码 (px -> namechange -> st_mask) ----------
$StOk = $true
if ($SkipRefresh) {
    Write-Host "ST 掩码刷新：已跳过（-SkipRefresh）" -ForegroundColor Yellow
} else {
    Write-Host "`n[0/3c] 刷新研究层 ST 掩码（step1_download -> step0_namechange -> step0b_st_mask）..." -ForegroundColor Yellow
    $StStages = @(
        @{ Name = "step1_download"; Args = @("--incremental") },
        @{ Name = "step0_namechange"; Args = @() },
        @{ Name = "step0b_st_mask"; Args = @() }
    )
    foreach ($stage in $StStages) {
        $stScript = Join-Path $ProjectRoot ("data\" + $stage.Name + ".py")
        & $PythonExe -u $stScript @($stage.Args) 2>&1 | Tee-Object -FilePath $LogFile -Append
        if ($LASTEXITCODE -ne 0) {
            $StOk = $false
            Write-Host ("[ST] " + $stage.Name + " 失败（exit " + $LASTEXITCODE + "）") -ForegroundColor Red
            break
        }
    }
    if ($StOk) { Write-Host "[ST] 掩码刷新完成" }
}
$StMax = Get-StMax
Write-Host ("ST 掩码最新交易日: " + $StMax)

if (-not $RefreshOk -or -not $RatingOk -or -not $StOk) {
    Write-Host '[错误] 数据刷新不完整，本期不生成正式信号' -ForegroundColor Red
    exit 2
}
if ($target -and $DailyMax -lt $target) {
    Write-Host ("[错误] 行情缓存 " + $DailyMax + " 尚未覆盖目标交易日 " + $target) -ForegroundColor Red
    exit 2
}
$signalArgs = @('--capital', $Capital)
if ($target) { $signalArgs += @('--date', $targetIso) }
Write-Host "`n[1/3] 生成信号..." -ForegroundColor Yellow
& $PythonExe -u $SignalPy @signalArgs 2>&1 |
    Tee-Object -FilePath $LogFile -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host ("[错误] 信号生成失败，exit " + $LASTEXITCODE) -ForegroundColor Red
    exit 2
}

Write-Host "`n[2/3] 月度复盘..." -ForegroundColor Yellow
$reviewArgs = @()
if ($targetIso) { $reviewArgs += @('--date', $targetIso) }
& $PythonExe -u $ReviewPy @reviewArgs 2>&1 |
    Tee-Object -FilePath $LogFile -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host ("[错误] 月度复盘失败，exit " + $LASTEXITCODE) -ForegroundColor Red
    exit 2
}

Write-Host "`n=== 完成 ===" -ForegroundColor Green
if ($target) { Write-Host ("调仓目标交易日: " + $target + "（判定 " + $verdict + "，距今 " + $gap + " 天）") }
if ($SkipRefresh) {
    Write-Host "行情刷新: 已跳过（-SkipRefresh）"
} elseif ($RefreshOk) {
    Write-Host "行情刷新: 成功"
} else {
    Write-Host ("行情刷新: 失败；本次信号基于 " + $DailyMax) -ForegroundColor Red
}
Write-Host ("行情最新交易日: " + $DailyMax)
if ($SkipRating) {
    Write-Host "评级刷新: 已跳过（-SkipRating）"
} elseif ($RatingOk) {
    Write-Host "评级刷新: 成功"
} else {
    Write-Host "评级刷新: 失败（沿用旧缓存）" -ForegroundColor Red
}
Write-Host "日志: $LogFile"
Write-Host ("台账: " + (Join-Path $SignalDir 'TRACKING.md'))
Write-Host ""
Write-Host "请手动填写台账（output/monthly_signals/TRACKING.md）："
Write-Host "  1. 实际下单手数（每只）"
Write-Host "  2. 实际成交价"
Write-Host "  3. 台账表里的粒度偏差和滑点"
exit 0
} finally {
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
