param(
    [string]$EntryTaskName = "AeroQuant-Radith-Momentum",
    [string]$MonitorTaskName = "AeroQuant-Radith-Monitor"
)

$ErrorActionPreference = "Stop"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    throw "Run this script from an elevated PowerShell (Administrator) to update Task Scheduler definitions."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$MonitorLauncher = Join-Path $ProjectRoot "main.py"

$preflight = & python -c "from runtime_safety import configuration_errors; errors = configuration_errors(require_llm=True, require_autonomy=True); print('; '.join(errors)); raise SystemExit(1 if errors else 0)"
if ($LASTEXITCODE -ne 0) {
    if (-not [string]::IsNullOrWhiteSpace(($preflight -join ""))) {
        Write-Output "Preflight errors: $($preflight -join '; ')"
    }
    throw "Autonomous runtime preflight failed. Fill the local .env before registering the task."
}

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$python = (Get-Command python.exe -ErrorAction Stop).Path
$taskNames = @($EntryTaskName, $MonitorTaskName)

# A running legacy instance would consume the one-shot trigger as an ignored duplicate.
foreach ($taskName in $taskNames) {
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask -and $existingTask.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
    }
}

$entryAction = New-ScheduledTaskAction -Execute $python -Argument "-u `"$MonitorLauncher`" --loop --interval 5" -WorkingDirectory $ProjectRoot
$monitorAction = New-ScheduledTaskAction -Execute $python -Argument "-u `"$MonitorLauncher`" --monitor --interval 1" -WorkingDirectory $ProjectRoot
# Each action owns its cadence; repeating the trigger would overlap with the long-running loops.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $EntryTaskName -Action $entryAction -Trigger $trigger -Principal $principal -Settings $settings -Description "Paper-only autonomous AeroQuant deterministic momentum entries" -Force | Out-Null
Register-ScheduledTask -TaskName $MonitorTaskName -Action $monitorAction -Trigger $trigger -Principal $principal -Settings $settings -Description "Paper-only autonomous AeroQuant position monitor" -Force | Out-Null

foreach ($taskName in $taskNames) {
    $registeredTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $repeatingTrigger = @(
        $registeredTask.Triggers | Where-Object {
            $_.Repetition -and $_.Repetition.Interval
        }
    )
    if ($repeatingTrigger.Count -gt 0) {
        throw "$taskName still has a repeating trigger; registration was not applied safely."
    }
}

Write-Output "Registered $EntryTaskName and $MonitorTaskName for $env:USERDOMAIN\$env:USERNAME"
