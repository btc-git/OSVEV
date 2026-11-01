# Command-driven export and reproducibility

## GPL FFmpeg `eq` pipeline

OSVEV uses standard FFmpeg `eq` and `unsharp` for its enhancements. These
replaced custom algorithms used by earlier OSVEV development versions. Use the
`osvev` conda environment described in the README, not the ordinary PyPI
PyAV wheel: that wheel lacks `eq`. PyAV and standalone FFmpeg must both use the
compatible GPL FFmpeg 9 build. The packaged executable bundles both.

## What is supported

Start the verified input path:

```powershell
python main.py
```

Open a supported source, adjust brightness/contrast/sharpening/crop, then click **Export...**.
PyAV is now the default; `--decoder pyav` is still accepted. OpenCV requires
explicit `--decoder opencv` selection, is labelled legacy, and cannot be
used for verified exports. No automatic fallback occurs. The existing decoder
contract applies: progressive 8-bit H.264/HEVC 4:2:0, supported explicit
matrix/range/SDR metadata, left chroma siting, and complete source PTS metadata;
or full-range packed RGB-family FFV1 as described below. Unsupported source
rotation/display matrices are rejected. No silent conversion fallbacks are used.
Decoder-flagged corrupt frames are rejected, including during independent
verification of externally created output. Generated source/export/reference
commands use strict decoding (`-xerror -err_detect explode`), as working-copy
conversion already does. Metadata/timing permissions do not authorize corrupt
pixels, and re-encoding cannot establish the missing original pixels.

| Existing enhancement | Command-based export |
|---|---|
| Neutral, all settings zero | Exact BGR baseline |
| Contrast, every integer setting -100 through +100 | Standard luma `eq`, contrast factor `1 + slider/100`; independently tested at all 256 gray levels |
| Brightness, every integer setting -100 through +100 | Standard luma `eq`, brightness `slider/100`, in explicit full-range BT.709 YUV444 |
| Sharpening, every integer setting 0 through 100 | Explicit full-range BT.709 YUV444, 5x5 luma `unsharp`, amount 0.00 through 1.00, chroma amount zero |
| JPEG compression simulation nonzero | Excluded from dialog exports; neither viewer changes. Direct API requests for JPEG processing remain rejected as non-reproducible |
| Brightness/contrast/sharpening combinations | Same spatial filter graph and fixed order in live processing and commands |
| Top/bottom/left/right crop | Exact integer rectangle after enhancements, before export scaling |
| Zoom Level and Interpolation Method | Export-only scaling after crop; FFmpeg neighbor/bilinear/bicubic/lanczos. Viewer behavior is unchanged; kernels/rounding may differ |

Brightness and contrast intentionally differ from those earlier versions:
`eq` combines its contrast and brightness expression on luma rather than
using separate integer-offset and per-channel RGB LUT operations. No old
LUT correction is needed. The default gamma and saturation are unchanged.

### Shared enhancement definition and intentional behavior changes

`processing_filters.py` defines filter nodes once. `FrameProcessor` feeds
the existing corrected BGR baseline through those nodes using an in-process,
single-threaded PyAV/libavfilter graph. The command generator serializes
the same nodes after decoding/converting the original source. There is no
FFmpeg process per live frame, rendered preview, or new frame cache.
Each viewer/export verifier owns one graph, reused until dimensions or
settings change. Spatial filters return one immediate frame per push;
synthetic graph PTS do not replace source timing or affect seeking.
Audits identify this contract as `osvev-eq-unsharp-1`. Logs from earlier
development builds show `osvev-eq-unsharp-experiment-1`, the same
filter contract under its former label.

This reuses the earlier successful persistent-libavfilter approach, not
its rendering/caching machinery. Keeping
the current BGR boundary preserves the neutral baseline and inspection.
FFmpeg `unsharp` negotiates a YUV format even when given GBR;
the graph therefore specifies the conversion rather than trusting implicit
matrix/range negotiation.

The fixed order is **combined brightness/contrast (`eq`), sharpening, crop**:

1. Any nonzero enhancement converts BGR24 to **full-range BT.709 YUV444** using
   explicit bicubic `scale` coefficients/range and `format=yuv444p`.
2. Nonzero brightness/contrast applies `eq=brightness=B/100:contrast=1+C/100`.
   Brightness spans -1 through +1; contrast spans 0 through 2.
3. Nonzero sharpening applies `unsharp=5:5:amount:5:5:0` to luma only.
   The graph then converts once back to BGR24, with no intermediate BGR conversion.
   The slider divided by 100 maps to the amount, formatted with two decimals.
4. Crop removes the requested pixel counts from top/bottom/left/right.
   The shared `crop_utils.py` validates a nonempty rectangle, the live
   processor slices the completed BGR image, and FFmpeg uses the identical
   `crop=w=...:h=...:x=...:y=...:exact=1` rectangle after enhancement conversion.
   Changing crop does not rebuild the enhancement graph.
5. Export-only scaling uses the selected Zoom Level and Interpolation Method.
   Output dimensions are exactly cropped width/height multiplied by zoom.
   The command and verifier use the same explicit full-range BGR FFmpeg scale
   definition. 1x bypasses scaling and preserves the prior pixel baseline.
   The Main View separately fits the processed frame at physical display density:
   area averaging when shrinking, integer nearest mapping when enlarging at 1x
   or with Nearest, otherwise the selected OpenCV interpolation. The magnifier
   uses bounded OpenCV resizing, or optional paused FFmpeg matching of its own
   complete selected region. Matching method names alone do not promise identical
   kernels, rounding or pixels, especially with different crop boundaries.

No crop value is rounded, padded, normalized to even dimensions or changed
to satisfy an encoder. PNG/FFV1 preserve odd output dimensions and 1x1 crops.
H.264 YUV420 requires even final scaled width and height and blocks incompatible
outputs with an explicit message. Users must deliberately change crop, zoom or format.
Exports above 8192 pixels on either axis or 33,177,600 pixels total require
confirmation about memory, disk and processing cost; no arbitrary zoom cap
is imposed. Encoders may reject dimensions beyond their own limits.
The four controls reset to zero with Reset or after successful source loading;
failed/cancelled loads preserve the previous crop. Invalid crops are reported.
Pixel inspection uses cropped-image coordinates for the displayed pixel,
then adds left/top offsets for baseline/native source values. Native
decoded Y/U/V planes remain full-sized and unchanged.

BT.709 here is the **working enhancement matrix**, not a reinterpretation
of native source samples. A BT.601 source still first uses its validated
BT.601 input conversion. Working YUV444 avoids additional chroma subsampling.
Conversions and filters include their specified rounding/clipping; very
small nonzero adjustments can introduce conversion-round-trip differences.
Chroma-plane preservation does not promise unchanged RGB hue after clipping.
No gamma, transfer-function conversion, HDR processing or linear-light claim
is made. All-zero settings bypass libavfilter entirely and copy the exact
baseline. Every nonzero combination uses just one YUV round trip, including
contrast-only; this can change pixels even at very small nonzero settings.

Brightness and contrast intentionally replace the earlier algorithms;
sharpening retains FFmpeg's 5x5 luma kernel, now without an intermediate
BGR round trip when combined with other controls.
These are useful, documented teaching operations, not claims of equivalence
to the old pixels. Slider ranges, decoder, magnifier and inspector
are preserved. GPL-enabled PyAV 18.1.0 is required for nonzero core enhancements with
either decoder; missing/filter failures are reported, never replaced by
legacy processing.

JPEG remains OpenCV's encode/decode simulation at quality `100-compression`.
It runs **after enhancements and cropping**, rather than between
brightness and contrast. Zero still bypasses JPEG entirely. FFmpeg MJPEG's
quality scale/encoder is not equivalent; JPEG is labelled teaching-only
and is explicitly excluded from dialog exports rather than complicating the
shared core. The dialog sets export compression to zero without changing the
viewer slider. Thus an active JPEG preview does not match the exported pixels,
as stated in the dialog and log. Direct API JPEG processing requests still
fail rather than silently pretending JPEG is reproducible.

Tests independently compare all 201 brightness values, all 201 contrast
values, all 101 sharpening values, extreme/small-adjustment combinations,
colored edges, clipping, tiny/odd dimensions and repeated/seek-order frames.
Source-to-export tests cover both supported matrices and ranges. Equality
is validated for the installed library builds, not promised across every
future FFmpeg version; every actual export still compares every selected
frame and fails if its independent command differs.

### Validation and dependency tradeoff when adopting `eq`

With maintained conda-forge PyAV 18.1.0, GPL FFmpeg 9.0.2 and matching
PyQt6 6.11.0/Qt 6.11.2, the full suite passes 110 tests. The unmodified input
suite still matches both complete source manifests (5,393 frames) and the
BT.601/BT.709 full/limited controls. Enhancement/export tests cover all
slider values, combinations/extremes, the four matrix/range cases,
PNG/FFV1 equality, lossy H.264, crop, scaling methods, speeds and navigation.
This does not mean every enhanced frame of both complete videos has been
re-certified under the new algorithm.

Both algorithms were measured inside the same isolated FFmpeg 9 environment,
using brightness +20, contrast +32, sharpness +30, persistent graphs and
60 measured iterations per source after warmup:

| Source | Earlier algorithm median / p95 | Standard `eq` median / p95 |
|---|---|---|
| 1274x720 short clip | 14.72 / 15.46 ms | 9.33 / 9.76 ms |
| 1280x698 gas station | 13.94 / 14.23 ms | 8.53 / 8.93 ms |

The combined enhancement string shrank from 698 to 289 characters.
Four-second enhanced Qt-paint tests with 3x magnification, both decoders and
both samples sustained rolling 29.54/29.98 FPS at 1x with no late or missed
slots. At 5x there were no late/missed slots; intentional source skipping
remains unchanged. Forced 120 ms GUI stalls skipped three overdue slots and
recovered, as before. The earlier algorithm's playback also sustained source rate in both
the original Python environment and the new one. These are offscreen paint
measurements, not physical-display/vsync or subjective smoothness guarantees.

Real-source demonstrations exported frames 21-25 of each sample using
brightness +20, contrast +32, sharpness +30, crop T2/B4/L6/R8, 2x Bicubic
scaling and 2x video speed. PNG sequences and FFV1 matched live/verifier and
independent FFmpeg pixels exactly; FFV1 timing/count/duration verification
passed. Source hashes were unchanged and normal text logs were produced.
Side-by-side images show the intended appearance change: the new combined
settings brighten shadows more and clip more highlights at the same slider
numbers; the new numbers are not equivalent to the old effect strength.

The PyInstaller spec builds a one-file executable (~129 MB) without
custom hooks or manual DLL copies. Since OSVEV 0.1.0 it also bundles the matching
GPL FFmpeg 9 `ffmpeg.exe`/`ffprobe.exe` and uses them regardless of PATH.
The packaged executable was tested with a hostile PATH, on the development
workstation only. It produced verified PNG/FFV1 exports, an H.264 export,
and a time-range export. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licensing and remaining
distribution obligations. The runtime is maintainable using standard conda
packages, but less simple to install than the earlier PyPI-only
dependencies.

### Historical pre-`eq` shared-enhancement benchmark

On the installed Windows/Python 3.11/PyAV 18.1.0/FFmpeg 8.0.1 builds,
combined brightness +20, contrast +32, sharpening +30 processing measured:

| Source | Previous OpenCV median | Shared persistent median / p95 | Rebuild plus first frame median |
|---|---|---|---|
| 1274x720 short clip | 10.81 ms | 15.96 / 17.92 ms | 40.96 ms |
| 1280x698 gas station | 9.19 ms | 15.28 / 16.05 ms | 39.66 ms |

This is additional processing cost, not an acceleration. All-zero copying
remained about 0.4 ms. Brightness alone measured about 6.7 ms, contrast
2.0-2.7 ms, and sharpening 9.0-10.0 ms. A slider change rebuilds the graph;
normal playback and repeated inspection reuse it. No background graph
builder, extra rendering engine or asynchronous frame queue was added.

Five-second completed-Qt-paint runs with brightness +20, contrast +20,
sharpening +30 and 3x magnification sustained source-rate playback:
29.42-29.43 FPS for the short clip and 29.87 FPS for gas, including startup;
rolling rates were about 29.5 and 29.97 FPS. Both OpenCV and PyAV had zero
late/missed slots and zero omitted source frames. These are offscreen Qt
measurements, not physical monitor/vsync guarantees. GUI-thread work and
high-resolution inputs can still exceed a frame deadline.

Enhanced 5x stress runs stayed near 29-30 painted FPS without input seeks.
One short-clip PyAV run recorded one missed paint slot; the other 5x runs
had none. A deliberately forced 120 ms GUI stall remained visible and
omitted three overdue slots, then recovered the source clock as before.

For brightness +20, contrast +32, sharpening +30, all 2,493 short-clip
frames and all 2,900 gas-station frames matched independent FFmpeg bytes,
with source SHA-256 unchanged. Actual viewer-frame hashes also matched
PNG and externally generated eight-frame FFV1 outputs for both sources.
All 86 then-existing regression tests passed. The suite additionally covers matrix/range controls,
individual settings, combinations, playback/navigation and export lifecycle.

## Selection and formats

The dialog supports **current frame**, an **inclusive selected range**, or
**whole video**. Displayed frame numbers are 1-based; command `trim` and audit
source indices are 0-based. Selection is by decoded presentation-order
frame index, not approximate `-ss` seeking or playback-speed frame skips.

| Output | Representation | Pixel preservation |
|---|---|---|
| PNG | RGB24 still, or numbered PNG sequence | Lossless intended processed RGB |
| FFV1 in MKV | FFV1 level 3, BGR0 | Lossless intended processed RGB; not preserved native source YUV |
| H.264 MP4 | libx264, CRF18, medium preset, limited BT.709 YUV420 | **Lossy**, including chroma subsampling and quantization |

For multiple PNGs, choosing `example.png` produces `example-000001.png`,
`example-000002.png`, etc. Numbering starts at 1 even if the selected source
range starts later. The audit maps each output ordinal to its source index.

### Exports from an opened time range

After **Open Time Range...**, the dialog's frame fields and the processing log use
**original source frame numbers** (for example 5318-8861 of 19944), and the third
selection becomes **Whole loaded time range**. The generated FFmpeg command is
byte-identical to the command a fully indexed session would produce for the same
original frames: it still reads the unchanged original file and selects with
`trim=start_frame=N:end_frame=M` counted from the start of the file. Verification
seeks the partially indexed PyAV capture to the exact source PTS of each selected
frame. The `.txt` log adds a `Loaded time range` line (source PTS interval, loaded
source-frame span, and that earlier frames were counted from demuxed packets).

Limitation: FFmpeg's `trim` filter decodes from the start of the file, so exporting
from late in a long video takes as long as before; only *opening* is faster. The
command intentionally keeps exact frame-count selection instead of approximate
`-ss` input seeking.

### Video export playback speed and timing

Video exports automatically snapshot the main viewer playback speed when
the dialog opens; there is no separate export-speed dropdown.
The offered 0.05x-5x range includes tested 0.5x, 1x, 2x and 5x cases.
**Every selected frame is encoded once in presentation order**; the live
viewer's fast-forward omissions are never used for export. No `fps` filter,
frame duplication, speed-dependent selection or audio
is added. PNG images/sequences ignore speed and carry no intrinsic timing.

For video, the graph uses `settb=expr=1/1000000`, then
`setpts=(PTS-STARTPTS)/speed` (serialized as an integer rational factor).
`-fps_mode passthrough -enc_time_base 1:1000000` keeps the scaled source
intervals rather than forcing CFR. Thus variable intervals and nonzero
source origins are supported within the existing input contract.

`setpts` alone does not reliably scale packet durations, particularly the
last displayed frame. A `setts` bitstream filter explicitly leaves PTS and
DTS unchanged, scales packet durations, and sets the last presentation
frame's duration from the selected source's explicit duration divided by
speed. This final-duration expression also handles VFR encoders which
otherwise guess a wrong nominal duration. No final duration is invented
from average FPS when source metadata is missing: video export is blocked.

H.264 uses **`-bf 0`** for deterministic presentation/decode packet alignment.
Tests with the real short clip found output B-frame reordering could leave
the MP4 duration 74 microseconds wrong even when every PTS matched; disabling
output B frames avoids that rounding/duration ambiguity. Source B frames
are still decoded normally, and all selected images are retained.
This changes compatibility encoding efficiency, not the lossless pipeline.

Verification compares each output PTS with
`(source_pts - first_selected_pts) * source_time_base / speed` and checks
strictly increasing timestamps, frame count and container duration
(last expected timestamp plus final source-frame duration divided by speed).
Every frame records expected/actual time and absolute error; the audit
records maximum timestamp/interval errors and expected/measured duration.
Timestamp tolerance is **one output time-base tick plus 3 microseconds**;
duration tolerance is twice that, covering final-PTS and duration rounding.
This is about 1.003 ms / 2.006 ms for MKV and 4 / 8 microseconds for the
generated MP4s. Tolerances are recorded, not unbounded success thresholds.
Timing or duration mismatch fails verification even if lossless pixels match.

External outputs must have a time-base tick no coarser than 1 ms for FFV1
MKV or 1 us for H.264 MP4. Coarse external time bases are rejected rather
than allowing them to inflate timing tolerances and hide a wrong speed.

Matroska quantizes to milliseconds. FFV1 video is blocked when scaled
intervals are below 1 ms; all video formats reject intervals below 3 us.
Exact native timestamps, container/packet bytes and unquantized timing are
not preserved. Time-base-scaled playback is verified within these declared
limits, not promised on arbitrary players or future encoder versions.

Zoom Level and Interpolation Method are exported through export-only scaling.
Audio, other streams, JPEG compression simulation, the right-side Magnified View,
Additional Zoom, lens/crosshair graphics, highlights and overlays are excluded.
Autorotation is disabled to match the viewer's native
orientation; metadata is not copied wholesale into exports.

Verified 8-bit SDR HEVC input uses the same explicit YUV420 conversion,
source PTS index and export verification as H.264; 10-bit/HDR is not supported.
OSVEV's BGR0 FFV1/MKV exports can be reopened with the verified decoder using
full-video Open. Packed RGB input is normalized only by channel packing to BGR24,
not by a YUV matrix/range conversion. Re-export uses the same processing and
pixel/timestamp verification as YUV input. Detailed source records use
`native_rgb_sha256` and a packed B,G,R hash description (unused X bytes and row
padding excluded); `native_yuv_sha256` remains specific to native YUV420 input.

## One shared command mechanism

### Earlier cropped/enhanced demonstration and responsiveness (before export scaling)

The complete demonstration used both included originals, brightness +20,
contrast +32, sharpening +30, JPEG zero, and crop top 13 / bottom 15 /
left 21 / right 23. Original dimensions 1274x720 and 1280x698 became exactly
1230x692 and 1236x670. Lanczos magnification and both highlights were active
in the viewer but did not alter the exported pixels.

The actual live frame 18 matched the single PNG. Videos retained all
30 selected source frames 18-47 at each of 0.5x, 1x, 2x and 5x. Every
FFV1 frame matched the live pipeline and independently filtered FFmpeg;
all H.264 reference frames matched while encoded pixels were reported lossy.
The 2x FFV1 commands were copied/executed in separate PowerShell processes,
then verified using the existing external-output route. Original file
SHA-256 values remained unchanged.

For the gas-station sample, measured durations were:

| Export speed | Expected duration | FFV1 MKV | H.264 MP4 |
|---|---|---|---|
| 0.5x | 2.002000667 s | 2.002 s | 2.002002 s |
| 1x | 1.001000333 s | 1.001 s | 1.001001 s |
| 2x | 0.500500167 s | 0.501 s | 0.500501 s |
| 5x | 0.200200067 s | 0.201 s | 0.200199 s |

Each output's per-frame PTS and total duration passed the recorded
quantization limits. Across both sources, largest duration errors were
under 0.8 ms for MKV and 1.4 us for MP4.

Side-by-side persistent-processing medians were 17.01 ms before / 16.68 ms
with cropping for the short clip, and 15.42 / 15.32 ms for gas. This is
measurement variation, not a claimed acceleration: crop adds one bounded
pixel slice/copy and no new graph, cache, decoder or preview subsystem.
Five-second cropped/enhanced playback runs sustained rolling painted rates
of 29.54 FPS (short) and 29.97-29.99 FPS (gas), with both decoders, zero
missed/late slots and zero omitted source frames. Interval p95 was
approximately 36-37 ms. These are completed offscreen Qt paints, not
physical monitor/vsync guarantees. The earlier random-seek and GUI-thread
work limitations remain; PyAV becoming default does not remove them.

Regression coverage includes exact crop boundaries, odd/1x1 output,
native inspector offsets, unchanged display-only controls, all offered
speed values, VFR/nonzero origins, current/range/whole video, timing failures
even when pixels match, and copied command/hash execution.
All 98 regression tests passed in 145.4 seconds after the final timing
precision checks. Editor diagnostics and Git whitespace checks were clean.

`ffmpeg_export.py` produces one immutable selection/conversion/filter plan.
The dialog shows its PowerShell-ready ordinary FFmpeg command. **Copy command**
puts exactly that text on the clipboard. Literal quoting handles spaces and
apostrophes. OSVEV's direct execution uses the same argument list without a
shell; it does not pipe live preview frames into an encoder.

The common filter chain is:

1. `trim=start_frame=...:end_frame=...` (exclusive end in the command).
2. Video `settb` and rational speed-scaled `setpts`; PNG `setpts=PTS-STARTPTS`.
3. Explicit bicubic matrix/range conversion to full-range BGR24.
4. The shared brightness/contrast/sharpening nodes when enabled.
5. The exact pixel crop when enabled.
6. The selected encoder's representation, with explicit limited BT.709
   conversion/tags for compatibility MP4.

An illustrative contrast +20, crop-to-1200x680, 2x FFV1 command for
BT.709 limited-range 30000/1001-FPS source frames 18 through 25 is:

```powershell
ffmpeg -noautorotate -threads 1 -i "original.mp4" -map 0:v:0 -an `
  -filter_threads 1 -vf "trim=start_frame=17:end_frame=25,settb=expr=1/1000000,setpts=(PTS-STARTPTS)/2,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=limited:out_range=full:flags=bicubic,format=bgr24,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=full:flags=bicubic,format=pix_fmts=yuv444p,eq=brightness=0.00:contrast=1.20,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=full:flags=bicubic,format=pix_fmts=bgr24,crop=w=1200:h=680:x=40:y=9:exact=1" `
  -frames:v 8 -fps_mode passthrough -n -threads 1 -map_metadata -1 `
  -enc_time_base 1:1000000 `
  -bsf:v "setts=pts=PTS:dts=DTS:duration='if(between(PTS,116782,116784),33367/2000000/TB,DURATION/2)'" `
  -c:v ffv1 -level 3 -pix_fmt bgr0 "new-output.mkv"
```

Use the actual dialog-generated command for your source's matrix/range,
settings, selection, destination and progress options; do not substitute
BT.709 for a BT.601 source. Only the live in-process enhancement graph is
persistent; no preview cache, working-copy video or rendering architecture
is required.

## Execute, cancel, or verify external output

- **Export** runs the displayed command on a worker, then
  independently verifies every selected frame. Playback is paused while
  the modal export dialog is open.
- The external-output verification API (`execute_export(..., verify_only=True)`)
  remains available, but its dialog button has been removed.
- Source start/end fields are visible only for **Selected range**.
  **Current frame** and **Whole video** select their bounds automatically.
- **Cancel** interrupts hashing, encoding, or verification. An owned-process
  watchdog also interrupts blocking FFmpeg reads. Closing during execution
  requests cancellation rather than destroying a running worker.

Existing media, processing logs and diagnostic files are not overwritten.
Choose a fresh output name. A cancelled/failed attempt retains any partial
media; **those files are not verified exports**, and no processing log is
created. Optional detailed diagnostics retain frame records and a
failure/cancellation audit. There is no automatic retry, media deletion,
or silent successful fallback.

External verification establishes pixel equality for lossless outputs, not
how an external file was created. Its audit explicitly identifies that the
encoding command was not executed by OSVEV. For lossy files, comparisons
describe observed differences; they do not prove the external encoding
command or its provenance.

## Audit and verification

By default, a successfully verified export writes only the media and
`<chosen-output>.txt` (for example `example.mkv.txt`, or `example.png.txt`
for a PNG sequence). This human-readable processing log includes:

- OSVEV version and Git commit.
- Creation date/time with local UTC offset.
- A one-line verification result (lossless pixel identity, or the lossy
  distinction), a one-line playback-timing result for video, and confirmation
  that the original source hash was unchanged.
- Source filename and SHA-256.
- Every output filename and file SHA-256, including each numbered PNG.
- Processing settings, crop edges, source/output dimensions, frame selection,
  any loaded time range, export zoom/interpolation and video export speed
  (PNG timing is explicitly not applicable).
- Any accepted source frame-count discrepancy: declared, packet and decoded
  counts, audit explanations and the limitation that verification does not
  establish recording completeness. Frame numbers use decoded presentation
  order, not container sample indices. The optional detailed JSON retains the
  full packet-count audit as `source_frame_count_discrepancy`.
- The complete PowerShell-ready reproducible command. It starts with the
  conventional `ffmpeg`; OSVEV executes that same argument list with its
  resolved FFmpeg (the bundled copy in the packaged executable).
- The full FFmpeg version/build output.
- For verify-only use of externally created output, a statement that OSVEV
  did not execute the command.

Detailed statistics are kept out of the text log; enable the diagnostics
checkbox to keep them. These include pixel-difference dictionaries, timing
tolerances, independent decoded-frame hash commands, the hash domain and
processing library versions.

The dialog's **Keep extra verification details (optional)**
checkbox is off by default. Enabling it additionally saves
`<chosen-output>.osvev.json` and `<chosen-output>.frames.jsonl`, including
failure/cancellation status when applicable. Programmatic callers can use
`execute_export(..., detailed_diagnostics=True)`.
The dialog explains that verification and the successful `.txt` processing log
do not depend on this option. Extra records are useful for detailed audits and
troubleshooting, not a prerequisite for running the recorded reproduction command.
Do not confuse them with a working copy's `.conversion.jsonl`, which should stay
with that derivative to preserve its source interpretation and timing provenance.

All verification and streamed per-frame records remain unchanged internally.
With diagnostics disabled they use a temporary directory that is cleaned up
after success, failure or cancellation; only the verified summary is rendered
to the text log, without repeating processing or hashing.

Each internal (optionally retained) frame record contains:

1. Source index, source PTS/time base and native packed Y/U/V SHA-256.
2. SHA-256 of the live OSVEV `FrameProcessor` result.
3. SHA-256 of independent FFmpeg processing directly from the original.
4. SHA-256 of the decoded PNG/FFV1/MP4 output.
5. Equality flags; video-output PTS/time base, expected/actual presentation
   time and timestamp error when applicable.

All comparison hashes use tightly packed row-major **uint8 B,G,R**, identical
dimensions and presentation-order selection. Native YUV hashes use Y, U, V
plane order with decoder row padding removed and are a separate domain.
No cross-domain RGB/YUV hash comparison is implied.

`verified_lossless` requires unchanged source SHA-256, matching reference
processing, matching output pixels for every selected frame, exact
decoded output frame count, and verified video timing/duration.
`verified_lossy_comparison` requires matching reference processing and
verified timing/duration, and records the MP4 pixel differences without claiming
identity. Errors/cancellation are `failed_not_verified` /
`cancelled_not_verified`.

The processing log and optional audit supply `framehash` SHA-256 commands for independently
filtered source and decoded output, both explicitly BGR24. Compare the hash
fields in aligned frame order; do not demand equality of complete framehash
text files when timestamps or time bases differ. Matroska timing quantization
and PNG-sequence timing are not pixel changes.

Independent framehash commands also use a microsecond encoder time base
so accelerated PTS are not collapsed onto a guessed original-FPS grid.
Hash equality is still compared by aligned frame ordinal, independently
of differing container time bases.

Verification rereads the source and output using bounded, persistent pipes.
It can take substantial time for whole-video exports; it is not merely a
first-frame spot check. Results apply to the recorded installed toolchain.
Hashes do not certify metadata truth, monitor color management, evidence
authenticity, or future-version equivalence.

## SWGDE reference

The supplied *Technical Notes on FFmpeg for Forensic Video Examinations*,
16-V-002, version 3.0 (final approved March 22, 2024), informs the relevant
techniques here:

- Section 9.4: FFV1 lossless transcoding, suitable MKV/AVI containers and
  player-compatibility limitations. OSVEV uses MKV.
- Sections 9.8-9.9: individual/sequential image extraction and selected
  portions. OSVEV deliberately uses decoded frame indices for exact
  selection instead of approximate time seeking.
- Section 12.1: decoded frame/stream hashing and comparison. OSVEV uses
  explicit BGR24 SHA-256 rather than comparing unqualified default formats.

The document's older `-vsync` examples are implemented with the current
`-fps_mode passthrough` option where relevant. Lossless encoding alone
does not establish equivalence after color conversion or effects.
This implementation is not a SWGDE endorsement or certification.
