#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$IncludeE2E
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw ".venv is missing. Run .\scripts\bootstrap.ps1 first."
}

Set-Location -LiteralPath $projectRoot
Write-Host "Running backend tests..."
& $venvPython -m pytest
if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

if (-not $SkipFrontend) {
    $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $npm) { throw "npm was not found." }
    Push-Location -LiteralPath (Join-Path $projectRoot "frontend")
    try {
        Write-Host "Running frontend unit tests..."
        & $npm.Source run test
        if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }

        Write-Host "Running TypeScript checks..."
        & $npm.Source run typecheck
        if ($LASTEXITCODE -ne 0) { throw "Frontend typecheck failed." }

        Write-Host "Running frontend lint checks..."
        & $npm.Source run lint
        if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }

        Write-Host "Building the production frontend..."
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

        if ($IncludeE2E) {
            & $venvPython -c "import nltk, tokenizers, torch"
            if ($LASTEXITCODE -ne 0) {
                throw "End-to-end tests require the optional model runtime. Run .\scripts\bootstrap.ps1 -WithModelRuntime first."
            }
            Write-Host "Running Playwright end-to-end tests..."
            & $npm.Source run e2e
            if ($LASTEXITCODE -ne 0) { throw "End-to-end tests failed." }
        }
    } finally {
        Pop-Location
    }
}

Write-Host "All requested checks passed."
