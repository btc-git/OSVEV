# Export development history

Historical validation and benchmark notes moved from
[Export and reproducibility](../EXPORTING.md). They describe earlier
development builds and are kept for reference; that document describes current behavior.

## Validation and dependency tradeoff when adopting `eq`

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
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) for licensing and remaining
distribution obligations. The runtime is maintainable using standard conda
packages, but less simple to install than the earlier PyPI-only
dependencies.

## Historical pre-`eq` shared-enhancement benchmark

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

## Earlier cropped/enhanced demonstration and responsiveness (before export scaling)

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
