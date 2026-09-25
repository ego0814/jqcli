# jqcli Troubleshooting

## CLI Import Error

Symptom:

```text
ImportError: cannot import name 'auth_group' from partially initialized module
```

Cause: the CLI was invoked with `python -m jqcli.cli`.

Fix: use the console script:

```powershell
.\.venv\Scripts\jqcli.exe --format json auth status
```

## Missing pytest

Symptom:

```text
No module named pytest
```

Fix:

```powershell
uv sync --extra test
```

Then rerun:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Stale Cookie Or Login Redirect

Symptoms:

- `auth status` reports authenticated, but live API responses contain `redirect` to `/user/login/index`
- `backtest result` returns a login redirect

Fix:

```powershell
.\.venv\Scripts\jqcli.exe --env-file .env --format json --non-interactive --timeout 30 auth login
```

Then rerun live checks without `--env-file` so the refreshed saved cookie is used:

```powershell
.\codex-skill\jqcli\scripts\smoke_readonly.ps1
```

## System Busy Response

Symptom:

```json
{"status":"2","code":"20000","msg":"系统繁忙，请稍后重试"}
```

Treat this as a live service response, not a local parser failure. Retry once after a short wait. If it persists, report it separately from local test results.

## Research Execution Timeout Or Channel Failure

If `research exec` or `research run` times out or reports a WebSocket/channel error, preserve the original error and let jqcli finish its `finally` cleanup. Do not attach to, interrupt, or delete a kernel/session returned by the read-only list unless it was created by the current invocation.

After the command returns, compare read-only counts with a baseline:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research kernels
.\.venv\Scripts\jqcli.exe --format json --non-interactive research sessions
```

Report counts and cleanup status only; do not include identifiers, Notebook paths, code, or output unless the user explicitly requested those details. `research run` never saves execution outputs back to the remote Notebook.

## Local Data Paths

Expected ignored local state:

```text
local/data/
local/experiments/
local/logs/
local/marketing/
local/scripts/
```

If generated data appears at repo root, move it under `local/` and update the generating command or default path.

## Backtest Concurrency And Wait Status

Symptom A: `backtest run ... --wait` reports `failed` immediately, but the run is still active.

Cause: JoinQuant returns a partial record while the report is still being generated, and the wait loop treats that intermediate state as terminal.

Fix: treat the status printed by `--wait` as provisional. Poll `backtest show <backtest_id>` until it reports `done`, `failed`, or `cancelled` before reading `stats` or `logs`, and never treat the first `failed` as the final result. Do not resubmit the same backtest: a resubmission creates a new record and consumes a concurrency slot.

Symptom B: a submission fails with:

```json
{"data": null, "status": "2", "code": "20000", "msg": "当前可并行回测数已达2个"}
```

Cause: the account allows at most two backtests to run at the same time.

Fix: submit formal backtests one at a time and wait for each record to reach a terminal state before starting the next one. Treat this message as a live quota limit, not a local parser failure.

Symptom C: Chinese text in `backtest logs` output is garbled when read through PowerShell.

Cause: the CLI emits UTF-8 while the Windows console decodes with the local code page.

Fix: run `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` before invoking jqcli, and prefer ASCII markers (ticker codes, API names, `trade price`) when parsing log lines programmatically.