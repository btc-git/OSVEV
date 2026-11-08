# OSVEV - Open Source Video Enhancement Visualizer

### Development Transparency
This application was developed with AI assistance and has undergone human review and testing. The program itself does not use AI. As with any AI-assisted software, additional scrutiny and verification are recommended before critical use.

---

OSVEV is a tool for visualizing the effects of different video adjustment filters and enhancements.

⚠️ **In active development and testing. Not a validated forensic tool; verify all findings independently.**
OSVEV is an inspection and teaching aid. See [Important Disclaimers](#important-disclaimers).

## Download

1. **[Download OSVEV.exe](https://github.com/btc-git/OSVEV/releases/latest/download/OSVEV.exe)**
   (latest version for Windows; no installation needed).
2. Double-click it to run. Windows may show "Windows protected your PC" because
   the app is not code-signed: click **More info** → **Run anyway**.

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
  [Export and reproducibility](docs/EXPORTING.md) carry OSVEV's documented
  verification claims, and only within those stated limits.
- FFprobe Info and the file row report header values from the file; they are
  not verified against decoded frames.
- Work from a copy of original recordings and preserve the original with its
  hash.
- This tool does not replace professional forensic video analysis, validated
  laboratory procedures or expert review.

## Quick Start

1. Download and run `OSVEV.exe` (see [Download](#download)).
2. Click **Open**, select a video, then choose **Entire Video**
   (default) or **Selected Time Range** for part of a long video.
3. Adjust brightness, contrast, sharpness, crop, rotation and zoom; use the
   magnifier and Pixel Inspector to examine pixels.
4. Click **Export** for verified PNG or FFV1/MKV output, or lossy H.264/MP4,
   each with a processing log.
5. Use **About** for the version, commit, FFmpeg build and licenses.

To run or build from source, see [Building from source](docs/BUILDING.md).

## Features

- Frame-by-frame, I-frame and timeline navigation with variable playback speed
- Magnifier with nearest-neighbor, bilinear, bicubic and Lanczos interpolation
- Brightness, contrast and sharpness (standard FFmpeg filters), exact crop,
  90-degree rotation and zoom
- Pixel Inspector showing native source and processed pixel values
- File information row and FFprobe header details
- Preview-only inspection aids: interpolated-pixel highlights and JPEG
  compression simulation
- Optional Selected Time Range for long videos
- Verified PNG and FFV1/MKV exports, or lossy H.264/MP4, with processing logs

See [Features and controls in detail](docs/TECHNICAL.md#features-and-controls-in-detail).

## Application Screenshot

<div align="center">
  <img src="screenshot.png" alt="OSVEV application interface" width="700">
</div>

## Controls

- **Open**: choose a video, then Entire Video or Selected Time Range
- **Left / Right**: step one frame; **Shift+Left / Shift+Right**: previous/next I-frame
- **Space**: play/pause
- **Timeline**: click or drag to seek
- **Magnifier**: drag the orange box to choose the magnified area
- **Reset**: restore default adjustments
- **Export**: run, copy or verify the FFmpeg export command
- **About**: version, disclaimers, licenses and build details

## Documentation

- [Technical reference](docs/TECHNICAL.md): supported inputs, Open Time Range,
  teaching-viewer guarantees and limits, color metadata, playback timing,
  working copies and losslessness.
- [Export and reproducibility](docs/EXPORTING.md): FFmpeg export, verification,
  processing logs and SWGDE references.
- [Building from source](docs/BUILDING.md): conda environment, running from
  source, building the executable, read-only hash check and regression tests.
- [Third-party notices](THIRD_PARTY_NOTICES.md): bundled components, licenses
  and release distribution.
- [Development notes](docs/development/): historical fidelity measurements,
  module structure and earlier export benchmarks.

## Troubleshooting

- Check the export's processing log for errors and warnings.
- **About** → **Build details** shows the version, source revision and FFmpeg
  build; include these when reporting a problem.
- Report problems on the [Issues](https://github.com/btc-git/OSVEV/issues) page.
  Do not attach case recordings or logs containing confidential paths.
- Running from source: see [Troubleshooting in Building from source](docs/BUILDING.md#troubleshooting).

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
