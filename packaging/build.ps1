# Builds dist\Chronicle\ (BU126) and dist\ChronicleSetup-<version>.exe (BU127).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# -Test          also run the unit suite in the build venv
# -SkipInstaller stop after the app folder (no Inno Setup needed)
#
# Every run starts from a fresh venv made from requirements.txt, so nothing
# from a dev venv ends up in the bundle.
param(
    [switch]$Test,
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root 'build\venv'
$Dist = Join-Path $Root 'dist'
$AppDir = Join-Path $Dist 'Chronicle'
$PyInstallerVersion = '6.22.3'
$VcRedist = Join-Path $PSScriptRoot 'redist\vc_redist.x64.exe'
$VcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

$version = [regex]::Match(
    (Get-Content (Join-Path $Root 'src\config.py') -Raw),
    '(?m)^APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw 'APP_VERSION not found in src\config.py' }
Write-Host "Chronicle $version"

# 1. Clean build venv
if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
Invoke-Checked 'py' @('-3.12', '-m', 'venv', $Venv)
$Python = Join-Path $Venv 'Scripts\python.exe'
Invoke-Checked $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '--upgrade', 'pip')
Invoke-Checked $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q',
    '-r', (Join-Path $Root 'requirements.txt'), "pyinstaller==$PyInstallerVersion")

if ($Test) {
    Invoke-Checked $Python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q',
        '-r', (Join-Path $Root 'requirements-dev.txt'))
    Push-Location $Root
    try { & $Python -m pytest -q -p no:cacheprovider tests } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Write-Warning "Unit suite reported failures (exit $LASTEXITCODE)" }
}

# 2. App folder
Invoke-Checked $Python @('-m', 'PyInstaller', '--noconfirm', '--clean',
    '--distpath', $Dist, '--workpath', (Join-Path $Root 'build\pyinstaller'),
    (Join-Path $PSScriptRoot 'chronicle.spec'))

# Personal data and dev files must never ship.
$forbidden = Get-ChildItem $AppDir -Recurse -Force | Where-Object {
    $_.Name -match '^(chronicle\.db.*|preferences\.json|setup_state\.json|location\.json|\.env|sessions)$' -or
    $_.Extension -eq '.wav'
}
if ($forbidden) {
    $forbidden | ForEach-Object { Write-Host "  $($_.FullName)" }
    throw 'User data or dev files found in the bundle'
}

$size = (Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ('{0}: {1:N0} MB' -f $AppDir, ($size / 1MB))

if ($SkipInstaller) { return }

# 3. Installer
$iscc = @(
    $env:ISCC,
    (Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue | ForEach-Object Source),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found. Install it or set $env:ISCC.' }

if (-not (Test-Path $VcRedist)) {
    New-Item -ItemType Directory -Force (Split-Path $VcRedist) | Out-Null
    Write-Host "Downloading $VcRedistUrl"
    Invoke-WebRequest -Uri $VcRedistUrl -OutFile $VcRedist -UseBasicParsing
}

Invoke-Checked $iscc @("/DAppVersion=$version", (Join-Path $PSScriptRoot 'chronicle.iss'))
$setup = Join-Path $Dist "ChronicleSetup-$version.exe"
Write-Host ('{0}: {1:N0} MB' -f $setup, ((Get-Item $setup).Length / 1MB))
