# SPECTRA setup for Windows (PowerShell 5.1+ / PowerShell 7).
#
#   .\setup.ps1            # install deps, generate data, train, seed, build UI
#   .\setup.ps1 -Force     # regenerate everything
#
# Requires: Python 3.11 or 3.12 (python.org, "Add to PATH") and Node.js 20 LTS.
# Everything else (torch CPU, ML models, dashboard) is installed by this script.

param(
    [switch]$Force,
    [switch]$SkipSeed
)
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Find-Python {
    foreach ($c in @("py", "python3.12", "python3.11", "python")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}

$py = Find-Python
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== creating virtualenv =="
    if ($py -eq "py") { & py -3.11 -m venv .venv } else { & $py -m venv .venv }
}

$args = @("scripts/setup.py")
if ($Force)    { $args += "--force" }
if ($SkipSeed) { $args += "--skip-seed" }
& ".venv\Scripts\python.exe" @args
exit $LASTEXITCODE
