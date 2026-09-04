$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TaskNames = @(
    "AeroQuant-Radith-Momentum",
    "AeroQuant-Radith-Monitor"
)

foreach ($TaskName in $TaskNames) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        continue
    }
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 1
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            if ($task.State -ne "Running") {
                break
            }
        }
        if ((Get-ScheduledTask -TaskName $TaskName).State -eq "Running") {
            throw "$TaskName is still running; reset aborted"
        }
    }
}

$Python = (Get-Command python.exe -ErrorAction Stop).Path
Push-Location $ProjectRoot
try {
    & $Python -u -m scripts.reset_runtime --confirm-reset
    if ($LASTEXITCODE -ne 0) {
        throw "reset_runtime.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
