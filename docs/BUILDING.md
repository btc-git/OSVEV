# Building and running OSVEV from source

Most users only need the packaged `OSVEV.exe`; see the [README](../README.md).

OSVEV 2.0.1 needs GPL-enabled FFmpeg libraries, because the shared
brightness/contrast filter is FFmpeg `eq`. The ordinary PyPI PyAV wheel
does not include it. The supported development environment is the conda
environment defined by [environment-eq.yml](../environment-eq.yml):

- Python 3.11
- conda-forge PyAV 18.1.0 and FFmpeg 9 (built with `--enable-gpl --enable-version3`)
- conda-forge PyQt6/Qt 6
- OpenCV and NumPy (with OpenBLAS, not Intel MKL)
- PyInstaller

## One-time setup

```powershell
conda env create -f environment-eq.yml      # creates the permanent "osvev" environment
```

To update after `environment-eq.yml` changes, run
`conda env update -n osvev -f environment-eq.yml --prune`.

Use conda-forge `pyqt6` in this environment, **not pip PyQt6**. Mixing pip
Qt with conda FFmpeg/ICU caused a Windows missing-procedure DLL error. A
pip/PyPI PyAV environment lacks `eq`; do not use one for this version.

## Run from source

```powershell
conda activate osvev
python main.py
```

Alternatively, run `.\launch-eq.ps1`. It needs no activation and checks the
interpreter, PyAV/FFmpeg versions and `eq`/`unsharp` first.
`-CheckOnly` prints those checks without opening the application.

The launcher and build script find the environment in this order:

1. `-EnvironmentPrefix`
2. `OSVEV_EQ_PREFIX`
3. the local VS Code `osvev.eqEnvironmentPrefix` setting
4. an activated environment named `osvev`
5. `conda env list` for `osvev`

In VS Code, use **Run and Debug -> OSVEV** or
**Terminal -> Run Task -> Run OSVEV**. The local `.vscode` files are
gitignored; point them at your own environment prefix.

When run from source, OSVEV uses the environment's own
`Library\bin\ffmpeg.exe`/`ffprobe.exe` before any system FFmpeg on PATH.
`OSVEV_FFMPEG_DIR` can name another directory explicitly.

## Build the self-contained Windows executable

From the activated environment, the familiar command works:

```powershell
conda activate osvev
pyinstaller osvev.spec
```

The checked script does the same without activation. It writes each build to a
new `dist\<timestamp-id>\OSVEV.exe` and never overwrites earlier builds:

```powershell
.\build.ps1
# or, if execution policy blocks scripts:
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

`osvev.spec` builds a **single-file, windowed `OSVEV.exe`** (about 130 MB) that
contains:

- Python and the application;
- PyAV with the environment's GPL FFmpeg 9 DLLs (`avfilter-12.dll`);
- the matching standalone `ffmpeg.exe` and `ffprobe.exe`;
- `LICENSE`, [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), and each bundled
  conda package's license files under `third_party_licenses\`.

The executable always uses its bundled FFmpeg/FFprobe, regardless of the system
PATH. Users do not need Python, conda or FFmpeg installed.

The spec refuses to build if:

- PyAV lacks `eq`;
- the environment's FFmpeg is missing; or
- the standalone FFmpeg lacks `eq`/`unsharp`.

The build records the Git commit, marked `+dirty` for uncommitted changes. It
is retained in **About > Build details** and in every processing log. About's
summary describes `+dirty` as a development build with uncommitted source changes.
Build details also retains the FFmpeg executable path, full version/configuration
output and library versions without crowding the main About text.

## Developer quick start

1. Check that all dependencies are installed (from the activated environment):
   ```
   python check_dependencies.py
   ```
2. Run `python main.py` (or `.\launch-eq.ps1`, or the packaged `OSVEV.exe`).
3. Click **Open**, select a file, then choose **Entire Video** (default) or
   **Selected Time Range** for part of a long video.
4. Use **About** for the version, commit, FFmpeg build and licenses.

## Read-only hash check

```powershell
python verify_fidelity.py path\to\recording.mp4 --frames 8
```

The development sample recordings referenced in measurements
(`short-clip.mp4`, `gas-station.mp4`) are not distributed with the source.

This prints JSON containing source-file SHA-256 before/after, per-frame BGR
SHA-256, neutral-processing hashes, a standalone FFmpeg BGR comparison, and
version/source-format information. It does not write media or logs. It checks
only the requested initial frame sample, not the whole video.

A decoder mismatch is explicitly reported by `opencv_ffmpeg_pixel_identical`.
The command exits nonzero for execution failures, a changed source file, or a
neutral-processing mismatch. A reported OpenCV/FFmpeg mismatch is an
investigation result, not an execution failure.

## Regression tests

```powershell
python -m unittest discover -s tests -v
```

Tests run the GUI offscreen and include cancellation, navigation, bounded
magnification, neutral hashes, and controlled FFV1/RGB-H.264/YUV-H.264 round
trips. FFmpeg must include FFV1, libx264, and libx264rgb for the codec tests.
The expanded input fixtures also require libx265. A three-generation neutral
OSVEV FFV1/MKV export/reopen/re-export test compares every independently decoded
BGR24 frame's SHA-256 and frame count, checks OSVEV's reopened BGR24 against the
same hashes, and verifies all source files remain unchanged. This tests decoded
pixel reproducibility, not byte-identical container files.
Optional PyAV tests verify complete sample hash manifests, controlled
matrix/range conversion, exact seeking (including B-frames and nonzero,
irregular PTS), metadata rejection, native inspection, cancellation, and
viewer lifecycle. They skip if PyAV is not installed. Whole-source checks use
two local development sample recordings (`short-clip.mp4`, `gas-station.mp4`)
that are not distributed with the source; those tests skip when the files are
absent from the repository root.
Playback regressions additionally cover fractional deadlines, irregular
timestamps, speed changes, stalls, loop timing, paint deduplication,
bounded diagnostics, and sequential fast-forward input fidelity.

## Troubleshooting

1. Run `python check_dependencies.py` or `.\launch-eq.ps1 -CheckOnly` in the `osvev` environment.
2. `Reproducible enhancement processing failed: no filter eq` means a non-GPL
   PyAV was used, for example a pip/PyPI PyAV environment or an old build. Use the `osvev`
   environment or a build from it.
3. **About** shows which FFmpeg executable is in use; the packaged executable
   reports a path inside its temporary `_MEI...` extraction directory.

## Technical notes

OSVEV uses PyQt6 for its GUI framework to provide better high DPI display support and modern UI scaling capabilities.

Ignored executables in `dist` may have been built from a different branch and
are not updated by checkout. Check **About** for the version and commit.
