# SPECTRA launcher for Windows: serves API + dashboard on http://localhost:8000
#
#   .\run.ps1              # start (set up first if needed)
#   .\run.ps1 -Dev         # also start the Vite dev server on :5173
#   .\run.ps1 -Port 8080

param(
    [switch]$Dev,
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "First run: setting up SPECTRA (this can take a few minutes)…"
    & powershell -NoProfile -ExecutionPolicy Bypass -File ".\setup.ps1"
}

$args = @("scripts/run_dev.py", "--port", "$Port")
if ($Dev) { $args += "--dev" }
& ".venv\Scripts\python.exe" @args
