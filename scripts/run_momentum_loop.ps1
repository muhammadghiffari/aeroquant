param(
    [int]$IntervalMinutes = 5,
    [switch]$DryRun,
    [switch]$Once,
    [string]$Symbols = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONUNBUFFERED = "1"
$LauncherLog = Join-Path $ProjectRoot "state\entry_launcher.log"

function Write-LauncherLog([string]$Message) {
    "$(Get-Date -Format o) $Message" | Add-Content -LiteralPath $LauncherLog
}

try {
    Write-LauncherLog "starting task launcher"
    $configCheck = python -c "import config; from runtime_safety import configuration_errors; errors = configuration_errors(require_llm=True, require_autonomy=True); assert not errors, '; '.join(errors)"
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime safety check failed."
    }

    $mode = if ($Once) { "--once" } else { "--loop" }
    $args = @("-u", "main.py", $mode, "--interval", $IntervalMinutes)
    if ($Symbols.Trim()) {
        $args += @("--symbol", $Symbols)
    }
    if ($DryRun) {
        $args += "--dry-run"
    }

    Write-LauncherLog "starting python $($args -join ' ')"
    & python @args
    $exitCode = $LASTEXITCODE
    Write-LauncherLog "python exited with code $exitCode"
    exit $exitCode
} catch {
    Write-LauncherLog "launcher failed: $($_.Exception.Message)"
    throw
}
