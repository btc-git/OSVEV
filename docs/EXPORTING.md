# Command-driven export and reproducibility

## Overview

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
[Experimental duplicate-PTS compatibility](TECHNICAL.md#experimental-duplicate-pts-compatibility).
PNGs ignore playback speed.

Exports warn before unusually large scaled resolutions (over 8192 pixels on
either axis or over 33,177,600 pixels), without imposing an arbitrary zoom cap.
Encoding may still reject resolutions beyond its own limits.

Audio, Additional Zoom, the right-side Magnified View, highlights, overlays,
and native-YUV preservation are not part of these RGB exports. The sections below describe the workflow, exact contracts, hashing domain,
limits, and relevant SWGDE reference sections.

## GPL FFmpeg `eq` pipeline

OSVEV uses standard FFmpeg `eq` and `unsharp` for its enhancements. These
replaced custom algorithms used by earlier OSVEV development versions. Use the
`osvev` conda environment described in [Building from source](BUILDING.md), not the ordinary PyPI
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
