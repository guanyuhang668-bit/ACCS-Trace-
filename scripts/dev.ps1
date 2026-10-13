#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8000,

    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$viteEntrypoint = Join-Path $projectRoot "frontend\node_modules\vite\bin\vite.js"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw ".venv is missing. Run .\scripts\bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $viteEntrypoint -PathType Leaf)) {
    throw "Frontend dependencies are missing. Run .\scripts\bootstrap.ps1 first."
}
$node = Get-Command "node.exe" -ErrorAction SilentlyContinue
if (-not $node) { throw "node.exe was not found." }

$logDir = Join-Path $projectRoot "data\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$env:ACCSTRACE_FRONTEND_ORIGIN = "http://127.0.0.1:$FrontendPort"

function Start-HiddenProcess {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList,
        [Parameter(Mandatory)] [string]$WorkingDirectory
    )

    $stdout = Join-Path $logDir "$Name.out.log"
    $stderr = Join-Path $logDir "$Name.err.log"
    return Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
}

$processes = @()
try {
    $processes += Start-HiddenProcess -Name "api" -FilePath $venvPython -WorkingDirectory $projectRoot -ArgumentList @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort"
    )
    $processes += Start-HiddenProcess -Name "worker" -FilePath $venvPython -WorkingDirectory $projectRoot -ArgumentList @(
        "-m", "app.worker"
    )
    $processes += Start-HiddenProcess -Name "frontend" -FilePath $node.Source -WorkingDirectory (Join-Path $projectRoot "frontend") -ArgumentList @(
        $viteEntrypoint, "--host", "127.0.0.1", "--port", "$FrontendPort", "--strictPort"
    )

    $healthUrl = "http://127.0.0.1:$ApiPort/api/health"
    $ready = $false
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        try {
            $null = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $ready) {
        throw "API did not become healthy. Inspect data\logs\api.err.log."
    }

    Write-Host "ACCS-Trace is running:"
    Write-Host "  Web:     http://127.0.0.1:$FrontendPort"
    Write-Host "  OpenAPI: http://127.0.0.1:$ApiPort/docs"
    Write-Host "  Logs:    $logDir"
    Write-Host "Press Ctrl+C to stop all three processes."

    while ($true) {
        foreach ($process in $processes) {
            if ($process.HasExited) {
                throw "A development process exited (PID $($process.Id), code $($process.ExitCode)). Inspect data\logs."
            }
        }
        Start-Sleep -Seconds 1
    }
} finally {
    foreach ($process in $processes) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
