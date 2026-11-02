# OSVEV - Open Source Video Enhancement Visualizer

### Development Transparency
This application was developed with AI assistance and has undergone human review and testing. The program itself does not use AI. As with any AI-assisted software, additional scrutiny and verification are recommended before critical use.

---

OSVEV is a tool for visualizing the effects of different video adjustment filters and enhancements.

⚠️ **Early beta / development preview. Use it as an inspection and teaching aid; verify all findings independently.** See [Important Disclaimers](#important-disclaimers).

## Download

Prerelease Windows builds, when available, are published on the
[Releases](https://github.com/btc-git/osvev/releases) page with a SHA-256
checksum. Compare the checksum before running the executable. `OSVEV.exe` is
not code-signed, so Windows SmartScreen may warn the first time it runs
(**More info** -> **Run anyway**). No Python, conda or FFmpeg installation is
needed for the packaged executable.

## Privacy & Security

- OSVEV works offline. It does not upload videos, frames, logs or usage data.
  Links in **About** and the export dialog open in your web browser only when
  clicked.
- Opening a video does not modify it. Exports and working copies are written
  as new files.
- Export logs, optional JSON/JSONL verification files and working-copy logs
  record full source and output paths, file names, hashes and build details.
  Folder names may identify cases or clients; review these files before
  sharing them.

## Important Disclaimers

- **This tool is in continuous development and may contain errors.**
- The Main View, Magnified View, interpolation, highlights and JPEG simulation
  are visual aids. Only exports verified as described in
  [Export and reproducibility](EXPORTING.md) carry OSVEV's documented
  verification claims, and only within those stated limits.
- FFprobe Info and the file row report header values from the file; they are
  not verified against decoded frames.
- Work from a copy of original recordings and preserve the original with its
  hash.
- This tool does not replace professional forensic video analysis, validated
  laboratory procedures or expert review.

## Installation and building

OSVEV 0.1.0 needs GPL-enabled FFmpeg libraries, because the shared
brightness/contrast filter is FFmpeg `eq`. The ordinary PyPI PyAV wheel
does not include it. The supported development environment is the conda
environment defined by [environment-eq.yml](environment-eq.yml):

- Python 3.11
- conda-forge PyAV 18.1.0 and FFmpeg 9 (built with `--enable-gpl --enable-version3`)
- conda-forge PyQt6/Qt 6
- OpenCV and NumPy (with OpenBLAS, not Intel MKL)
- PyInstaller

### One-time setup

```powershell
conda env create -f environment-eq.yml      # creates the permanent "osvev" environment
```

To update after `environment-eq.yml` changes, run
`conda env update -n osvev -f environment-eq.yml --prune`.

Use conda-forge `pyqt6` in this environment, **not pip PyQt6**. Mixing pip
Qt with conda FFmpeg/ICU caused a Windows missing-procedure DLL error. A
pip/PyPI PyAV environment lacks `eq`; do not use one for this version.

### Run from source

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

### Build the self-contained Windows executable

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
- `LICENSE`, [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and each bundled
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

## License

Copyright © 2026 CrimLawTech LLC.

OSVEV is free software (SPDX: `GPL-3.0-or-later`). You can
redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. See [LICENSE](LICENSE).

The packaged executable also includes FFmpeg (GPL v3 build), PyQt6 (GPL v3),
Qt (LGPL v3 main modules and GPL v3 Multimedia), OpenCV, NumPy, Python and other
components. The **About** dialog and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) list their licenses and exact
dependency source/build directions. Release contents and publishing notes are in
that file's "Release distribution" section.

## Quick Start

1. Check that all dependencies are installed (from the activated environment):
   ```
   python check_dependencies.py
   ```
2. Run `python main.py` (or `.\launch-eq.ps1`, or the packaged `OSVEV.exe`).
3. Click **Open**, select a file, then choose **Entire Video** (default) or
   **Selected Time Range** for part of a long video.
4. Use **About** for the version, commit, FFmpeg build and licenses.

### Default verified input decoder and explicit legacy option

PyAV is the default input decoder. OSVEV uses standard FFmpeg
`eq` for brightness/contrast and `unsharp` for sharpening.
Run inside the `osvev` environment above (or explicitly select `--decoder pyav`):

```powershell
python main.py
```

Use `python main.py --decoder opencv` only to explicitly select the legacy
decoder. PyAV is also required for nonzero core enhancements with OpenCV input.
A PyAV failure is reported;
there is **no automatic fallback** to OpenCV.

PyAV retains the current native decoded Y, U, V planes separately from the
explicit BT.601/BT.709, limited/full-range to BGR conversion. It reuses one
decoder and converter, not FFmpeg subprocesses, rendered previews, or a
video-frame cache. Exact seeking uses integer source PTS from the existing
cancellable FFprobe scan, not frame index divided by average FPS.

The inspector's **Native Source YCbCr** row shows the decoded **native source
Y′/Cb/Cr** samples at the crosshair's source pixel in PyAV mode (never values
reconstructed from RGB; legacy OpenCV reports them as unavailable). Cb/Cr are
the stored shared 4:2:0 samples at `(x // 2, y // 2)`, not upsampled per-pixel
estimates; the value-cell tooltip gives coordinates, matrix, range, chroma
location and color provenance. The frame-info panel identifies the selected conversion
and native sample format. Neither enhancements nor overlays modify those
source planes.

The deliberately narrow supported YUV input is progressive, even-sized, 8-bit
H.264 or HEVC `yuv420p`/`yuvj420p`, left- or center-sited chroma, explicit BT.709 or
SMPTE170M/BT.601 matrix, limited/full range, and BT.709/SMPTE170M SDR
primaries/transfer tags. Unspecified color fields may instead use the explicitly
authorized assumptions described under "Unspecified source color metadata";
they are not verified source color properties. Both Open dialogs offer MP4, MOV, AVI, MKV and M4V.
These are container choices, not permission to bypass codec/metadata checks.
Full opening can establish an undeclared frame count from the complete validated
FFprobe index; a declared count must agree. Open Time Range still requires a
positive declared container frame count and exact packet/frame correspondence.
AVI files without explicit frame PTS (including some ordinary H.264 remuxes)
remain rejected; best-effort timestamps are not substituted.
A usable stream
rate/time base, present nondecreasing integer PTS, and complete matching FFprobe frame
metadata are required. Unspecified color fields require authorization; contradictory,
changing, or unsupported metadata remains rejected. This includes HDR, interlace,
other bit depths/formats/codecs, other chroma siting, and dynamic geometry.
Decoder-flagged corrupt frames are refused when encountered, not displayed or
accepted as verified source pixels. Playback/navigation retains the previous
accepted frame and reports the failure. Initial opening is not a full-file
corruption certification. Export/reference decoding and working-copy conversion
also stop on decode errors; transcoding cannot establish missing original pixels.
Color, timing and recovered-count permissions do not waive this safeguard.
Rotation tags and display matrices (including zero-angle flip/shear matrices)
are rejected, not silently ignored or applied. Color-managed monitor reproduction
is not implemented. No runtime-version-independent hash guarantee is claimed.
The existing packaged executable has **not** been rebuilt or certified.

Verified full-video input also accepts progressive 8-bit packed RGB-family
FFV1 (`bgr0`, `rgb0`, `bgr24`, `rgb24`), including OSVEV's own BGR0/MKV exports.
Explicit full-range RGB (`gbr`) matrix and BT.709/SMPTE170M SDR primaries/transfer
are required. RGB channel packing preserves exact BGR values without a YUV
conversion. Native inspection reports R/G/B, not invented Y/U/V. Native RGB
hashes use packed B,G,R samples, excluding unused X bytes and row padding;
YUV hash records retain their existing meaning. RGB input may have odd dimensions.
FFV1 Open Time Range is intentionally unsupported; open the full video instead.

Small-fixture coverage in `tests/test_video_inputs.py`:

| Input | Full Open | Open Time Range |
|---|---|---|
| Tagged 8-bit YUV420 H.264, MP4/MOV/MP4-family M4V | Accepted; pixels, seeking, numbering and lossless export verified | Accepted with declared counts and exact packet/frame match |
| Same H.264, MKV | Accepted using the validated full index count | Rejected when no total count is declared |
| H.264 AVI remux fixture | Rejected: FFprobe does not expose integer frame PTS | Rejected: packet/frame metadata mismatch |
| Tagged 8-bit YUV420 HEVC, MP4/MOV/MP4-family M4V | Accepted; color controls, pixels, seeking and lossless export verified | Accepted with declared counts and exact packet/frame match |
| Same HEVC, MKV | Accepted using the validated full index count | Rejected when no total count is declared |
| Actual OSVEV BGR0 FFV1/MKV exports, including odd dimensions | Exact pixels, RGB inspection, seeking and re-export verified | Explicitly unsupported |

Packed RGB aliases (`rgb0`, `bgr24`, `rgb24`) have constructed-frame packing
coverage; file-level FFV1 round-trip coverage is BGR0. HEVC AVI, other FFV1
containers, planar/alpha/high-bit-depth FFV1, and other codecs are not certified.
HEVC controls cover BT.709/SMPTE170M and limited/full range; source B-frames,
nonzero/irregular PTS, invalid color/timing and display transforms are covered too.

**Measured trade-off:** local offscreen playback remains near 30 FPS, but
exact keyframe-based seeking can be slower. The tested gas-station clip's
last-frame seek took about 210 ms with PyAV versus 50 ms with OpenCV. This
limitation remains with the new default; no seek cache or larger architecture was added
to conceal that limitation.

## Features

- Frame-by-frame navigation
- Variable playback speed
- Advanced magnification with multiple interpolation methods:
  - Nearest neighbor
  - Bilinear
  - Bicubic
  - Lanczos
- Image adjustments:
  - Brightness and contrast via standard FFmpeg `eq` on explicit full-range
    BT.709 YUV444 luma (`brightness=slider/100`, `contrast=1+slider/100`),
    then conversion back to BGR; not the legacy OpenCV adjustment algorithm
  - Sharpness adjustment (FFmpeg 5x5 luma unsharp)
  - Exact top/bottom/left/right pixel crop, after enhancements, without output resizing
  - **Rotate** (0° Original, 90° clockwise, 180°, 270° clockwise): exact 90-degree
    pixel rotation after crop and before Zoom Level, without interpolation; applied
    to the Main View, Magnified View, Pixel Inspector mapping and verified exports
  - JPEG compression teaching simulation (after the core enhancements)
- Detailed frame information panel
- **File** row above Play Speed: the loaded file's name (hover for the full path;
  click to show it selected in its folder), labeled **Codec**, **Resolution**,
  **Avg FPS** and **Size** attributes, and an **FFprobe Info** button that opens a list of container
  and stream header values (full path, container, brands, writing application,
  codec profile/level, duration, bit rates, frame rates, color tags, audio streams), with Copy and
  Show in Folder. These values are read once by FFprobe when the video is loaded;
  they are reported header values and are not verified against decoded frames.
  Resolution is before crop/rotation/zoom; average FPS is not playback speed or
  a claim of constant frame rate. Unreported values are not guessed. Attributes
  wrap when needed, and the dark-blue filename is shortened to fit.
  After a working-copy conversion the row identifies the loaded working copy.
- Pixel Inspector with two value rows plus Image Adjustments
  - **Native Source YCbCr** (or **Native Source RGB** for native RGB sources):
    decoded source samples at the crosshair's source pixel, unchanged by enhancements
  - **Processed RGB**: the processed pixel under the Main View crosshair after
    enhancements, crop, Zoom Level and the selected interpolation
- **Inspection Aids (Preview Only)** control group: Highlight Original Pixels /
  Highlight Interpolated Pixels (schematic, display-only) and JPEG Compression
  Simulation; both are excluded from exports and working copies
- Optional **Selected Time Range** in Open for long videos (PyAV decoder only; see below)

### Open Time Range (partial indexing)

After file selection, **Open** defaults to **Entire Video**, which indexes the
whole video exactly as before, including FFV1 and sources ineligible for partial
indexing. **Selected Time Range** asks for a start and an exclusive end as source
presentation timestamps (seconds or
`HH:MM:SS.sss`, the same values as the frame panel's *Source Timestamp*). Frames with
`start <= timestamp < end` become the working timeline for navigation, playback,
magnification, pixel inspection and export.

The scope dialog keeps Start/End visible (disabled for Entire Video) and retains
the same compact size in either mode. A cancellable, time-limited header-only
FFprobe query displays reported duration, resolution, and file size; missing
values are unknown. This query does not decode frames or build an index.

When reopening the currently loaded, fully indexed source, defaults use the
first frame timestamp and the last timestamp plus its explicit frame duration,
if available. For a newly selected file, headers cannot verify the final frame's
exclusive end without indexing. Editable defaults use the reported video-stream
start plus its duration, or the container start plus container duration when
stream duration is unavailable. Unknown starts default to zero; negative starts
are limited to zero to respect existing range validation. Missing/unusable
duration retains the 60-second fallback. Reported duration and estimated defaults
use `HH:MM:SS.sss`; estimated starts round down and exclusive ends round up to
milliseconds. Existing indexed boundaries retain finer precision when necessary.
These metadata-based defaults may be approximate, not verified last-frame timing.
User-edited values are not overwritten by late header
results. Original source timestamps, including nonzero starts, are not rebased.

How exact correspondence is kept, without clips, transcoding or source changes:

- FFprobe decodes frame metadata only around the interval (`-read_intervals`, from
  2 s before the start to 5 s after the end), then keeps frames inside it.
- Frames before the interval are **counted** by demuxing packets with PyAV (no decoding),
  reading compressed data up to the interval end + 5 s. Their count is the offset that
  maps the working timeline to original source frame numbers.
- The two independent lists must agree exactly (selected frame PTS == demuxed packet PTS
  in the interval) and must fit the container's declared frame count; otherwise the open
  is refused rather than guessed. Constant FPS is never assumed; VFR is supported.
- Seeking uses the exact source PTS of each frame, from the preceding keyframe, so ranges
  that start between keyframes decode correctly.
- The frame panel shows `Source Frame` as the original number (e.g. 5318 of 19944) plus
  a `Loaded Range Frame` row.

Measured on a 19,944-frame, 11-minute VFR H.264 file (full scan 16.0 s): 10 s range 0.61 s,
1-minute ranges about 1.7 s, 2-minute range 3.2 s, 9-minute range 11.9 s. Each partial result
was identical to the matching slice of the full scan. Loading cost grows with the range length
and, slightly, with how far into the file it starts (packet counting).

Limitations: PyAV decoder only (OpenCV legacy mode is not supported); the container must
declare its frame count and satisfy the packet/frame PTS cross-check (the tested
MP4/MOV/MP4-family M4V fixtures do). A container extension alone does not establish
eligibility. MKV with an undeclared total and the tested H.264 AVI remux are rejected;
FFV1 partial opening is explicitly unsupported. Full Open can establish an undeclared
count from the validated full index, without estimating from FPS.
Exports still decode from the start of the file
(see [EXPORTING.md](EXPORTING.md)); times are stream presentation timestamps, which can
differ from a player's clock if the stream does not start at 0.

## Teaching-viewer guarantees and limits

- Videos are opened directly; there is no mandatory working-copy transcode.
- With brightness, contrast, sharpness, and JPEG compression simulation all at zero,
  the processed frame is byte-identical to the selected decoder's 8-bit BGR baseline.
  Processing never modifies that decoded source array.
- The Main View shows the processed frame resized directly to the window's
  aspect-fit size, rendered at the screen's physical pixel density (for example
  2x at 200% Windows scaling) so the display does not resample it again. When the
  frame must shrink, it is area-averaged (OpenCV INTER_AREA) so every source pixel
  contributes instead of most being skipped. When it is enlarged at Zoom Level 1x,
  or with Nearest selected, each displayed pixel repeats the source pixel given by
  the Pixel Inspector's exact integer mapping (position × source size // displayed
  size); above 1x with Bilinear, Bicubic or Lanczos it uses that OpenCV method.
  This is a display preview only; it is not a
  Zoom Level enlargement and need not match a full-zoom export downscaled.
  Zoom Level is rendered in the Magnified View (followed by its display-only
  Additional Zoom), not as a full-frame enlargement in the Main View: up to 30x
  full-frame resampling on every frame would be prohibitively large (e.g. 1080p
  at 30x is about 5.6 GB) and would then be resampled again to fit the window.
  Interpolation, Additional Zoom and highlights change only the displayed view,
  not the underlying frame or source file.
- Single-click the displayed video to center the magnifier crosshair on that
  displayed position's source pixel. At edges, the normal-sized selection box
  extends beyond the image and is clipped; only valid pixels are magnified,
  at the same zoom without stretching. Dragging uses the same clipping and keeps
  the cursor's grab offset; moving the pointer outside the video clamps the
  crosshair to the image boundary. Clicks outside the displayed video are ignored.
  Selection boundaries round outward to include partially covered source pixels,
  so even an enlarged 1x1 crop remains inspectable. Fast interpolation retains
  neighbors from the processed frame, including just outside the lens selection,
  to agree with Processed RGB without rendering a full zoomed frame.
- **Processed RGB** reads the pixel of the processed image *at Zoom Level*
  under the Main View crosshair, so it addresses interpolated pixels and changes
  with Zoom Level and interpolation. It is computed with the same OpenCV
  resampling as the magnifier (from a local neighborhood that is bit-identical to
  resizing the whole frame), before highlights, Additional Zoom, overlays or
  window fit. The crosshair's precision within an enlarged pixel is limited by
  the Main View's window-fit scale. The native row maps that position back to
  its source sample (zoomed position // Zoom Level, plus crop offsets).
  The same row appends **source RGB** in parentheses: the actual decoded source
  pixel at that mapped location after its documented color interpretation,
  before enhancements, zoom or interpolation. This is neither native YCbCr nor
  an interpolated source value; no additional conversion is performed.
- Experimental **Match FFmpeg Export** (off by default, beside magnifier
  interpolation) uses the existing PyAV/FFmpeg scaling definitions. Once lens
  movement stops or playback pauses, a background render matches FFmpeg
  crop-and-scale of the same complete selected processed region at the same zoom.
  Paused frame stepping also requests a fresh exact render. Dragging and playback
  retain the fast OpenCV preview; superseded results are discarded. The magnifier
  tooltip distinguishes preview from completed exact rendering. Additional Zoom
  and highlights remain display-only. While paused, Processed RGB is replaced by
  the FFmpeg-matched value at the crosshair from that same render (marked
  "FFmpeg-matched"; "pending" until it completes, OpenCV preview during
  playback). Like the magnifier, it matches FFmpeg crop-and-scale of the selected
  region, not of the whole frame. This is
  not a match to a portion of a larger export with different crop boundaries, or
  to lossy encoded output. Large crops/high zoom can take longer; requests whose
  scaled BGR image exceeds 256 MiB are explicitly refused. Errors disable the
  option and show a warning. Exports, Main View, and enhancements are unchanged.
- The grid highlights explain enlargement positions. They do not certify
  that a grid position retained its original color; bilinear, bicubic, and
  Lanczos interpolation can change colors at those positions too.
- Source resolution, source pixel count, source timestamps, source frame
  duration, and average FPS are separate from view-only zoom and target
  playback FPS. Zoom does not increase the source video's resolution.
- Brightness adjustment can still change derived chroma after conversion
  back to BGR because of rounding and gamut clipping.
- Loading shows the current stage and scanned frame count. When the container
  declares a frame count, the metadata scan also shows its percentage (not an
  estimated whole-load completion time). Unknown counts and partial-range scans
  retain an animated bar. No extra probe is run to calculate progress.
- Loading runs off the GUI thread. Cancel interrupts metadata scanning, stops
  FFprobe, and discards the candidate video/index; the previous video remains
  paused. Cancellation is checked between native decoder/JSON-parser calls,
  which cannot be interrupted mid-call. Scrubbing also leaves playback paused.
- Playback loops using source-time deadlines when complete, increasing
  timestamps are available. Missing/invalid timestamps use an explicitly
  labelled average-FPS timing fallback. This is not a monitor/vsync-certified player.

### Convenience audio

The audio line shows Volume (0–100%, with its percentage), Mute, container audio
stream count, Qt-discovered track count, and the actual output state:
Loading, Playing, Paused, Off (1x playback
only), Not playing (outside audio track), Unavailable, and whether muted.
When Qt exposes multiple tracks, a compact selector switches its active audio
track without changing video playback, volume or mute. The selected codec comes
from Qt. Qt does not expose per-track channels/sample rate; **ch ?, Hz ?** mark
them unavailable for multitrack sources rather than assuming container and Qt track
indices correspond. For a single container stream and at most one Qt track,
codec, channels and sample rate retain their container metadata display.
"No audio streams" is distinguished from a stream that exists but cannot be
played (whose reason is shown beside it). The presence of a stream does not mean
it contains audible sound; no audio analysis is performed. Original audio plays through
Qt's independent media player at **1x only**, follows pause/resume and seeks, and
restarts at the loaded video/time-range loop boundary. Frame stepping and other
playback speeds are silent. Videos without audio retain video-only behavior.
Audio failures appear inline and do not prevent video inspection.

Audio follows the existing source-time playback clock with periodic correction
when reported drift exceeds 150 ms; startup, seeks, device buffering, and heavy
video processing can still cause audible/visible offsets. This is convenient
listening, **not sample-accurate forensic synchronization**. Missing/ambiguous
source timing disables audio rather than guessing. The explicit legacy OpenCV
decoder remains video-only.

**Exports never include audio.** The export dialog and processing log identify
this exclusion; video processing, export commands, and verification are unchanged.

### Unspecified source color metadata

Otherwise supported progressive 8-bit H.264/HEVC YUV420 sources with unspecified
color fields offer a one-time **Unspecified source color** dialog. **Cancel**
keeps the previous video paused. **Open Video** authorizes filling missing fields
only: choose BT.601 (default) or BT.709 and Limited (default) or Full range.
Explicit source values are never replaced. Missing primaries and transfer are
assumed SDR SMPTE170M for BT.601 or BT.709 for BT.709, following the effective
matrix; missing chroma location is assumed left (horizontal 0, vertical 128 in
FFmpeg's 1/256-luma-sample units). These are assumptions, not verified source
properties or a guarantee of matching the camera's intended colors. Untagged HDR
cannot be identified from absent tags alone.

The original metadata and native Y/U/V samples remain unchanged. The inspector
tooltip distinguishes reported, user-selected, and assumed values. Decoding,
seeking, partial-range indexing, standalone FFmpeg reproduction, and independent
export verification use the same authorized interpretation. Export logs include
original declarations, effective values and their provenance, and state that
source color intent could not be established. Lossless verification certifies
pixel agreement **under that interpretation**, not the correctness of the source
color assumptions. Reopen the source to choose a different missing-field policy.

Fully tagged left-chroma sources keep the existing conversion/processing path.
Explicit `chroma_location=center` uses bicubic YUV420-to-YUV444 chroma
reconstruction at horizontal/vertical 128/128 (1/256-luma-sample units), then
explicit matrix/range conversion to BGR. The intermediate YUV444 step is
necessary because the direct packed-RGB fast path can ignore chroma positioning.
The same stages are used by PyAV, timeline previews, and the independent FFmpeg
export command/verifier. Original native planes and metadata remain unchanged;
the inspector identifies siting, and export logs record it as reported.
The first displayed frame is converted after the verified index establishes
chroma location, including when opening a selected range.

Unsupported
formats, explicit HDR/unsupported color tags or HDR side data, conflicting
declarations, chroma locations other than left/center, changing formats/colors, invalid PTS,
and inconsistent counts remain rejected. RGB FFV1 requirements are unchanged.

### Playback timing and diagnostics

#### Experimental duplicate-PTS compatibility

Entire Video accepts tied presentation timestamps without changing or removing
frames. Source ordinal and timestamp occurrence identify each frame. Seeking
decodes/counts from an unambiguous timestamp anchor; ambiguous seek landings
restart from the beginning and check the indexed PTS sequence. Such seeks may
be slower. Selected Time Range retains its restriction when duplicate packet
timestamps prevent an unambiguous packet-to-frame index; open Entire Video.
Missing or decreasing frame PTS remain rejected.

Duplicate-PTS playback uses original integer PTS and the source time base,
without replacement timestamps or average-FPS timing. Every frame is submitted
in source order, including at increased speeds; this mode disables intentional
and catch-up frame selection. Frames sharing a timestamp have **no independently
established display interval**: OSVEV submits them on successive GUI timer turns,
not with invented media durations. Monitor display of every frame is not
guaranteed. Slow decoding/rendering may lag real time (and convenience audio).
Unique-PTS playback scheduling is unchanged. A positive explicit final-frame
duration is required to establish the loop boundary; if unavailable, opening is
rejected with an explicit error rather than inventing that boundary.

PNG exports retain all selected frames in source order. Their processing log
records original integer PTS, the source time base, timestamp occurrences, and
affected source frame numbers; PNG itself has no playback timing. FFV1/MKV
selections containing ties require **1x export speed**, nonnegative original
presentation times exactly representable in Matroska milliseconds, and an
explicit final duration also exactly representable in milliseconds. These
exports preserve **absolute original presentation times**, without the usual
rebasing or speed adjustment. Every output frame is independently checked for
exact BGR pixels, count, order and presentation time with **zero timestamp
tolerance**. Export logs preserve the source integer PTS and time base even if
the output time base differs. H.264/MP4 selections containing ties are rejected
because the encoder/muxer changes them. Tie-free selections retain the existing
verified export timing policy. No timestamps are silently repaired.

The Play Speed row shows **Time: HH:MM:SS.mmm / HH:MM:SS.mmm** from indexed
source presentation timestamps, relative to the first indexed frame. It follows
the displayed frame during playback, stepping, seeking and live drag previews;
duplicate timestamps display the same elapsed time. Total duration includes the
last frame's explicitly reported duration and is **unknown** when that duration
is unavailable; no frame/FPS estimate is used. Partial loads are labeled
**Time (loaded range)** and show elapsed time and duration for the indexed range,
not an unverified whole-video duration. Millisecond display rounding does not
change source timestamps.

The existing Play Speed row also shows **Actual** (observed source-frame index
progression per monotonic wall-clock second, including skipped frames),
**Displayed** (distinct video
paints/second over the preceding 5 seconds), and **Skipped** (eligible source
presentations missed through overload or paint coalescing, excluding intentional
fast-forward selection). Actual uses distinct completed Main View Qt paints in a
2-second window, capped at 120 samples, and needs at least 0.5 seconds of samples;
it shows `—` when insufficient. It
supports VFR and looping, but is not monitor/vsync timing or a latency measure.
Displayed needs two paints spanning at least 0.5 seconds. Live values show `—`
when paused or unavailable. Skipped includes a 5-second recent count, which
expires even without new paints; its cumulative total remains on pause.
All indicators use the normal Control Panel text style. Resume, speed
changes, successful seeking/stepping, and a new video reset them.
Actual is measured index progression, not source average FPS multiplied by the
speed setting. It includes intentional high-speed selection and overload skips;
Displayed counts only completed video paints. VFR can vary Actual FPS even at
correct real-time speed. The timestamp-based actual-speed multiplier remains in
the Play/Pause tooltip. These counts are not added to overlapping late-slot counters.

Frame Information also reports the actual source **Keyframe** flag, independently
of picture type; **Frames Since Previous I-Frame** (0 on an I-frame, unknown until
an I-frame is established inside the available index); and **Source Bitrate
(reported)** from the video stream in the existing FFprobe scan, when available.
I-frame distance is not labelled GOP length. Partial ranges do not infer I-frames
outside the range, and bitrate is never estimated from packet sizes. Missing
values are shown as `—`.

Both input backends share a monotonic-clock, single-shot playback scheduler.
Fractional frame intervals are retained in absolute deadlines rather than
rounded into a permanently repeating 33/34 ms timer. Variable source
timestamp intervals are respected; pause, scrubbing, and speed changes
reanchor the clock at the current frame.

Normal playback presents every frame if processing keeps up. After a
missed deadline, overdue presentation slots are omitted to recover source
time rather than permanently slowing down or rapidly repainting old
frames. Manual stepping and scrubbing remain exact and do not use this
catch-up policy. Faster playback deliberately presents every Nth frame
(N is the rounded speed multiplier), as before.

Small forward playback jumps decode sequentially instead of seeking back
to a keyframe for every displayed frame. This includes deliberate
fast-forward skips and short stall recovery. Larger jumps and loop
boundaries still seek; random PyAV scrubbing retains the measured seek
cost described above. No frame collection, background renderer, or
working-copy video has been added.

**Hover Play/Pause during playback** for a lightweight diagnostic:
completed Qt-paint FPS, interval median/p95/max, intentional source skips,
late presentation slots, missed paint slots, and source-timestamp versus
average-FPS timing mode. It updates about once per second, retains only
120 timing samples, and does not log to disk. Lens/adjustment repaints of
the same frame do not count as new playback frames. Statistics reset on
resume or speed changes. Late slots and missed paint slots overlap; do
not add them together.

These are completed GUI paint observations, **not a measurement of actual
monitor scanout or vsync**. Frame decoding and enhancements still execute
on the GUI thread. Heavy settings, higher-resolution inputs, random seeks,
or other system work can exceed the presentation budget; the clock
recovers timing, but cannot make such a stall itself invisible.

### Inputs and losslessness

#### Optional compatible working copies (experimental)

Recognized input compatibility failures offer **Create Compatible Working Copy**.
This is separate from normal opening and never changes the verified decoder's
requirements. Unrelated I/O errors and cancelled loading do not offer conversion.
For missing color tags, the existing interpretation dialog also offers this
alternative. Choosing it starts a separate, cancellable preflight, then lets you
choose a new filename and explicitly authorize any required assumptions.
If **Open Video** succeeds using authorized color assumptions, a working copy is
not required. Missing color declarations are not themselves corrupt pixels;
neither opening nor converting can establish what the missing declarations were.

Working-copy conversion currently processes the **entire source file**, even
when opening began with a selected time range. A shorter loaded range does not
shorten this conversion. The dialog states this and scrolls its explanations and
permissions when needed, keeping progress and action buttons accessible.
Conversion, full-index validation and per-frame verification are separate passes
and can take time. Lossless RGB copies can be much larger and less smooth to
play than a compressed original; they are optional compatibility derivatives,
not optimized playback proxies or universal file repair.

The working copy is **video-only FFV1/MKV, packed 8-bit RGB**. The original is
not modified. Audio, subtitles, data and other video streams are excluded.
This initial converter handles constant, even-sized, progressive 8-bit YUV420
MPEG-4 Part 2, H.264, HEVC and YUV FFV1 with supported SDR interpretation.
It refuses HDR, interlaced/high-bit-depth inputs, display transforms, changing
format/color declarations and unsupported or conflicting explicit color tags.
It reuses the verified left/center chroma conversion. Missing matrix/range need
explicit selections; missing primaries/transfer assume SDR matching the effective
matrix, and missing chroma assumes left. These are assumptions, not verified
source properties. Only unspecified fields are filled.

By default, original absolute presentation times and explicit positive frame
durations must be exactly representable in Matroska milliseconds. Negative or
decreasing times remain rejected. Existing duplicate PTS are retained.
When original PTS are missing, complete nondecreasing FFmpeg best-effort timing
can be used **only with explicit authorization**, and existing original PTS must
agree. This is labelled **reconstructed/derived timing, not original timestamps**.
No FPS-based reconstruction, automatic `+genpts`, rate changes or frame dropping
are performed. Incomplete best-effort timing remains rejected.

**Optional bounded timestamp precision compatibility:** the bundled FFmpeg
Matroska muxer writes a 1 ms time base even with a finer encoder time base; it
exposes no timestamp-scale control. For otherwise eligible inputs of any supported
container, a separate, initially unchecked authorization permits millisecond
quantization. Each **absolute** PTS is independently rounded to the nearest
millisecond (positive half ties upward), never by accumulating rounded intervals.
Every PTS must match that exact result and deviate by at most **0.5 ms**.
Distinct source times must remain distinct; newly introduced collisions are
rejected. Existing ties must remain ties. A positive duration below 0.5 ms,
which would round to zero, is instead stored as exactly 1 ms (deviation below
1 ms); only that packet's duration is set, and its original/output duration and
deviation are logged and verified like every other frame.
FFmpeg/Matroska may report a source duration truncated to milliseconds through
DefaultDuration, or rounded to the nearest millisecond through packet duration;
only those two positive values are accepted, with at most **1 ms** deviation.
Because the Matroska muxer omits a packet's duration when it equals either the
floor or ceiling of the output stream's DefaultDuration (e.g. 133/134 ms at
7.5 fps), such a frame may read back as the other bound. This is accepted only
when the source's nearest-millisecond duration is itself one of those two
bounds, the deviation stays within **1 ms**, and each affected frame's original
and output duration and deviation are logged under `matroska_default_duration`.
Reported duration is not assumed to be the interval to the next picture.
The container endpoint must equal the rounded last PTS plus one of those
permitted floor/nearest final durations; the packet/container tail can differ
from the demuxer's DefaultDuration. Both are logged and checked. Endpoint drift,
overall presentation-span drift, and every adjacent
PTS-interval deviation must each be at most **1 ms**. These are absolute limits,
not percentage guarantees for very short durations.

Original source gaps/overlaps are disclosed, not filled or explained away.
Any output discontinuity inconsistent with the independently quantized source
times, or any excess deviation, fails verification. The log records original
rational timing, every signed PTS/duration change, the policy/authorization,
maximum deviations and endpoint/span drift. This is a **timing-altered derivative,
not original-precision evidence**. Timing authorization is separate from color,
derived best-effort timing and recovered-count permissions. Unchecking it
retains exact-only conversion. No normal decoder or export tolerance is changed.

An AVI's declared frame count may describe empty/indexed timing slots rather than
decoded pictures. If its count disagrees, eligible working-copy preflight requires
strict independent FFmpeg/PyAV decoding to agree on every recovered frame's native
pixels, order and supported timing. A separate, initially unchecked authorization
is required to proceed despite the count discrepancy. No frames are invented,
duplicated or discarded to satisfy the header. The conversion log records the
declared and recovered counts, their difference, authorization and limitations.
This verifies **the recovered sequence, not the completeness of the recording**;
the discrepancy's cause is not established automatically, and missing source
material cannot be ruled out. Unexplained decode/error diagnostics, inconsistent
recovery and incompatible timestamps/durations remain rejected.

Other supported containers (MP4, MOV, M4V, MKV) can report more stored samples
than FFmpeg presents — for example an MP4/MOV edit list that flags leading
packets *discard*, or leading packets that precede the first keyframe and have
no decodable reference. If the shared packet-index audit fully explains the gap
and all other input validation passes, the original opens directly with only its
decoded frames and original timestamps, and a warning identifies the discrepancy.
Subsequent exports retain the declared, packet and decoded counts and the audit's
explanations in the processing log and optional detailed JSON. Frame numbers refer
to decoded presentation order, not container sample indices; verification does
not establish recording completeness.
Otherwise opening is refused and offers **Create Compatible Working Copy**; AVI
count mismatches always take that route. Working-copy preflight then additionally
requires the declared count to equal the demuxed packet count, every packet to
carry an integer timestamp, decoded frames to correspond one-to-one with packet
PTS, and every undecoded packet to be either demuxer-discard-flagged or before
the first keyframe. Any other missing packet, corrupt/untimed packet,
duplicated timestamp or inconsistent container index is refused. The same
separate authorization, strict corroboration and per-frame verification apply;
original timestamps are kept (not rebased). The log records declared, packet
and decoded counts, the undecoded packet PTS, cause and assumptions. Normal
input validation and export verification are unchanged.

If decoder output order shows local presentation-timestamp reversals (for
example 11.999 s followed by 11.866 s), working-copy preflight can plan to
present the decoded frames in their original container PTS order. This is
refused for derived/best-effort timing, duplicate PTS, displacement beyond 16
frames, or displaced frames that are not themselves in PTS order. Strict
independent decoding must corroborate every frame's pixels and native PTS in
decoder order, and a separate, initially unchecked authorization is required.
Each frame's pixels, original PTS and duration are kept; nothing is added,
duplicated, dropped or retimed. The log records the reversals, moved frames,
assumptions and a `source_frame`/`output_frame` mapping per frame, and the whole
output sequence is verified against that mapping. Direct opening is unchanged.

Before success, OSVEV checks every frame's order/count, interpreted BGR pixels,
presentation time and duration against independent decoding/conversion, checks
the container endpoint, rechecks the original SHA-256 and runs the existing
full-index compatibility validation. The adjacent `.conversion.jsonl` log
records both file hashes, FFmpeg command/version, stream omissions, reported and
effective colors with assumption provenance, and per-frame original/best-effort/
output timing and pixel hashes. Keep this log with the derivative: RGB inspection
of the working copy is **not original native-YUV inspection**, and its color tags
do not establish the original recording's intended interpretation. Later exports
describe the working copy as their source, not the original unsupported recording.

Conversion, full indexing and verification run in a worker; cancelling interrupts
FFmpeg and removes job-owned incomplete files. Existing files are never
overwritten. Only a successfully verified copy is offered for opening, and the
previously loaded video stays available until the new one opens. Raw frame memory
is bounded, but the complete metadata index grows with frame count. FFV1 files can
be much larger than their sources, and staging/publication temporarily requires
additional disk space. Small-fixture coverage is not a guarantee for arbitrary
damaged recordings or a full-size performance benchmark.

#### Existing verified inputs

Both file pickers offer MP4, MOV, AVI, MKV and M4V. These extensions are container
choices, not codec or verification guarantees. Verified inputs are the explicitly
tagged 8-bit SDR H.264/HEVC YUV420 and packed RGB-family FFV1 described above.
User-authorized missing-color inputs additionally permit reproducible decoding
and verified exports under a recorded interpretation, not verified source color intent.
The support matrix distinguishes accepted fixtures from tested rejection cases:
in particular, the H.264 AVI remux lacks explicit frame PTS and is rejected, not
certified as working AVI input. Partial indexing has additional count and
packet/frame correspondence requirements; full opening does not remove those.
Legacy OpenCV may decode other inputs but they are not certified; the default
PyAV path explicitly rejects inputs outside its narrower contract above. Native high-bit-depth,
HDR, interlaced and color-managed fidelity are outside this baseline's
guarantees. Variable presentation intervals require complete nondecreasing
integer PTS; duplicate-PTS compatibility and its full-index, playback and export
limitations are documented above. Video export also requires an explicit final frame duration.
Invalid frame count/rate or unreadable video
is reported instead of inventing timing.

**Unchanged OpenCV BGR pixels do not prove unchanged native source samples.**
For one included BT.709 sample, legacy OpenCV applies BT.601 conversion and
differs from the explicitly controlled BT.709 baseline even before effects.
The default PyAV path matches independently established native and BGR
hashes for all 5,393 frames of the two samples and the four matrix/range
controls. Correct metadata is still a precondition, not something a hash
can prove. A lossless codec cannot
undo a prior color conversion or chroma subsampling.

See [Fidelity and recovered features](FIDELITY_AND_FEATURES.md) for the
measured results, hash definitions, and the full earlier feature inventory.

### Read-only hash check

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

### Regression tests

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

## Controls

- **Open**: Choose a video, then Entire Video (default) or Selected Time Range
- **File** name (above Play Speed): Click to show the loaded file in its folder;
  **FFprobe Info** shows its container and stream header information
- **Reset**: Reset all adjustments to defaults
- **Frame** arrows: Navigate frames manually (**Left / Right** keys). Holding a key keeps
  stepping one frame at a time, waiting for each frame to display before the next step
  (so slow 4K decodes never build a backlog), and stops on release, at the loaded
  start/end, on any other key, or on a read error. A single press still wraps around.
- **I-Frame** arrows: Pause and navigate to the nearest preceding/following frame
  whose source `pict_type` is `I`, skipping the current frame. Keyframe flags are
  not substituted for frame type. Navigation stays within the loaded video or
  time range and does not wrap; unavailable frame types are reported explicitly.
  Keyboard shortcuts: **Shift+Left / Shift+Right**.
- **Play/Pause**: Toggle video playback (**Space**)
- **Timeline**: Click to seek immediately. Drag the handle smoothly for live
  previews from an independent decoder and processor, using the same verified
  frame index and color interpretation. Previews target up to 8 FPS, adapt to
  decoding/processing cost, and retain only the newest requested position.
  In-flight previews may lag a continuously moving handle; newer completed
  frames are never replaced by older results, and results from obsolete
  sessions or processing settings are discarded. The selected frame updates
  even while holding the mouse still. On release the normal decoder seeks accurately to
  the final frame. Seeking leaves playback paused and audio silent. During
  playback, the handle follows the displayed frame index without interpolation.
  Slow sources may preview less frequently; the final seek and GUI rendering
  retain their existing costs.
- **Magnifier**: Drag the orange box to select an area to magnify
- **Export**: Show/copy an ordinary FFmpeg command, execute with progress
  and cancellation, or verify an externally produced output.

## FFmpeg export and verification

The verified PyAV input path now supports command-driven **PNG**, **FFV1
MKV**, and **compatibility H.264 MP4** output for the current frame, an
inclusive range, or the whole video. PNG ranges produce numbered image
sequences. PNG/FFV1 preserve the intended processed RGB pixels; H.264
YUV420/CRF18 is explicitly **lossy**.

**Playing FFV1/MKV:** This format is lossless, but some video players may not
support it. Use [VLC](https://www.videolan.org/vlc/) or another FFV1/MKV-compatible
player.

The certified processing scope is **brightness, contrast, sharpening and crop**,
individually or combined. The live processor and generated command use the
same explicit definitions: one full-range BT.709 YUV444 conversion, standard
`eq` (brightness slider/100, contrast 1+slider/100), then 5x5 luma `unsharp`
and conversion back to BGR. Neutral settings bypass conversion exactly.
Brightness/contrast appearance intentionally differs from earlier
pre-`eq` OSVEV development versions.
Crop removes the exact requested pixels; a nonzero Rotate setting then applies
an exact lossless FFmpeg transpose (`transpose=dir=clock`, `hflip,vflip` or
`transpose=dir=cclock`) with swapped output dimensions where applicable, which
is recorded in the export log/report and verified per frame against the live
processor; export scaling then uses the selected
Zoom Level and Interpolation Method (Nearest, Bilinear, Bicubic or Lanczos).
Neither viewer's display behavior changes. Export FFmpeg interpolation can
differ from OpenCV/Qt display interpolation; scaled exports are not claimed to
match viewer pixels. PNG/FFV1 allow odd output dimensions; H.264 YUV420 rejects
odd final dimensions without silently changing them.
JPEG remains a teaching simulation but is explicitly excluded from dialog
exports, along with pixel highlighting, the right-side Magnified View,
Additional Zoom and inspection overlays. Legacy OpenCV input blocks verified export;
launch `python main.py --decoder pyav` and reload the source.

Use **Export** to encode and automatically verify, or **Copy command** to
run the displayed command independently in PowerShell. The existing
external-output verification API remains available without a dialog button.
Source start/end fields appear only for **Selected range**. Every selected
frame is compared with the existing processor plus export-only scaling
and independently filtered FFmpeg pixels.
By default, successful exports produce only the media and one human-readable
`.txt` processing log. It contains:

- the OSVEV version and Git commit;
- a timezone-aware creation time;
- the verification result;
- source/output filenames and SHA-256 hashes;
- dimensions, settings, crop, selection, speed and zoom;
- any accepted source frame-count discrepancy and its interpretation limits;
- the complete command;
- the FFmpeg version/build.

PNG sequences list every image and its hash. Detailed JSON/JSONL diagnostic
records are optional in the dialog. These include per-frame hashes, verification
statistics, hash commands and library versions. Internal per-frame verification
is unchanged. Existing files are not overwritten.
**Keep extra verification details (optional)** is off by default. Leave it off
for the usual media-plus-readable-log output; enable it for a detailed audit or
troubleshooting. The extra records are not needed to run the recorded export
command, but they preserve more verification evidence. Reproduction still needs
the original source and compatible recorded tools/settings; logs alone cannot
guarantee identical results across different runtimes.
Cancelled/failed jobs retain partial media **not verified**, with no text log.
When detailed diagnostics are enabled, the JSON records the failure and reason.
Rejected opens and failed working-copy conversions show errors but do not create
a verified export log or a general session log.
The working copy's `.conversion.jsonl` is different from these optional export
diagnostics: keep it with the MKV. It records the original-to-working-copy
conversion, including color assumptions and any authorized timing changes.

Video exports normally use the main viewer's selected playback speed.
All selected frames are retained, unlike the viewer's display-only fast-forward
omissions. Presentation times are normally rebased to zero, and timing is divided
by export speed and independently verified. **Exception: FFV1/MKV selections
containing duplicate PTS require 1x export speed and preserve absolute original
presentation times**, subject to the restrictions in
[Experimental duplicate-PTS compatibility](#experimental-duplicate-pts-compatibility).
PNGs ignore playback speed.

Exports warn before unusually large scaled resolutions (over 8192 pixels on
either axis or over 33,177,600 pixels), without imposing an arbitrary zoom cap.
Encoding may still reject resolutions beyond its own limits.

Audio, Additional Zoom, the right-side Magnified View, highlights, overlays,
and native-YUV preservation are not part of these RGB exports. See [Export and reproducibility](EXPORTING.md)
for the workflow, exact contracts, hashing domain, limits, and relevant
SWGDE reference sections.

## Troubleshooting

If you encounter issues:

1. From source, run `python check_dependencies.py` or `.\launch-eq.ps1 -CheckOnly` in the `osvev` environment.
2. `Reproducible enhancement processing failed: no filter eq` means a non-GPL
   PyAV was used, for example a pip/PyPI PyAV environment or an old build. Use the `osvev`
   environment or a build from it.
3. **About** shows which FFmpeg executable is in use; the packaged executable
   reports a path inside its temporary `_MEI...` extraction directory.

## Technical Notes

OSVEV uses PyQt6 for its GUI framework to provide better high DPI display support and modern UI scaling capabilities.

Ignored executables in `dist` may have been built from a different branch and
are not updated by checkout. Check **About** for the version and commit.
