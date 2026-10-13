#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$WithModelRuntime
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot

$pythonCommand = $null
$pythonPrefix = @()
$pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $pythonCommand = $pyLauncher.Source
    $pythonPrefix = @("-3.11")
} else {
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python 3.11 was not found. Install Python 3.11 and run this script again."
    }
    $pythonCommand = $python.Source
}

$hostVersion = & $pythonCommand @pythonPrefix -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $hostVersion.Trim() -ne "3.11") {
    throw "ACCS-Trace requires Python 3.11.x; found '$hostVersion'."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "Creating Python 3.11 virtual environment..."
    & $pythonCommand @pythonPrefix -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv." }
}
$venvVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $venvVersion.Trim() -ne "3.11") {
    throw "The existing .venv does not use Python 3.11. Move it aside and run bootstrap again."
}

$lockFile = Join-Path $projectRoot "requirements-lock.txt"
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "requirements-lock.txt is missing; refusing an unpinned Python install."
}

Write-Host "Installing the exact Python dependency lock..."
& $venvPython -m pip install --disable-pip-version-check --requirement $lockFile
if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements-lock.txt." }

if ($WithModelRuntime) {
    $modelLockFile = Join-Path $projectRoot "requirements-model-runtime-win-x64-lock.txt"
    if (-not (Test-Path -LiteralPath $modelLockFile -PathType Leaf)) {
        throw "requirements-model-runtime-win-x64-lock.txt is missing; refusing an unpinned model runtime install."
    }
    $venvMachine = (& $venvPython -c "import platform; print(platform.machine().lower())").Trim()
    if ($LASTEXITCODE -ne 0 -or $venvMachine -notin @("amd64", "x86_64")) {
        throw "The published model runtime lock supports Windows x86-64 only. Generate and validate a platform lock before continuing."
    }
    Write-Host "Installing the exact Windows x86-64 ACCS TorchScript CPU runtime dependency lock..."
    & $venvPython -m pip install --disable-pip-version-check --require-hashes --extra-index-url "https://download.pytorch.org/whl/cpu" --requirement $modelLockFile
    if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements-model-runtime-win-x64-lock.txt." }
}

Write-Host "Installing ACCS-Trace as an editable package without resolving dependencies..."
& $venvPython -m pip install --disable-pip-version-check --no-deps --no-build-isolation --editable "."
if ($LASTEXITCODE -ne 0) { throw "Failed to install the editable ACCS-Trace package." }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "The locked Python environment is inconsistent." }

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envFile
    Write-Host "Created .env from .env.example."
}

Write-Host "Initializing the local database and built-in demo runtime..."
$initialize = @'
from app.config import get_settings
from app.database import build_engine, build_session_factory, run_migrations
from app.seed import seed_builtin_models

settings = get_settings()
settings.ensure_directories()
engine = build_engine(settings)
run_migrations(settings)
with build_session_factory(engine)() as session:
    seed_builtin_models(session)
engine.dispose()
print(settings.resolved_database_url)
'@
& $venvPython -c $initialize
if ($LASTEXITCODE -ne 0) { throw "Failed to initialize the database." }

if (-not $SkipFrontend) {
    $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    $node = Get-Command "node.exe" -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm was not found. Install Node.js 20 or newer, or use -SkipFrontend."
    }
    if (-not $node) {
        throw "node.exe was not found. Install Node.js 20.19+ or 22.12+."
    }
    $nodeVersionText = (& $node.Source -p "process.versions.node").Trim()
    $nodeVersion = [version]$nodeVersionText
    $supportedNode = (
        ($nodeVersion.Major -eq 20 -and $nodeVersion -ge [version]"20.19.0") -or
        ($nodeVersion.Major -ge 22 -and $nodeVersion -ge [version]"22.12.0")
    )
    if (-not $supportedNode) {
        throw "Vite requires Node.js 20.19+ or 22.12+; found $nodeVersionText."
    }
    Write-Host "Installing frontend dependencies from package-lock.json..."
    Push-Location -LiteralPath (Join-Path $projectRoot "frontend")
    try {
        & $npm.Source ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
    } finally {
        Pop-Location
    }
}

$installedOptions = @()
if ($WithModelRuntime) { $installedOptions += "ACCS model runtime" }
if ($installedOptions.Count -gt 0) {
    Write-Host ("Bootstrap complete with optional components: " + ($installedOptions -join ", ") + ".")
} else {
    Write-Host "Bootstrap complete with the lightweight demo runtime. Use -WithModelRuntime to install the optional TorchScript model runtime."
}
Write-Host "Run .\scripts\dev.ps1 from the repository root."
