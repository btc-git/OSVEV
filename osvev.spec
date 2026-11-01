# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
# Build from the activated OSVEV conda environment (environment-eq.yml):
#     pyinstaller osvev.spec
# or use build.ps1, which also writes each build to a new dist\<timestamp> folder.

import glob
import json
import os
from pathlib import Path
import subprocess
import sys

import av

# Enhancements require GPL FFmpeg's eq filter inside PyAV's own libraries.
try:
    av.filter.Graph().add('eq')
except Exception as error:
    raise SystemExit(
        f'{sys.executable} uses PyAV libavfilter {av.library_versions["libavfilter"]} without "eq". '
        'Build from the GPL conda environment (environment-eq.yml).'
    ) from error

prefix = Path(sys.prefix)
tool_dir = prefix / 'Library' / 'bin'
tools = [tool_dir / 'ffmpeg.exe', tool_dir / 'ffprobe.exe']
for tool in tools:
    if not tool.is_file():
        raise SystemExit(f'{tool} not found; build from the OSVEV conda environment.')
filters = subprocess.run([str(tools[0]), '-hide_banner', '-filters'], capture_output=True,
                         text=True, check=True).stdout.split()
if 'eq' not in filters or 'unsharp' not in filters:
    raise SystemExit(f'{tools[0]} lacks the GPL eq/unsharp filters.')

# Record the source commit in the bundle (not in the working tree).
root = Path(SPECPATH)
try:
    commit = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=root,
                            capture_output=True, text=True, check=True).stdout.strip()
    if subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root,
                      capture_output=True, text=True, check=True).stdout.strip():
        commit += '+dirty'
except (OSError, subprocess.CalledProcessError):
    commit = 'unknown'
generated = Path(workpath) / 'generated'
generated.mkdir(parents=True, exist_ok=True)
(generated / '_build_info.py').write_text(f'GIT_COMMIT = {commit!r}\n', encoding='utf-8')
print(f'OSVEV build commit: {commit}')

# License texts of every conda package and the pip wheels in the build environment.
license_datas = []
for record in glob.glob(str(prefix / 'conda-meta' / '*.json')):
    meta = json.loads(Path(record).read_text(encoding='utf-8'))
    licenses = Path(meta.get('extracted_package_dir', '')) / 'info' / 'licenses'
    if meta.get('extracted_package_dir') and licenses.is_dir():
        for file in licenses.rglob('*'):
            if file.is_file():
                target = Path('third_party_licenses') / meta['name'] / file.relative_to(licenses).parent
                license_datas.append((str(file), str(target)))
for pattern in ('opencv_python-*.dist-info', 'pyinstaller-*.dist-info'):
    for info in glob.glob(str(prefix / 'Lib' / 'site-packages' / pattern)):
        for file in Path(info).rglob('*'):
            if file.is_file() and ('LICENSE' in file.name.upper() or 'COPYING' in file.name.upper()):
                license_datas.append((str(file), str(Path('third_party_licenses') / Path(info).name.split('-')[0])))
for file in glob.glob(str(prefix / 'Lib' / 'site-packages' / 'cv2' / 'LICENSE*.txt')):
    license_datas.append((file, 'third_party_licenses/opencv_python'))

a = Analysis(
    ['main.py'],
    pathex=[str(generated)],
    # Beside PyAV's FFmpeg DLLs at the bundle root, so both use the same GPL libraries.
    binaries=[(str(tool), '.') for tool in tools],
    datas=[('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'), *license_datas],
    hiddenimports=[
        'magnifier_label',
        'frame_processor',
        'frame_info',
        'osvev_app',
        'ffmpeg_tools',
        'version',
        '_build_info',
        'PyQt6',
        'cv2',
        'numpy',
        'av',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OSVEV',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='osvev_icon.ico',
)
