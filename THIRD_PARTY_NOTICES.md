# Third-party notices

OSVEV (Open Source Video Enhancement Visualizer)
Copyright © 2026 CrimLawTech LLC
SPDX-License-Identifier: GPL-3.0-or-later

OSVEV is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed WITHOUT ANY WARRANTY. See `LICENSE` for the full
GPLv3 text.

The Windows executable (`OSVEV.exe`) is a single-file PyInstaller bundle. Apart
from OSVEV's own source code, it contains the third-party components listed
below. Each component remains under its own license. The bundle combines
OSVEV with GPL-3.0-only components (PyQt6) and an FFmpeg build configured
with `--enable-gpl --enable-version3`. **The executable as a whole is therefore
distributed under the terms of GPL version 3.** OSVEV's own source remains
available under GPL-3.0-or-later.

The build collects available conda-forge package license texts under
`third_party_licenses\<package>\`, along with the OpenCV and PyInstaller wheel
licenses, including OpenCV's `LICENSE-3RD-PARTY.txt`. Collection depends on the
license files being present in the build environment/cache; inspect the actual
release bundle rather than assuming collection proves completeness.
A release archive should also ship this file,
`LICENSE` and that `third_party_licenses` folder next to the executable.
These come from conda's `info/licenses` folders and the wheels' `*.dist-info`.

## Bundled components (OSVEV 0.1.0 build)

Versions are those of the `osvev` environment created from
`environment-eq.yml` (conda-forge, Windows x64). They were verified against the
PyInstaller table of contents of the 0.1.0 build. Licenses are the package
metadata's SPDX expressions.

### Video processing

| Component | Version | License | Role |
|---|---|---|---|
| FFmpeg (`ffmpeg.exe`, `ffprobe.exe`, avcodec/avformat/avfilter/avutil/avdevice/swscale/swresample DLLs) | 9.0.2, conda-forge `gpl` build | Configured `--enable-gpl --enable-version3`, so the build is GPL-3.0-or-later. Conda metadata says GPL-2.0-or-later, which describes the source, not this configuration. | Decoding, `eq`/`unsharp`/crop/scale filtering, export, metadata. Full configuration is printed in **About** and every processing log. <https://ffmpeg.org/legal.html> |
| PyAV (`av`) | 18.1.0 | BSD-3-Clause | Python bindings to the same FFmpeg libraries |
| x264 (`libx264-164.dll`) | 164.3095 | GPL-2.0-or-later | H.264 encoder used for MP4 compatibility exports |
| x265 (`libx265.dll`) | 3.5 | GPL-2.0-or-later | Linked into FFmpeg; not used by OSVEV's exports |
| OpenH264 (`openh264-7.dll`) | 2.6.0 | BSD-2-Clause | Linked into FFmpeg; built from source by conda-forge, so Cisco's binary patent license does **not** apply |
| libaom, dav1d, SVT-AV1 | 3.14.1, 1.5.4, 4.2.0 | BSD-2-Clause | AV1 codecs linked into FFmpeg |
| libjxl, libwebp, Opus, Vorbis, Ogg, Highway | 0.12.0, 1.6.0, 1.6.1, 1.3.7, 1.3.5, 1.4.0 | BSD-3-Clause (Highway: Apache-2.0 OR BSD-3-Clause) | Codec libraries linked into FFmpeg |
| LAME, mpg123 | 4.0, 1.33.7 | LGPL-2.0-only, LGPL-2.1-only | MP3 libraries. LAME is statically linked into `avcodec-63.dll`; mpg123 ships as a separate DLL. OSVEV exports are video-only |
| OpenSSL | 3.6.5 | Apache-2.0 | Linked into FFmpeg; compatible with GPLv3 |

### User interface

| Component | Version | License | Role |
|---|---|---|---|
| Qt (Qt6Core, Qt6Gui, Qt6Widgets, Qt6Network, Qt6Svg and their platform/style/image-format/icon plugins) | 6.11.2 (`qt6-main`) | LGPL-3.0-only | GUI framework. No GPL-only Qt modules are bundled. <https://www.qt.io/licensing/open-source-lgpl-obligations> |
| PyQt6, PyQt6-sip | 6.11.0, 13.13.0 | GPL-3.0-only | Riverbank Computing Python bindings for Qt |
| ICU | 78.3 | MIT (Unicode License) | Unicode support for Qt |
| double-conversion, PCRE2 | 3.4.0, 10.47 | BSD-3-Clause | Qt support libraries |

### Python, numerics and image libraries

| Component | Version | License | Role |
|---|---|---|---|
| Python | 3.11.17 | PSF-2.0 (`Python-2.0`) | Runtime |
| Tcl/Tk (bundled by PyInstaller's standard-library collection; unused) | 8.6.13 | TCL (BSD-style) | Not used by OSVEV |
| SQLite | 3.54.0 | Public domain ("blessing") | Python standard library |
| NumPy | 2.4.6 | BSD-3-Clause | Frame arrays |
| OpenBLAS (`libblas`/`libcblas`/`liblapack`) | 0.3.34 | BSD-3-Clause | NumPy backend; Intel MKL deliberately excluded |
| OpenCV (`opencv-python` wheel) | 4.13.0.90 | Apache-2.0 | Display resizing and the legacy decoder. The wheel includes its own **LGPL-2.1 FFmpeg** (`opencv_videoio_ffmpeg`), used only by the explicitly selected legacy OpenCV decoder, and the notices in its `LICENSE-3RD-PARTY.txt` |
| PyInstaller bootloader | 6.22.2 | GPL-2.0-or-later with bootloader exception | Single-file launcher |

### Graphics, text and support libraries

| Component | Version | License | Role |
|---|---|---|---|
| GLib, Pango, librsvg, GDK-Pixbuf, FriBidi, libintl | 2.90.1, 1.58.2, 2.62.4, 2.44.8, 1.0.17, 0.22.5 | LGPL-2.1-or-later | Linked into FFmpeg (`librsvg`, text rendering) |
| libiconv | 1.18 | LGPL-2.1-only | Character conversion |
| cairo | 1.18.6 | LGPL-2.1-only OR MPL-1.1 | 2D graphics for librsvg/Pango |
| Graphite2 | 1.3.15 | LGPL-2.0-or-later (also MPL/GPL options) | Font shaping |
| FreeType | 2.14.3 | FTL OR GPL-2.0-only | Fonts |
| HarfBuzz, Fontconfig, libxml2, libexpat, libffi, Brotli, libdeflate, pixman, lcms2 | conda-forge (see table of contents) | MIT | Support libraries |
| zlib, zlib-ng, libpng, bzip2, xz, zstd, libjpeg-turbo, libtiff, OpenJPEG, Lerc | conda-forge | Zlib, libpng, bzip2, 0BSD/public domain, BSD-3-Clause, IJG AND BSD-3-Clause AND Zlib, HPND, BSD-2-Clause, Apache-2.0 | Compression and image formats |
| Microsoft Visual C++ 2015-2022 runtime, Universal CRT | 14.51, 10.0.26100 | Microsoft redistributable licenses | Windows runtime DLLs; redistribution as permitted by Microsoft's terms |

## Corresponding source and relinking

Redistributing covered GPL/LGPL binaries carries source-access obligations,
not just an obligation to include license notices. For online distribution,
[GPLv3 section 6(d)](https://www.gnu.org/licenses/gpl-3.0.html#section6) permits
Corresponding Source on a different server, including a third-party server,
with equivalent copying facilities and clear directions next to the binary.
The distributor remains responsible for availability. Users need not download
the source with the executable, and a separately hosted source ZIP is not the
only permitted approach.

Exact version/build-specific source locations can therefore be part of a release
plan where the applicable licenses permit them. A generic upstream homepage or
license link is not a substitute for matching source, patches and build material.
Other GPL/LGPL versions and component-specific terms must also be checked.
The written-offer alternatives have conditions of their own; a bare promise is
not the default substitute for source access on an online release.

Matching source material for the packaged components includes:

1. **OSVEV source** at the exact commit shown in **About** and in each processing
   log (<https://github.com/btc-git/osvev>). It includes `environment-eq.yml`,
   `osvev.spec` and `build.ps1`, which are the scripts used to build the
   executable (GPLv3 counts these as part of the corresponding source).
2. **Upstream source and conda-forge recipes** for each bundled GPL/LGPL package:
   - FFmpeg 9.0.2, x264, x265, LAME, mpg123;
   - Qt 6.11.2, PyQt6 6.11.0, PyQt6-sip;
   - GLib, Pango, cairo, librsvg, GDK-Pixbuf, FriBidi, libiconv, libintl and
     Graphite2;
   - the LGPL FFmpeg inside the OpenCV wheel.

   Each conda package's recipe and patches are in its `info/recipe` folder and
   in `https://github.com/conda-forge/<name>-feedstock`.

Applicable LGPL replacement/relinking rights must also be preserved. OSVEV's
build scripts are in the repository, and the single-file executable extracts
libraries as separate DLLs at runtime. Those facts alone do not establish that
all corresponding source is available or that rebuilding/replacement has been
verified for a particular release.

## Patent notice

FFmpeg and the bundled codec libraries implement formats that may be covered
by patents in some jurisdictions, notably H.264 (x264, OpenH264), H.265 (x265)
and some audio formats. Copyright licenses, including the GPL, do not grant
patent rights. OpenH264 here is built from source, so Cisco's patent-covered
binary license does not apply. Distributors and users are responsible for
determining whether a patent license is required for their use.

## Distribution status (not yet complete)

Already implemented:

- OSVEV's GPL license, copyright, source link and third-party notices.
- About tabs for those documents and exact build/runtime identification.
- Build-time collection of dependency license texts, as described above.

Before distributing a new executable:

- Make the matching OSVEV source revision and build scripts available, including
  new modules; do not identify a modified development tree as an exact clean commit.
- Verify the actual bundled component/license inventory and matching source access.
  Publish version/build-specific source directions alongside the binary, including
  required patches/recipes. This correspondence and availability have not yet been
  established for a new release; this document is not a compliance certification.
- Include `LICENSE`, this file and the collected `third_party_licenses` in the
  release package. The external release archive layout has not yet been produced.
- Consider the separate patent questions above; no patent review has been done.

Release validation, separate from license notices:

- Test the packaged executable on clean Windows without a development environment.
- Identify the release and its SHA-256. The executable is currently unsigned;
  disclose that fact and possible Windows SmartScreen warnings.
