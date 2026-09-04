param(
    [Parameter(Mandatory = $false)]
    [string]$Symbol = "SPY"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python.exe -ErrorAction Stop).Path

Push-Location $ProjectRoot
try {
    & $Python -u main.py --once --symbol $Symbol
    if ($LASTEXITCODE -ne 0) {
        throw "paper canary cycle failed with exit code $LASTEXITCODE"
    }

    & $Python -u -m scripts.verify_canary --symbol $Symbol
    if ($LASTEXITCODE -ne 0) {
        throw "paper canary verification failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
