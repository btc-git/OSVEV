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
These come from conda's `info/licenses` folders and the wheels' `*.dist-info`.
They, `LICENSE` and this file are embedded in the executable; see
**Release distribution** below.

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
| LAME, mpg123 | 4.0, 1.33.7 | LGPL-2.0-only, LGPL-2.1-only | MP3 libraries. `avcodec-63.dll` imports `libmp3lame.DLL`; `mpg123.dll` is also bundled. OSVEV exports are video-only |
| OpenSSL | 3.6.5 | Apache-2.0 | Linked into FFmpeg; compatible with GPLv3 |

### User interface

| Component | Version | License | Role |
|---|---|---|---|
| Qt (Qt6Core, Qt6Gui, Qt6Widgets, Qt6Network, Qt6Svg and their platform/style/image-format/icon plugins) | 6.11.2 (`qt6-main`) | LGPL-3.0-only | GUI framework. <https://www.qt.io/licensing/open-source-lgpl-obligations> |
| Qt Multimedia (`Qt6Multimedia.dll`, FFmpeg and Windows media plugins) | 6.11.2 (`qt6-multimedia`) | GPL-3.0-only | Audio playback support. The exact conda package declares GPLv3 and supplies its license text |
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
| OpenCV (`opencv-python` wheel) | 4.13.0.90 | Apache-2.0 | Display resizing and the legacy decoder. The wheel includes its own **LGPL-2.1 FFmpeg 4.4.6** (`opencv_videoio_ffmpeg`), used only by the explicitly selected legacy OpenCV decoder, and the notices in its `LICENSE-3RD-PARTY.txt`. Its exact source/build mapping is given below |
| PyInstaller bootloader | 6.22.2 | GPL-2.0-or-later with bootloader exception | Single-file launcher |

### Graphics, text and support libraries

| Component | Version | License | Role |
|---|---|---|---|
| GLib, Pango, librsvg, GDK-Pixbuf, FriBidi, libintl | 2.90.1, 1.58.2, 2.62.4, 2.44.8, 1.0.17, 0.22.5 | LGPL-2.1-or-later | Linked into FFmpeg (`librsvg`, text rendering) |
| libiconv | 1.18 | LGPL-2.1-only | Character conversion |
| cairo | 1.18.6 | LGPL-2.1-only OR MPL-1.1 | 2D graphics for librsvg/Pango |
| Graphite2 | 1.3.15 | LGPL-2.0-or-later (also MPL/GPL options) | Font shaping |
| FreeType | 2.14.3 | FTL OR GPL-2.0-only; used under the FTL (see Acknowledgements) | Fonts |
| HarfBuzz, Fontconfig, libxml2, libexpat, libffi, Brotli, libdeflate, pixman, lcms2 | conda-forge (see table of contents) | MIT | Support libraries |
| zlib, zlib-ng, libpng, bzip2, xz, zstd, libjpeg-turbo, libtiff, OpenJPEG, Lerc | conda-forge | Zlib, libpng, bzip2, 0BSD/public domain, BSD-3-Clause, IJG AND BSD-3-Clause AND Zlib, HPND, BSD-2-Clause, Apache-2.0 | Compression and image formats |
| Microsoft Visual C++ 2015-2022 runtime, Universal CRT | 14.51, 10.0.26100 | Microsoft redistributable licenses | Windows runtime DLLs; redistribution as permitted by Microsoft's terms |

### Acknowledgements

Some permissive licenses require a statement in the documentation that
accompanies a binary:

- This software is based in part on the work of the FreeType Team
  (<https://www.freetype.org>). FreeType is dual-licensed; OSVEV uses it under the
  [FreeType License](https://freetype.org/license.html), not its GPL-2.0-only
  alternative, which cannot be combined into a GPLv3 distribution.
- This software is based in part on the work of the Independent JPEG Group
  (libjpeg-turbo, `jpeg8.dll`).

The inspected 0.1.0 build collected no license texts for the FreeType
(`libfreetype`, `libfreetype6`) or SQLite (`libsqlite`) conda packages, so the
FreeType License is linked above. SQLite is in the public domain.

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

### OSVEV source

Use [OSVEV's repository](https://github.com/btc-git/osvev) at the exact commit
shown in **About > Build details** and in each processing log. The release tag
must identify that source revision. It includes `environment-eq.yml`,
`osvev.spec` and `build.ps1`, the scripts used to build the executable.

### Exact conda package sources, recipes and patches

The following Windows x64 package identities come from the supplied OSVEV 0.1.0
build inventory. Each was matched to its public conda-forge distribution record
and the identity inside that package's metadata. The metadata contains the
recipes, build scripts and referenced patch files; an unpatched upstream archive
alone does not describe these builds.

To obtain the source/build material for a row:

1. Obtain the linked upstream source. Its checksum is recorded in the package
   recipe. Retain any additional source inputs and locked dependencies required
   by that recipe, such as the Cargo dependencies of librsvg.
2. Obtain the linked **package/recipes** file. This is the exact conda binary
   package, which also carries the build material; it is **not** a substitute for
   the upstream source. A `.conda` file is a ZIP container. Open its
   `info-*.tar.zst` member with a Zstandard/tar-capable archive tool and retain
   `info/recipe/` plus the available variant/hash-input metadata under `info/`.
   This exposes `meta.yaml` or `recipe.yaml`, build scripts and patches without
   installing the package or running its code.
3. Follow the recipe's Windows x64 selectors and build configuration. Some
   patches and source inputs in shared recipes apply only to other platforms or
   outputs. Do not substitute a feedstock's current default branch for the
   package's own recipe.

| Package | Version / build | Upstream source | Exact build material |
|---|---|---|---|
| FFmpeg | `9.0.2 / gpl_h928aae7_903` | [ffmpeg-9.0.2.tar.gz](https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.gz) | [package/recipes][pkg-ffmpeg] |
| x264 | `1!164.3095 / pl5321h71a6e3e_3` | [revision baee400fa9ced6f5481a728138fed6e867b0ff7f](https://code.videolan.org/videolan/x264/-/archive/baee400fa9ced6f5481a728138fed6e867b0ff7f/x264-baee400fa9ced6f5481a728138fed6e867b0ff7f.tar.gz) | [package/recipes][pkg-x264] |
| x265 | `3.5 / h3ee6617_4` | [x265_3.5.tar.gz](https://bitbucket.org/multicoreware/x265_git/downloads/x265_3.5.tar.gz) | [package/recipes][pkg-x265] |
| LAME | `4.0 / h0c5f640_1` | [lame-4.0.tar.gz](https://downloads.sourceforge.net/sourceforge/lame/lame-4.0.tar.gz) | [package/recipes][pkg-lame] |
| mpg123 | `1.33.7 / ha58212f_1` | [mpg123-1.33.7.tar.bz2](https://downloads.sourceforge.net/project/mpg123/mpg123/1.33.7/mpg123-1.33.7.tar.bz2) | [package/recipes][pkg-mpg123] |
| Qt main modules | `6.11.2 / pl5321hfcac499_0` | [qt-everywhere-src-6.11.2.tar.xz](https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz) | [package/recipes][pkg-qt-main] |
| Qt Multimedia | `6.11.2 / pl5321h30c8fa7_0` | [qtmultimedia-everywhere-src-6.11.2.tar.xz](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/qtmultimedia-everywhere-src-6.11.2.tar.xz) | [package/recipes][pkg-qt-multimedia] |
| PyQt6 | `6.11.0 / py311h3e6a449_7` | [pyqt6-6.11.0.tar.gz](https://pypi.org/packages/source/P/PyQt6/pyqt6-6.11.0.tar.gz) | [package/recipes][pkg-pyqt6] |
| PyQt6-sip | `13.13.0 / py311h3e6a449_7` | [pyqt6_sip-13.13.0.tar.gz](https://pypi.org/packages/source/P/PyQt6-sip/pyqt6_sip-13.13.0.tar.gz) | [package/recipes][pkg-pyqt6-sip] |
| GLib | `2.90.1 / ha564072_0` | [glib-2.90.1.tar.xz](https://download.gnome.org/sources/glib/2.90/glib-2.90.1.tar.xz) | [package/recipes][pkg-glib] |
| Pango | `1.58.2 / h13911b6_1` | [pango-1.58.2.tar.xz](https://download.gnome.org/sources/pango/1.58/pango-1.58.2.tar.xz) | [package/recipes][pkg-pango] |
| cairo | `1.18.6 / h477c42c_0` | [cairo-1.18.6.tar.xz](https://cairographics.org/releases/cairo-1.18.6.tar.xz) | [package/recipes][pkg-cairo] |
| librsvg | `2.62.4 / h5506a08_0` | [librsvg-2.62.4.tar.xz](https://download.gnome.org/sources/librsvg/2.62/librsvg-2.62.4.tar.xz) | [package/recipes][pkg-librsvg] |
| GDK-Pixbuf | `2.44.8 / h1f5b9c4_2` | [gdk-pixbuf-2.44.8.tar.xz](https://download.gnome.org/sources/gdk-pixbuf/2.44/gdk-pixbuf-2.44.8.tar.xz) | [package/recipes][pkg-gdk-pixbuf] |
| FriBidi | `1.0.17 / hfd05255_0` | [fribidi-1.0.17.tar.xz](https://github.com/fribidi/fribidi/releases/download/v1.0.17/fribidi-1.0.17.tar.xz) | [package/recipes][pkg-fribidi] |
| libiconv | `1.18 / hc1393d2_3` | [libiconv-1.18.tar.gz](https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.18.tar.gz) | [package/recipes][pkg-libiconv] |
| libintl | `0.22.5 / h5728263_4` | [gettext-0.22.5.tar.xz](https://ftp.gnu.org/pub/gnu/gettext/gettext-0.22.5.tar.xz) | [package/recipes][pkg-libintl] |
| Graphite2 | `1.3.15 / h5112557_1` | [graphite2-1.3.15.tgz](https://github.com/silnrsi/graphite/releases/download/1.3.15/graphite2-1.3.15.tgz) | [package/recipes][pkg-graphite2] |

[pkg-ffmpeg]: https://conda.anaconda.org/conda-forge/win-64/ffmpeg-9.0.2-gpl_h928aae7_903.conda
[pkg-x264]: https://conda.anaconda.org/conda-forge/win-64/x264-1!164.3095-pl5321h71a6e3e_3.conda
[pkg-x265]: https://conda.anaconda.org/conda-forge/win-64/x265-3.5-h3ee6617_4.conda
[pkg-lame]: https://conda.anaconda.org/conda-forge/win-64/lame-4.0-h0c5f640_1.conda
[pkg-mpg123]: https://conda.anaconda.org/conda-forge/win-64/mpg123-1.33.7-ha58212f_1.conda
[pkg-qt-main]: https://conda.anaconda.org/conda-forge/win-64/qt6-main-6.11.2-pl5321hfcac499_0.conda
[pkg-qt-multimedia]: https://conda.anaconda.org/conda-forge/win-64/qt6-multimedia-6.11.2-pl5321h30c8fa7_0.conda
[pkg-pyqt6]: https://conda.anaconda.org/conda-forge/win-64/pyqt6-6.11.0-py311h3e6a449_7.conda
[pkg-pyqt6-sip]: https://conda.anaconda.org/conda-forge/win-64/pyqt6-sip-13.13.0-py311h3e6a449_7.conda
[pkg-glib]: https://conda.anaconda.org/conda-forge/win-64/libglib-2.90.1-ha564072_0.conda
[pkg-pango]: https://conda.anaconda.org/conda-forge/win-64/pango-1.58.2-h13911b6_1.conda
[pkg-cairo]: https://conda.anaconda.org/conda-forge/win-64/cairo-1.18.6-h477c42c_0.conda
[pkg-librsvg]: https://conda.anaconda.org/conda-forge/win-64/librsvg-2.62.4-h5506a08_0.conda
[pkg-gdk-pixbuf]: https://conda.anaconda.org/conda-forge/win-64/gdk-pixbuf-2.44.8-h1f5b9c4_2.conda
[pkg-fribidi]: https://conda.anaconda.org/conda-forge/win-64/fribidi-1.0.17-hfd05255_0.conda
[pkg-libiconv]: https://conda.anaconda.org/conda-forge/win-64/libiconv-1.18-hc1393d2_3.conda
[pkg-libintl]: https://conda.anaconda.org/conda-forge/win-64/libintl-0.22.5-h5728263_4.conda
[pkg-graphite2]: https://conda.anaconda.org/conda-forge/win-64/graphite2-1.3.15-h5112557_1.conda

The Qt main source link uses Qt's official archive rather than the mirror named
in its recipe. Qt's [published checksum](https://download.qt.io/archive/qt/6.11/6.11.2/single/md5sums.txt)
matches that recipe's declaration. The shared PyQt recipe also describes
WebEngine and Charts outputs; that does not mean those outputs are bundled here.

### OpenCV's separate FFmpeg DLL

The bundled `cv2\opencv_videoio_ffmpeg4130_64.dll` matches the upstream
`opencv_videoio_ffmpeg_64.dll` at
[opencv_3rdparty revision d82ad9a54a7b42a1648a9cae8fed5c2f20ea396c](https://github.com/opencv/opencv_3rdparty/tree/d82ad9a54a7b42a1648a9cae8fed5c2f20ea396c/ffmpeg).
Its SHA-256 is
`fcc614672159094a35815b7ea2819a648cbf86c30d90c57e8c6ba1432f594548`.

That revision supplies the source-fetch scripts, Windows cross-build scripts,
Docker build environment and wrapper source. Its
[source manifest](https://github.com/opencv/opencv_3rdparty/blob/d82ad9a54a7b42a1648a9cae8fed5c2f20ea396c/ffmpeg/download_src.sh)
identifies these inputs:

- [FFmpeg n4.4.6](https://github.com/FFmpeg/FFmpeg/tree/n4.4.6);
- [libvpx v1.15.2](https://chromium.googlesource.com/webm/libvpx/+/refs/tags/v1.15.2);
- [libaom v3.13.1](https://aomedia.googlesource.com/aom/+/refs/tags/v3.13.1);
- [OpenH264 v1.8.0](https://github.com/cisco/openh264/tree/v1.8.0) headers for the
  dynamic-loading wrapper; and
- [OpenCV revision cff7581175d2abfc6aef2e4f04f482e258b5c864](https://github.com/opencv/opencv/tree/cff7581175d2abfc6aef2e4f04f482e258b5c864),
  recorded by the build repository's submodule.

Use that fixed build repository revision, including its wrapper/build scripts,
not just a current FFmpeg download. This DLL is separate from the GPL FFmpeg
9.0.2 libraries used by OSVEV's verified processing path.

### Verification scope and continued access

These directions were checked against public package metadata and source-server
headers on 2026-11-02 UTC. Package recipe identities and referenced patch-file
presence were checked. The OpenCV DLL's Git blob identity was matched to the
upstream repository. Dependency source archives were not downloaded or rebuilt,
and their contents/checksums were not independently re-verified.

Publish this notice, or a direct link to this version of it, beside the release
binary. Retain the supplied license texts. Source availability remains the
distributor's responsibility; keep the directions working or provide appropriate
replacement hosting/material if an upstream location stops serving the required
source. Recheck this table whenever the packaged dependencies change.

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

## Release distribution

OSVEV is published as a single `OSVEV.exe` on the
[Releases](https://github.com/btc-git/osvev/releases) page, marked as a
pre-release while it is in beta. The release notes carry the executable's
SHA-256, a link to the source tag, and a reminder that the executable is
unsigned, so Windows SmartScreen may warn.

- **License and notices.** `LICENSE` and this file are embedded in the
  executable, and **About** shows them. They are also in the repository at the
  release tag.
- **Dependency license texts.** The collected `third_party_licenses` folder is
  embedded in the executable, not offered as a separate download. PyInstaller's
  `pyi-archive_viewer` can list and extract it. The inspected build supplied none
  for FreeType or SQLite; see Acknowledgements above.
- **Source.** The release tag identifies the exact OSVEV commit. The tables above
  give exact source and build material for the GPL/LGPL dependencies.

When publishing a new executable:

- Build from a clean, committed and public revision, and tag that revision. Do
  not identify a modified development tree as an exact clean commit (**About >
  Build details** shows `+dirty` for uncommitted changes).
- Confirm the build still uses the package set documented above and update the
  tables when it changes. Preserve the applicable source-access and
  replacement/relinking rights; this document is not a compliance
  certification.
- Keep the source directions above working, and link this file from the release
  notes.
- Consider the separate patent questions above; no patent review has been done.
- Test the packaged executable on clean Windows without a development
  environment before publishing.
