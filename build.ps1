# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
[CmdletBinding()]
param(
    [string]$EnvironmentPrefix = $env:OSVEV_EQ_PREFIX
)

$ErrorActionPreference = 'Stop'
if (-not $EnvironmentPrefix) {
    $settingsPath = Join-Path $PSScriptRoot '.vscode\settings.json'
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $EnvironmentPrefix = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).'osvev.eqEnvironmentPrefix'
    }
}
if (-not $EnvironmentPrefix -and $env:CONDA_PREFIX -and (Split-Path $env:CONDA_PREFIX -Leaf) -eq 'osvev') {
    $EnvironmentPrefix = $env:CONDA_PREFIX
}
if (-not $EnvironmentPrefix) {
    # The permanent environment created with: conda env create -f environment-eq.yml
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if (-not $conda -and (Test-Path "$env:USERPROFILE\miniconda3\Scripts\conda.exe")) { $conda = "$env:USERPROFILE\miniconda3\Scripts\conda.exe" }
    if ($conda) {
        $EnvironmentPrefix = (& $conda env list --json | ConvertFrom-Json).envs |
            Where-Object { (Split-Path $_ -Leaf) -eq 'osvev' } | Select-Object -First 1
    }
}
$Python = Join-Path $EnvironmentPrefix 'python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "OSVEV conda environment not found ($EnvironmentPrefix). Create it with: conda env create -f environment-eq.yml"
}
$EnvironmentPrefix = (Resolve-Path -LiteralPath $EnvironmentPrefix).Path
$Python = Join-Path $EnvironmentPrefix 'python.exe'
$oldPath = $env:PATH
$oldUserSite = $env:PYTHONNOUSERSITE
$env:PATH = "$EnvironmentPrefix\Library\bin;$EnvironmentPrefix;$EnvironmentPrefix\Scripts;$oldPath"
$env:PYTHONNOUSERSITE = '1'
Push-Location $PSScriptRoot
try {
    & $Python -c "import PyInstaller, PyQt6.QtWidgets, cv2, numpy, av; from packaging.version import Version; assert Version(PyInstaller.__version__) >= Version('6'), 'PyInstaller 6 or newer required'; assert av.__version__ == '18.1.0', 'Validated PyAV 18.1.0 required'; av.filter.Graph().add('eq'); print('PyInstaller', PyInstaller.__version__, '| PyAV', av.__version__, '| libavfilter', av.library_versions['libavfilter'], '| eq available')"
    if ($LASTEXITCODE -ne 0) {
        throw "Build dependencies are missing or lack GPL eq in $Python."
    }
    foreach ($tool in @('ffmpeg', 'ffprobe')) {
        # These exact executables are bundled into OSVEV.exe by osvev.spec.
        $tool = Join-Path $EnvironmentPrefix "Library\bin\$tool.exe"
        $version = & $tool -version
        if ($LASTEXITCODE -ne 0) {
            throw "$tool could not run. Repair its installation before building."
        }
        $version | Select-Object -First 1
    }
    $work = Join-Path $PSScriptRoot 'build'
    $staging = Join-Path $work 'staging'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    # PyInstaller logs to stderr; Windows PowerShell must not treat redirected log lines as errors.
    $ErrorActionPreference = 'Continue'
    & $Python -m PyInstaller --noconfirm --distpath $staging --workpath $work osvev.spec 2>&1 | ForEach-Object { "$_" }
    $pyinstallerExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($pyinstallerExit -ne 0) {
        throw "PyInstaller failed. dist\OSVEV.exe was not changed. See output above."
    }
    $built = Join-Path $staging 'OSVEV.exe'
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) {
        throw "Build did not produce the expected executable: $built"
    }
    $dist = Join-Path $PSScriptRoot 'dist'
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $executable = Join-Path $dist 'OSVEV.exe'
    try {
        Move-Item -LiteralPath $built -Destination $executable -Force
    }
    catch {
        throw "Could not replace $executable (close OSVEV if it is running). The new build is at $built."
    }
    $hash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$executable.sha256" -Value "$hash *OSVEV.exe" -Encoding Ascii
    Remove-Item -LiteralPath $staging -Recurse -Force
    Write-Host ""
    Write-Host "Built: $executable"
    Write-Host "SHA-256: $hash"
    Write-Host "Self-contained single-file application: GPL FFmpeg 9 ffmpeg.exe/ffprobe.exe are bundled and used regardless of PATH."
}
finally {
    Pop-Location
    $env:PATH = $oldPath
    $env:PYTHONNOUSERSITE = $oldUserSite
}
