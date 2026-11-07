# Teaching baseline: fidelity and recovered features

## Partial time-range import follow-up

**Open Time Range...** indexes only a source-PTS interval `[start, end)` while keeping
exact original frame numbers: frame metadata comes from FFprobe `-read_intervals`
around the interval, earlier frames are counted from demuxed packets (not decoded),
and both lists must agree exactly or the open is refused. Normal **Open** and all
decoding, enhancement and export algorithms are unchanged; export commands for the
same original frames are byte-identical. Tests: `tests/test_range_import.py`
(full-scan slice equality incl. mid-GOP, VFR, file start/end; exact PTS seeking with
native-sample equality; plan/command equality; verified PNG/FFV1/H.264 exports; app
navigation and export dialog numbering). Details in [README.md](../../README.md).

## Command-driven export follow-up

Current functionality now includes a small shared FFmpeg export plan:
brightness/contrast/sharpening and exact top/bottom/left/right pixel cropping
from the original source to PNG, RGB FFV1
MKV, or explicitly lossy H.264 MP4. Current-frame, inclusive-range and
whole-video selections are supported. Commands are visible/copyable and
can run inside OSVEV or independently, followed by the same verification.

Every selected frame is compared in tight BGR24 with the live
`FrameProcessor` and independently filtered FFmpeg/output pixels. Audits
record source-file preservation, versions, settings, selection, commands,
native YUV hashes, processed/output hashes and video PTS. This certifies
selected processed RGB, not preservation of native YUV or original timing,
audio, metadata, monitor appearance or container bytes.

PyAV is now the default decoder; legacy OpenCV requires explicit selection
and never serves as automatic fallback. Native samples stay uncropped;
the inspector maps cropped coordinates back to source coordinates.
Video exports automatically use the main viewer's selected speed while retaining every
selected frame. Each output PTS and total duration is independently checked
against source timing divided by speed, within recorded encoding/container
quantization tolerances. PNGs ignore speed. Exports scale after cropping with
the selected Zoom Level and Interpolation Method, using FFmpeg; OpenCV/Qt
display interpolation can differ, and neither viewer is changed. Highlights,
the Magnified View, Additional Zoom and overlays never alter exported pixels.
Crop dimensions are never normalized for an encoder: odd-sized PNG/FFV1
is supported, and incompatible H.264 YUV420 output is explicitly rejected.

With the standard `eq` pipeline, all 201 contrast settings match
the ordinary `eq` path. Brightness/contrast and sharpening use
the same explicitly controlled libavfilter operations as the export command:
one full-range BT.709 YUV444 conversion, combined luma `eq` and 5x5 luma
`unsharp`, then BGR conversion. Brightness/contrast intentionally differ from
earlier pre-`eq` development versions; neutral behavior is unchanged. A single persistent
graph is rebuilt when controls or dimensions change. All-zero processing
still copies the verified baseline without any conversion.
JPEG remains an OpenCV teaching simulation, now applied after the core
enhancements; the export dialog explicitly excludes it even when active.
Direct API requests to reproduce JPEG processing are still rejected.
No previews, caches or mandatory working copies from the abandoned
architecture have been restored.

See [Export and reproducibility](../EXPORTING.md). Earlier feature inventories
below remain historical assessments, not the current export capability.

## Playback scheduling follow-up

OSVEV now schedules playback with monotonic,
source-time deadlines shared by OpenCV and PyAV, instead of a repeating
integer-millisecond timer. Complete increasing timestamps drive playback;
otherwise the timing diagnostic explicitly reports average-FPS fallback.
Overdue presentation slots are omitted to recover timing after stalls.
Stepping and scrubbing remain exact. No new media cache or rendering
architecture was introduced, and the validated native/BGR input
conversion and enhancement math are unchanged.

Small forward skips now decode sequentially. The earlier fast-playback
path sought on every frame jump, repeatedly decoding preceding GOPs:
local offscreen 5x PyAV playback painted only about 8-9 FPS, with intervals
up to 251 ms. After correction it paints near 30 FPS with no input seeks
in the tested runs. Ordinary 1x playback no longer accumulates the drift
from using 33 ms for a 33.367 ms interval, or 34 ms for 33.859 ms.

Hover Play/Pause for completed Qt-paint FPS, frame-interval statistics,
intentional skips, late slots, missed paint slots, and timing mode. This
does not certify monitor scanout, vsync, or audio synchronization. Late
slots and missed paint slots overlap, so they must not be summed.
The diagnostic uses at most 120 timing samples and updates once per second.

The sections below retain the earlier stabilization and input-adapter
assessments as history; their original average-FPS scheduling description
is superseded by this follow-up.

## Scope and repository state

This stabilization started from an earlier development snapshot while newer
work continued separately. No branch history, archived source, existing
media, diagnostic results, or executables were deleted or replaced.

Untracked diagnostic/media files present at the time were development
leftovers, not newer Python modules being loaded into the older
application. Ignored environments, builds, and archives also survive
checkout. An executable built from other source is not certified for this
source.

The current scope is a direct-open OpenCV teaching viewer. Crop, export,
FFmpeg command generation, mandatory transcodes, and rendered previews have
not been restored.

## Baseline fixes

- Zero JPEG compression simulation bypasses JPEG encode/decode entirely.
- One processed-frame cache is shared by the main view, magnifier, and pixel
  inspector. Neutral processed bytes equal decoded source BGR bytes.
- The inspector reports real processed RGB at Zoom Level (including the
  selected interpolation, clipping and rounding) and native decoded source
  samples, rather than theoretical or RGB-derived values.
- Main-view fitting resizes the processed frame directly to the display size at
  the screen's physical pixel density: area-averaged when shrinking; when
  enlarging, exact integer nearest-neighbor (the inspector's mapping) at Zoom
  Level 1x or with Nearest, otherwise the selected interpolation
  (display preview only; no Zoom Level
  intermediate). The magnifier retains the selected
  interpolation at all offered zoom levels, followed by optional
  nearest-neighbor enlargement.
- Magnifier rendering is bounded by the visible area, with neighboring
  samples retained for interpolation. Extreme combined zoom no longer
  leaves an old frame displayed.
- Green grid overlays do not alter inspected or processed pixels. Their
  labels explicitly describe grid positions, not color preservation.
- Lens bounds fit small images, and its normalized position survives resize.
  Nonempty selections include partially covered source pixels, even for 1x1 crops.
  Fast interpolation retains processed-frame neighbors outside the lens so its
  pixels agree with the inspector; the full zoomed frame is not rendered.
- Consecutive frames decode without seeking again. Random navigation still
  seeks; looping and manual stepping remain supported.
- Timeline dragging uses the emitted position; failed navigation retains
  the previously displayed frame/index.
- Pause and close stop the timer even if capture has failed.
- Loading stops playback, validates a candidate before replacing the old
  capture, and scans metadata on a cancellable worker. Errors are reported;
  unavailable metadata does not retain another video's old values.
- Source attributes are distinguished from display enlargement and target
  playback speed. Speed reciprocals are no longer rounded to 0.33/1.33.

## What "lossless" needs to mean

There are several independent comparisons:

| Comparison | What matching hashes prove | What they do not prove |
|---|---|---|
| Source-file SHA-256 | Identical file bytes | That its existing compression was lossless |
| Native decoded frame hashes | Identical samples in a specified pixel format/bit depth, with frames aligned | Identical containers, packets, metadata, or audio |
| Decoded BGR frame SHA-256 | Identical displayed-pipeline input samples for a specified decoder/conversion | Preservation of native YUV, high bit depth, HDR, or correct color interpretation |
| Processed-frame hashes | Identical effects output bytes with the same format and frame correspondence | That the effects themselves are nondestructive |
| Export-file SHA-256 | Identical exported file bytes | A necessary condition for identical decoded images; mux metadata can differ |

A lossy H.264 source cannot be restored to pre-encoding camera samples.
It can still be preserved unchanged as a file, decoded deterministically
under a specified environment, and used as the reference for effects.

Any frame-hash comparison must also identify dimensions, sample ordering,
pixel format, bit depth, frame order, decoder/version, and color-conversion
rules. Timing, color tags, and audio require additional comparisons.

### Verified controlled round trips

The regression suite sends five deterministic 48x32 BGR frames through
in-memory FFmpeg encodes and decodes:

| Path | Result |
|---|---|
| BGR -> FFV1/bgr0 -> BGR | Exact bytes preserved |
| BGR -> libx264rgb/CRF 0 -> BGR | Exact bytes preserved |
| BGR -> libx264/CRF 0/yuv420p -> BGR | Bytes differ |
| Native yuv420p -> libx264/CRF 0/yuv420p -> native yuv420p | Exact bytes preserved |

These establish representation-specific guarantees in the tested FFmpeg
build, not universal guarantees for every input or codec implementation.
The tests use NUT as an in-memory carrier, not a proposed UI export format.
RGB H.264 and lossless H.264 also have more limited player compatibility than
ordinary lossy H.264. "CRF 0" does not make RGB-to-YUV420 conversion lossless.

PNG sequences and correctly configured FFV1 are promising exact outputs
for the viewer's BGR/RGB baseline. They still cannot recover native samples
discarded during input conversion, and PNG requires separate timing/audio.

## Decoder/color investigation

Environment tested: Python 3.11.3, OpenCV 4.13.0, NumPy 2.4.1, standalone
FFmpeg 8.0.1. OpenCV reports the FFMPEG backend, but its bundled libavcodec
is 58.134.100, not the standalone executable's library.

For the first eight frames:

| Source | Source format / matrix tag | Neutral processing vs OpenCV decode | Standalone FFmpeg default BGR vs OpenCV BGR |
|---|---|---|---|
| `short-clip.mp4` | H.264, yuv420p, smpte170m | Exact | Exact |
| `gas-station.mp4` | H.264, yuv420p, bt709 | Exact | Different |

For the gas-station sample, the eight-frame comparison found 4,800,373
differing channel samples, with maximum absolute channel difference 25
and mean difference about 0.312. For its first frame, explicitly using
FFmpeg `scale=in_color_matrix=bt601` reproduced OpenCV BGR exactly;
BT.709/default conversion did not.

This is evidence that color-matrix handling explains that observed mismatch.
It is not a recommendation to force every source to BT.601, nor proof that
every decoder disagreement has that cause. The source is tagged BT.709.
Matching an incorrect conversion would not establish faithful source color.

The correct next investigation is an authoritative decode/conversion
contract, not adjusting enhancement filters to hide discrepancies.
Container restriction alone cannot solve this: both samples are H.264 MP4.

Re-run the read-only diagnostic:

```powershell
python verify_fidelity.py path\to\recording.mp4 --frames 8
```

The two development samples measured here are not distributed with the
source.

It prints source-file and individual frame SHA-256 values and explicitly
reports decoder equality or mismatch. It makes no whole-video or native-YUV
claim. Source files are never transcoded or overwritten.

## Historical OpenCV effects and current shared contract

Before the shared-filter follow-up, the viewer's fixed order was:

1. OpenCV YCrCb brightness (integer Y offset).
2. Optional JPEG recompression simulation.
3. Per-BGR-channel contrast lookup table.
4. OpenCV Gaussian-blur unsharp masking.

Magnification/interpolation and grid overlays occur afterward, only for
inspection. The additional zoom always uses nearest-neighbor.

An FFmpeg `eq`/`lutyuv`/`unsharp` chain is not automatically the same
algorithm: parameter mappings, channel choices, pixel format, color matrix,
rounding, clipping, kernel, and operation order can differ. Crop is much
easier to explain consistently because it selects an integer rectangle.

The current shared contract retains the validated per-BGR-channel contrast
LUT but replaces brightness/sharpening with explicit libavfilter definitions.
Its order is brightness, contrast, sharpening, then optional JPEG teaching
simulation. The source conversion, native sample retention, magnification
and inspector are unchanged. See [Export and reproducibility](../EXPORTING.md)
for precise matrix/range, kernel and rounding definitions.

It is reasonable to publish a clearly labeled *analogous* FFmpeg command
without claiming hash equality. Exact mode would require shared processing
definitions and a shared decode/color contract, plus output tests. Keep
that separate from the lightweight live teaching view.

## Full inventory of later development

This inventory comes from an earlier development line's source and history,
not from a new certification run of that work.

| Area | Features added or explored | Suggested disposition |
|---|---|---|
| Import | Many additional container extensions; working-folder selection; mandatory H.264 CRF-0 MP4 working copy; unique working-file names; dedicated working-files folder | Preserve ideas, but retain direct open as the baseline. Mandatory conversion introduces startup, storage, and representation changes. |
| Trim selection | Dual-handle range slider, start/end thumbnails, typed start/end times, clip duration | Useful later as a small, optional clip-selection feature. Frame-accurate trim/timing needs tests. |
| Import progress/lifecycle | Worker transcode, cancel/progress reporting, transient working-file cleanup, confidence estimates | Worker/cancel approach is useful. Confidence estimates are not hash proof. No existing files should be cleaned automatically in this recovery pass. |
| Main-view modes | Actual Size and Fit to View; zoom/interpolation applied as whole-frame output scale | Keep display fitting separate from processing/output scale. Restore actual-size view only if useful. |
| Adjustments/UI | Per-control reset buttons, numeric values, rearranged layout, closed-panel-aware info updates | Small usability improvements can be restored independently. An earlier info-update checkbox was later removed. |
| Crop | Left/right/top/bottom pixel controls and reset buttons; effective crop calculation; shared preview/export rectangle; even dimensions for H.264/yuv420p | High-value next processing feature. Preserve requested vs effective crop, and do not silently discard pixels for codec compatibility. |
| Processing order | Draggable processing stack for brightness/contrast, sharpness, crop, scale; saved order; reordered filter construction and tests | Useful teaching concept, but defer until fixed-order operations are documented and reproducible. Order changes clipping/boundaries and thus results. |
| Artifact teaching presets | None, Noise, H.264-like macroblocks, Resolution Loss, Heavy JPEG-like recompression; intensity; legacy-name aliases | Reintroduce selectively as explicitly simulated artifacts, not real codec measurements. Other artifact helpers/prototypes existed but were not all exposed by the final UI. |
| Overlay/caching | Processed-frame LRU cache, cached masks, cache-size calculation, shared processed-frame retrieval, changes to main/zoom overlay placement | Shared retrieval is adopted in a single-entry cache. Do not restore large caches before measuring actual output memory. |
| Magnifier usability | Click outside lens to reposition; normalized location retained during redraw/view changes | Small independent candidate; normalized preservation is adopted. Click-to-reposition is not yet restored. |
| Settings | Save/load INI with source/working paths, clip range, adjustments, crop, zoom/interpolation, speed, order, highlights, and import command | Start with just current adjustment/view settings; avoid restoring unrelated working-copy state. |
| Export UI | Destination and filename; H.264 CRF 0, H.264 CRF 12, FFV1 MKV, PNG sequence; dimensions, progress, command/log preview | Start with one explicit pixel-preserving path and one clearly labeled ordinary FFmpeg path. The final UI exposes FFV1, while an earlier checkpoint deferred its validation. |
| Export paths | Pure FFmpeg ordinary processing; OpenCV frame piping for custom effects; timestamped PyAV visual encoding; PNG manifests | Multiple paths were a major source of complexity. Do not restore all simultaneously. |
| Logs/provenance | Source/working/output identity; SHA-256; settings; requested/effective crop; processing order; codec parameters; FFmpeg version; exact argv serialization; import/export commands; PNG manifest hashes | Valuable. First add a small log with explicitly scoped file/frame hashes and truthful reproducibility labels. |
| Speed/audio export | Video PTS retiming; chained audio tempo filters; stream-copy audio at 1x; AAC for changed tempo; fixes removing padding/shortest behavior that caused hangs/final-frame loss | Separate future export work. Validate duration, frame count, final frame, and audio independently. |
| Smooth Preview | Render a temporary processed video through export; playback window; frame steps, speed, status; multiple windows with different rendered settings | Useful eventual A/B teaching feature, but expensive. Snapshot each preview's settings and keep rendering optional. |
| Preview synchronization | Play All; Sync Previews; parent-controlled lockstep; shared seeking; frame mapping across different totals; window lifecycle and temporary session directories | Defer until independent previews are correct. Matching frame percentages is not the same as matching source presentation times. |
| Timing | Native PTS/time-base data; asynchronous compact FFprobe index; timestamp-to-frame lookup; CFR/VFR tests; media-time/wall-clock scheduling; fallback timing | Important for certified playback/export, not required to preserve neutral decoded frame bytes in this baseline. |
| Decoder/filter overhaul | Native PyAV frames; libavfilter sessions; filter-graph reuse; canonical luma brightness/contrast/unsharp definitions; shared ordinary filter strings for interactive/export paths | Potential exact-mode foundation. It deliberately changes the older OpenCV algorithms, so adopt only after comparing and documenting the change. |
| Playback/status | Actual/target playback information; audio-stream detection and muted status | Good small teaching/status additions; audio detection is not audio playback. |
| Validation/prototypes | Hash-based independent export reproduction, H.264/FFV1 probes, timestamp/lifecycle tests, memory/lookup benchmarks, decoder/filter prototypes, artifact test images | Preserve as reference. Many are bespoke scripts, not a maintained regression suite or universal proof. |

### Concrete performance risk in the newer architecture

The newer main view passes its zoom dropdown as full-frame `output_scale`.
A 10x scale produces 100x the pixels; 30x produces 900x. The processed-frame
cache limit is computed from input dimensions, so enlarged outputs can
exceed its intended memory budget. This is separate from the mere number
of features or source lines.

The current baseline magnifies only a visible region and caches one
unscaled processed frame. It does not scale/export whole videos on zoom.

## Recommended incremental sequence

1. Keep the stabilized teaching viewer and its regression tests.
2. Define source/native-vs-BGR fidelity explicitly; investigate correct
   matrix/range conversion with per-frame hashes before claiming exact mode.
3. Add crop with one integer-coordinate definition and no hidden scaling.
4. Add simple command explanations and a log, marking analogous commands
   as analogous. Preserve the OpenCV teaching algorithms unless an explicit
   exact mode is selected.
5. Add optional settings snapshots and a verified lossless image-output path.
6. Add processing-order demonstrations and independent A/B previews only
   after the fixed-order pipeline is trustworthy.
7. Treat synchronized preview, broad codec support, VFR scheduling, and
   audio retiming as separate projects with their own validation.

Validation here includes offscreen GUI regression tests, two real sample
loads and playback runs, and controlled codec/hash checks. It does not
replace hands-on visual testing or certify the old executable.

At the end of stabilization, all 28 tests passed. Local two-second offscreen
playback runs measured approximately 29.4 FPS for the 29.53-FPS short clip
and 30.1 FPS for the 29.97-FPS gas-station clip, without warnings. The earlier
short-clip baseline run measured about 21 FPS. These are local observations,
not performance guarantees for other machines, resolutions, or effects.
# Historical opt-in PyAV input follow-up

This section records the earlier adapter decision. PyAV is now the default,
as described above; the benchmark findings remain.

The historical assessment below describes the stabilized OpenCV baseline,
before input-only PyAV development. Nothing from the
old rendering, full-video caching, synchronized preview, or export architecture
has been restored.

PyAV is now the default (`--decoder opencv` explicitly selects legacy input).
The verified input adapter supports H.264 and progressive 8-bit SDR HEVC with
the same strict YUV420 color/timing requirements. It also reopens OSVEV's
BGR0 FFV1/MKV exports through full-video Open, retaining exact RGB samples and
format-appropriate native inspection/hash labels, without a YUV conversion.
Both dialogs share MP4/MOV/AVI/MKV/M4V filters; full opening may establish an
undeclared count from the validated full frame index. Partial opening still
requires declared totals and exact packet/frame correspondence, and excludes FFV1.
Rotation/display matrices are rejected. See the README for the current contract.

The original input-only development version described here selected a native H.264 decoder
and explicit bicubic BT.601/BT.709, limited/full-range to full-range BGR
converter. OpenCV was then the default and independently selectable. PyAV
errors do not fall back to OpenCV. Native Y/U/V are retained separately
for the current frame and exposed in the inspector's Native Source YCbCr row
(originally only in a value-cell tooltip); U/V are the stored shared 4:2:0 samples, not RGB-derived estimates.

The opt-in path reproduces both previously established ordered native
and BGR SHA-256 manifests: 2,493 short-clip frames plus 2,900 gas-station
frames. Presentation order, integer PTS, and time base participate in those
manifests. All four known matrix/range controls reproduce their original
hashes and independently decoded FFmpeg bytes. Source-file hashes are
unchanged and neutral processing retains the converted BGR bytes.

Selection does not change enhancement math or the display pipeline.
Corrected BT.709 colors intentionally differ from legacy OpenCV's BT.601
interpretation. Whole-source native equality and BGR equality are distinct
guarantees; neither proves that the source's metadata describes its intended
colorimetry, nor that a monitor displays those numbers correctly.

Missing/contradictory metadata, non-H.264 codecs, unsupported color tags,
bit depths, formats, chroma siting, interlace, dynamic geometry/color,
incomplete timing/index metadata, or unknown container frame count are
reported explicitly. See the README for the exact supported contract and
tested runtime. The packaged executable was not rebuilt.

Responsiveness remains close to the recovered viewer for playback and
adjustment controls. Exact keyframe-based seeks are slower in some cases:
the gas-station final-frame seek measured about 210 ms versus 50 ms for
OpenCV. Development stopped at this opt-in adapter rather than adding a
seek cache or redesign. This is a reason to review interactive behavior
before deciding on a new default.

The separate diagnostic contrast experiment reproduced the unchanged
OpenCV contrast LUT with FFmpeg `lutrgb` after the explicit BGR baseline
conversion: both sources, 16 frames each, contrast +20, -20, +100, -100,
zero channel differences. This does not certify every contrast setting,
brightness, sharpening, JPEG, or video export. No filter engine was added
to the application.
