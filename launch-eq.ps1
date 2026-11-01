# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
[CmdletBinding()]
param(
    [string]$EnvironmentPrefix = $env:OSVEV_EQ_PREFIX,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
if (-not $EnvironmentPrefix) {
    $settingsPath = Join-Path $PSScriptRoot '.vscode\settings.json'
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        $EnvironmentPrefix = $settings.'osvev.eqEnvironmentPrefix'
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
if (-not $EnvironmentPrefix) {
    throw 'OSVEV conda environment not found. Create it with: conda env create -f environment-eq.yml'
}
$EnvironmentPrefix = (Resolve-Path -LiteralPath $EnvironmentPrefix).Path
$python = Join-Path $EnvironmentPrefix 'python.exe'
$bin = Join-Path $EnvironmentPrefix 'Library\bin'
foreach ($file in @($python, (Join-Path $bin 'ffmpeg.exe'), (Join-Path $bin 'ffprobe.exe'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "Missing OSVEV dependency: $file. Create the environment using environment-eq.yml."
    }
}

$oldPath = $env:PATH
$oldUserSite = $env:PYTHONNOUSERSITE
try {
    $env:PATH = "$bin;$EnvironmentPrefix;$(Join-Path $EnvironmentPrefix 'Scripts');$oldPath"
    $env:PYTHONNOUSERSITE = '1'
    $probe = @'
import shutil
import subprocess
import sys
import av
from PyQt6.QtCore import QT_VERSION_STR

print("Python:", sys.executable)
print("PyAV:", av.__file__)
print("Libraries:", av.library_versions)
print("Qt:", QT_VERSION_STR)
for name in ("eq", "unsharp"):
    av.filter.Graph().add(name)
print("eq/unsharp: available")
for name in ("ffmpeg", "ffprobe"):
    executable = shutil.which(name)
    print(name + ":", executable)
    version = subprocess.check_output([executable, "-version"], text=True)
    print(version.splitlines()[0])
    if "--enable-gpl" not in version or not version.startswith(name + " version 9."):
        raise RuntimeError("OSVEV requires compatible GPL FFmpeg 9 executables.")
if av.library_versions["libavfilter"][0] != 12:
    raise RuntimeError("OSVEV was validated with libavfilter 12 (FFmpeg 9).")
'@
    $probe | & $python -I -
    if ($LASTEXITCODE -ne 0) {
        throw 'OSVEV dependency check failed; OSVEV was not launched.'
    }
    if (-not $CheckOnly) {
        & $python -s (Join-Path $PSScriptRoot 'main.py')
        if ($LASTEXITCODE -ne 0) {
            throw "OSVEV exited with code $LASTEXITCODE."
        }
    }
}
finally {
    $env:PATH = $oldPath
    $env:PYTHONNOUSERSITE = $oldUserSite
}
