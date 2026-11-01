# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Verified native input, with an explicit reproducible BGR boundary."""

from dataclasses import dataclass
from collections.abc import Callable
from collections import Counter
from itertools import chain
import av
import cv2
import numpy as np
from av.video.reformatter import ColorRange, Colorspace, Interpolation, VideoReformatter
from numpy.typing import NDArray
from color_interpretation import (
    ColorChoices, MissingColorMetadata, native_reported, resolve_reported,
    conversion_stages, NATIVE_FIELDS, InputDecodeError, InputCompatibilityError, color_audit,
)


@dataclass(frozen=True)
class NativeSamples:
    """Unmodified source planes, separate from the converted display array."""

    frame: av.VideoFrame
    y: NDArray[np.uint8]
    u: NDArray[np.uint8]
    v: NDArray[np.uint8]
    matrix: str
    range_name: str
    chroma_location: str = "left"

    @property
    def hash_format(self) -> str:
        return "SHA-256; planar uint8 Y then U then V; 4:2:0; decoder row padding removed"

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """Return stored 4:2:0 samples, not an interpolated chroma estimate."""
        if not 0 <= x < self.frame.width or not 0 <= y < self.frame.height:
            raise ValueError("Native sample position is outside the source frame.")
        return int(self.y[y, x]), int(self.u[y // 2, x // 2]), int(self.v[y // 2, x // 2])

    def packed_bytes(self) -> bytes:
        """Y then U then V, without decoder alignment padding."""
        return self.y.tobytes() + self.u.tobytes() + self.v.tobytes()


@dataclass(frozen=True)
class NativeRGBSamples:
    """Meaningful packed RGB-family samples; no row padding or unused X byte."""

    frame: av.VideoFrame
    samples: NDArray[np.uint8]
    matrix: str = "rgb"
    range_name: str = "full"

    @property
    def hash_format(self) -> str:
        return "SHA-256; packed row-major uint8 B,G,R; unused X byte and decoder row padding removed"

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """Return native R/G/B, not RGB-derived YCbCr."""
        if not 0 <= x < self.frame.width or not 0 <= y < self.frame.height:
            raise ValueError("Native sample position is outside the source frame.")
        b, g, r = self.samples[y, x]
        return int(r), int(g), int(b)

    def packed_bytes(self) -> bytes:
        return self.samples.tobytes()


def _audio_stream_info(stream):
    """Container metadata only; no audio is decoded or analyzed."""
    context = getattr(stream, "codec_context", None)
    channels = getattr(context, "channels", None)
    rate = getattr(context, "sample_rate", None) or getattr(stream, "rate", None)
    return {
        "codec": getattr(context, "name", None) or None,
        "channels": int(channels) if channels else None,
        "sample_rate": int(rate) if rate else None,
    }


class PyAVInputCapture:
    """Small VideoCapture-compatible boundary; no effects, rendering, or cache."""

    def __init__(self, path: str, *, color_choices: ColorChoices | None = None):
        self._container = None
        self._path = path
        self.has_duplicate_pts = False
        self.count_discrepancy: dict | None = None
        self._unique_pts_indices: dict[int, int] = {}
        self.color_choices = color_choices
        self.effective_color = None
        self.original_native_color = None
        self.color_audit = None
        self.chroma_location = "left"
        self._color_graph = None
        self.native_samples: NativeSamples | NativeRGBSamples | None = None
        self._timestamps: tuple[int, ...] = ()
        self._next_index = 0
        self._seek_pending = False
        self._partial = False
        self._signature: tuple[int, int, str, int, int, int, int] | None = None
        self._reformatter = VideoReformatter()
        try:
            self._container = av.open(path)
            self.has_audio = bool(self._container.streams.audio)
            self.audio_streams = tuple(
                _audio_stream_info(stream) for stream in self._container.streams.audio)
            self.media_start_seconds = (
                self._container.start_time / av.time_base
                if self._container.start_time is not None else None
            )
            if not self._container.streams.video:
                raise InputDecodeError("The file contains no video stream.")
            self._stream = self._container.streams.video[0]
            context = self._stream.codec_context
            if context.name not in ("h264", "hevc", "ffv1"):
                raise InputCompatibilityError(
                    f"Unsupported input codec {context.name!r}. Supported: H.264/HEVC YUV420 "
                    "or packed 8-bit RGB-family FFV1."
                )
            context.thread_count = 1
            self._iterator = self._container.decode(self._stream)
            self._time_base = self._stream.time_base
            self._frame_rate = self._stream.average_rate
            self._frame_count = self._stream.frames
            if (self._time_base is None or self._time_base <= 0
                    or self._frame_rate is None or self._frame_rate <= 0):
                raise InputDecodeError("Explicit stream timing is required for PyAV input.")
        except InputCompatibilityError:
            self.release()
            raise
        except (av.FFmpegError, OSError, ValueError, InputDecodeError) as error:
            self.release()
            raise InputDecodeError(f"Cannot open PyAV input: {error}") from error

    def isOpened(self) -> bool:
        return self._container is not None

    def release(self) -> None:
        if self._container is not None:
            self._container.close()
            self._container = None
        self.native_samples = None
        self._timestamps = ()
        self._color_graph = None

    def get(self, property_id: int) -> float:
        if property_id == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._frame_count)
        if property_id == cv2.CAP_PROP_FPS:
            return float(self._frame_rate)
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            return float(self._next_index)
        raise ValueError(f"Unsupported input property: {property_id}")

    def _audit_count_discrepancy(self, metadata, decoded, cancelled):
        """Reuse the working-copy packet audit; only fully explained non-AVI gaps may open."""
        from ffmpeg_export import ExportCancelled
        from frame_info import MetadataCancelled
        from working_copy import WorkingCopyError, audit_count_discrepancy
        reason = (f"FFprobe and container frame counts disagree: the container declares "
                  f"{self._frame_count} frames but {decoded} were decoded.")
        try:
            audit = audit_count_discrepancy(self._path, metadata, self._frame_count,
                                            cancelled or (lambda: False))
        except ExportCancelled as error:
            raise MetadataCancelled() from error
        except WorkingCopyError as error:
            # Still refused for viewing; compatibility class offers the verified working copy.
            raise InputCompatibilityError(f"{reason} {error}") from error
        except (av.FFmpegError, OSError) as error:
            raise InputCompatibilityError(f"{reason} The packet index could not be audited: {error}") from error
        if audit["avi"]:
            # AVI recovery needs the working copy's independent corroboration.
            raise InputCompatibilityError(reason)
        return audit

    def configure_index(self, metadata: list[dict], *, partial: bool = False,
                        cancelled: Callable[[], bool] | None = None) -> None:
        """Use exact source PTS, not rounded seconds or index/average-FPS guesses.

        ``partial`` indexes a contiguous working range from get_range_metadata;
        positions then start at that range, and seeking still targets exact PTS.
        Center-sited input also rewinds to zero: the initial structural-validation
        read precedes siting information, which PyAV does not expose natively.
        Callers must read the first indexed frame before presenting its pixels.
        """
        if not metadata:
            raise InputDecodeError("PyAV input requires complete FFprobe frame metadata.")
        count_discrepancy = None
        timestamps = []
        expected = self.native_samples
        if expected is None:
            raise InputDecodeError("Read the first native frame before indexing.")
        rgb = isinstance(expected, NativeRGBSamples)
        if partial and rgb:
            raise InputDecodeError("RGB FFV1 currently requires full-video opening, not Open Time Range.")
        matrix_tag = "gbr" if rgb else ("bt709" if expected.matrix == "bt709" else "smpte170m")
        range_tag = "tv" if expected.range_name == "limited" else "pc"
        required = {
            "width": expected.frame.width, "height": expected.frame.height,
            "pix_fmt": expected.frame.format.name, "color_space": matrix_tag,
            "color_range": range_tag,
            "color_primaries": "bt709" if expected.frame.color_primaries == 1 else "smpte170m",
            "color_transfer": "bt709" if expected.frame.color_trc == 1 else "smpte170m",
            "interlaced_frame": 0,
        }
        if not rgb:
            required["chroma_location"] = resolve_reported(
                metadata[0], self.color_choices)["chroma_location"]
            if self.color_choices is not None:
                required.update({key: value for key, value in self.effective_color.items()
                                 if key != "chroma_location"})
        from frame_info import FrameInfoHandler, MetadataCancelled
        for number, entry in enumerate(metadata):
            if number % 256 == 0 and cancelled is not None and cancelled():
                raise MetadataCancelled()
            FrameInfoHandler.validate_display_transforms(entry)
            compared = entry
            if not rgb:
                # Reject explicit HDR side data even if its color tags are unspecified.
                reject_hdr_side_data(entry.get("side_data_list", []))
                resolved = resolve_reported(entry, self.color_choices)
                if self.color_choices is not None:
                    compared = {**entry, **resolved}
            differences = [f"{key}={entry.get(key)!r} (required {value!r})"
                           for key, value in required.items() if compared.get(key) != value]
            if differences:
                raise InputDecodeError(
                    "Missing, unsupported, or changing source color/format metadata: "
                    + "; ".join(differences) + ". "
                    "Supported: progressive 8-bit H.264/HEVC 4:2:0, left/center chroma, "
                    "BT.709/SMPTE170M, explicit limited/full range and SDR tags; "
                    "or full-range packed RGB FFV1 with RGB matrix and explicit SDR tags."
                )
            pts = entry.get("pts")
            if type(pts) is not int or (timestamps and pts < timestamps[-1]):
                raise InputCompatibilityError("Present, nondecreasing integer frame PTS are required (missing or decreasing PTS).")
            timestamps.append(pts)
        counts = Counter(timestamps)
        duplicate_pts = any(count > 1 for count in counts.values())
        if partial and duplicate_pts:
            raise InputDecodeError(
                "Duplicate PTS require the complete verified frame index; open Entire Video."
            )
        if partial:
            if self._frame_count and len(timestamps) > self._frame_count:
                raise InputDecodeError("Partial index exceeds the container frame count.")
            self._next_index, self._seek_pending = 0, True
        else:
            if timestamps[0] != expected.frame.pts:
                raise InputDecodeError("FFprobe and PyAV disagree on the first presentation PTS.")
            if self._frame_count and self._frame_count != len(timestamps):
                count_discrepancy = self._audit_count_discrepancy(metadata, len(timestamps), cancelled)
        self._timestamps = tuple(timestamps)
        self.has_duplicate_pts = duplicate_pts
        self.count_discrepancy = count_discrepancy
        self._unique_pts_indices = (
            {pts: index for index, pts in enumerate(timestamps) if counts[pts] == 1}
            if duplicate_pts else {}
        )
        self._frame_count = len(timestamps)
        self._partial = partial
        if not rgb:
            self.chroma_location = required["chroma_location"]
        if not rgb and (self.color_choices is not None or self.chroma_location == "center"):
            self.effective_color = resolve_reported(metadata[0], self.color_choices)
            self.color_audit = color_audit(metadata, self.color_choices, self.original_native_color)
            self._color_graph = None
            if self.chroma_location == "center":
                self._next_index, self._seek_pending = 0, True

    def _seek_by_ordinal(self, index: int) -> None:
        """Locate an unambiguous decoded anchor, then verify/count every source frame."""
        self._container.seek(
            self._timestamps[index], stream=self._stream, backward=True, any_frame=False)
        iterator = self._container.decode(self._stream)
        first = next(iterator, None)
        anchor = self._unique_pts_indices.get(first.pts) if first is not None else None
        if anchor is None or anchor > index:
            # A timestamp seek into a tied group cannot establish its occurrence.
            # A fresh demux/decode starts at source ordinal zero, without guessing.
            container = av.open(self._path)
            try:
                stream = container.streams.video[0]
                stream.codec_context.thread_count = 1
                if stream.time_base != self._time_base:
                    raise InputDecodeError("Source time base changed while reopening for seeking.")
            except (IndexError, ValueError, InputDecodeError):
                container.close()
                raise
            self._container.close()
            self._container, self._stream = container, stream
            iterator = container.decode(stream)
            first = next(iterator, None)
            anchor = 0
        for ordinal, frame in enumerate(chain(() if first is None else (first,), iterator), anchor):
            if ordinal >= len(self._timestamps) or frame.pts != self._timestamps[ordinal]:
                raise InputDecodeError("Decoded frame sequence does not match the verified source index.")
            if ordinal == index:
                self._iterator = chain((frame,), iterator)
                return
        raise InputDecodeError("Decoder ended before the requested source frame occurrence.")

    def set(self, property_id: int, value: float) -> bool:
        if property_id != cv2.CAP_PROP_POS_FRAMES:
            raise ValueError(f"Unsupported input property: {property_id}")
        index = int(value)
        if value != index or not 0 <= index < self._frame_count:
            raise InputDecodeError("Requested frame index is outside the source.")
        if not self._timestamps:
            raise InputDecodeError("Exact seeking requires the source PTS index.")
        self._seek_pending = self._seek_pending or index != self._next_index
        self._next_index = index
        return True

    def _validate_frame(self, frame: av.VideoFrame) -> tuple[str, str]:
        if frame.is_corrupt:
            raise InputDecodeError(
                f"Decoder flagged a corrupt video frame at PTS {frame.pts} "
                f"(time base {frame.time_base}). Verified viewing/export refused: "
                "decoder-concealed pixels are not verified source imagery. "
                "Transcoding cannot establish the missing original pixels."
            )
        rgb = self._stream.codec_context.name == "ffv1"
        if any(side.type.name == "DISPLAYMATRIX" for side in frame.side_data):
            raise InputDecodeError("Unsupported source display matrix/rotation; no transform is applied.")
        if frame.pts is None:
            raise InputCompatibilityError("Progressive frames with explicit PTS and the stream time base are required.")
        if frame.time_base != self._time_base or frame.interlaced_frame:
            raise InputDecodeError("Progressive frames with explicit PTS and the stream time base are required.")
        effective = None
        if not rgb:
            if frame.format.name not in ("yuv420p", "yuvj420p") or frame.width % 2 or frame.height % 2:
                raise InputDecodeError("Unsupported or ambiguous native input: progressive 8-bit even-sized YUV420 required.")
            reject_hdr_side_data([{"side_data_type": side.type.name} for side in frame.side_data])
            reported = native_reported(frame, self._stream.codec_context)
            effective = resolve_reported(reported, self.color_choices, include_chroma=False)
            if self.color_choices is not None:
                self.effective_color = {**effective, "chroma_location": self.chroma_location}
            if self.original_native_color is None:
                self.original_native_color = {
                    field: {"frame": getattr(frame, attr),
                            "stream": getattr(self._stream.codec_context, attr)}
                    for field, attr, _, _ in NATIVE_FIELDS
                }
            if frame.format.name == "yuvj420p" and effective["color_range"] != "pc":
                raise InputDecodeError("Native pixel format and color range contradict each other.")
        if (
            (rgb and (
                frame.format.name not in ("bgr0", "rgb0", "bgr24", "rgb24")
                or frame.colorspace != 0 or frame.color_range != 2
            ))
            or (not rgb and (
                frame.format.name not in ("yuv420p", "yuvj420p")
                or frame.width % 2 or frame.height % 2
                or (self.color_choices is None and (
                    frame.colorspace not in (1, 6) or frame.color_range not in (1, 2)))
            ))
            or ((rgb or self.color_choices is None) and (
                frame.color_primaries not in (1, 6) or frame.color_trc not in (1, 6)))
        ):
            error_type = (InputCompatibilityError if rgb and frame.format.name in ("yuv420p", "yuvj420p")
                          else InputDecodeError)
            raise error_type(
                f"Unsupported or ambiguous native input: {self._stream.codec_context.name}, "
                f"{frame.format.name}, {frame.width}x{frame.height}, "
                f"matrix={frame.colorspace}, range={frame.color_range}, "
                f"primaries={frame.color_primaries}, transfer={frame.color_trc}. "
                "Explicit progressive 8-bit "
                "H.264/HEVC YUV420, BT.709/SMPTE170M matrix, SDR primaries/transfer, "
                "range, and PTS are required. FFV1 requires packed 8-bit BGR0/RGB0/"
                "BGR24/RGB24, explicit full-range RGB matrix and supported SDR tags."
            )
        if frame.format.name == "yuvj420p" and frame.color_range != 2 and self.color_choices is None:
            raise InputDecodeError("Native pixel format and color range contradict each other.")
        for field, unspecified in (
            ("colorspace", 2), ("color_range", 0),
            ("color_primaries", 2), ("color_trc", 2),
        ):
            stream_value = getattr(self._stream.codec_context, field)
            if (self.color_choices is None and stream_value != unspecified
                    and stream_value != getattr(frame, field)):
                raise InputDecodeError(f"Stream and frame {field} metadata contradict each other.")
        signature = (
            frame.width, frame.height, frame.format.name, frame.colorspace,
            frame.color_range, frame.color_primaries, frame.color_trc,
        )
        if self._signature is not None and signature != self._signature:
            raise InputDecodeError("Mid-stream format/color changes are not supported.")
        self._signature = signature
        if rgb:
            return "rgb", "full"
        if self.color_choices is not None or self.chroma_location == "center":
            return (
                "bt709" if effective["color_space"] == "bt709" else "bt601",
                "limited" if effective["color_range"] == "tv" else "full",
            )
        return (
            "bt709" if frame.colorspace == 1 else "bt601",
            "limited" if frame.color_range == 1 else "full",
        )

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        if self._container is None:
            raise InputDecodeError("The PyAV source is closed.")
        try:
            index = self._next_index
            if self._partial and index >= self._frame_count:
                return False, None
            target = self._timestamps[index] if self._timestamps and index < self._frame_count else None
            if self._seek_pending:
                if self.has_duplicate_pts:
                    self._seek_by_ordinal(index)
                else:
                    self._container.seek(target, stream=self._stream, backward=True, any_frame=False)
                    self._iterator = self._container.decode(self._stream)
                self._color_graph = None
            seeking = self._seek_pending and not self.has_duplicate_pts
            for frame in self._iterator:
                if self._timestamps and index >= self._frame_count:
                    raise InputDecodeError("The decoder produced extra, unindexed frames.")
                if seeking and frame.pts is not None and frame.pts < target:
                    continue
                if target is not None and frame.pts != target:
                    raise InputDecodeError("Decoded frame PTS does not match the requested index.")
                matrix, range_name = self._validate_frame(frame)
                if matrix == "rgb":
                    plane = frame.planes[0]
                    channels = 4 if frame.format.name in ("bgr0", "rgb0") else 3
                    samples = np.frombuffer(plane, dtype=np.uint8).reshape(
                        frame.height, plane.line_size
                    )[:, :frame.width * channels].reshape(frame.height, frame.width, channels)[:, :, :3]
                    if frame.format.name.startswith("rgb"):
                        samples = samples[:, :, ::-1]
                    samples = samples.copy()
                    samples.setflags(write=False)
                    self.native_samples = NativeRGBSamples(frame, samples)
                    self._next_index = index + 1
                    self._seek_pending = False
                    return True, samples.copy()
                colorspace = Colorspace.ITU709 if matrix == "bt709" else Colorspace.ITU601
                color_range = ColorRange.MPEG if range_name == "limited" else ColorRange.JPEG
                if self.color_choices is not None or self.chroma_location == "center":
                    if self._color_graph is None:
                        graph = av.filter.Graph()
                        graph.threads = 1
                        graph.link_nodes(
                            graph.add_buffer(template=frame),
                            *(graph.add(name, options) for name, options
                              in conversion_stages(self.effective_color)),
                            graph.add("buffersink"),
                        )
                        graph.configure()
                        self._color_graph = graph
                    self._color_graph.push(frame)
                    converted = self._color_graph.pull().to_ndarray(format="bgr24")
                else:
                    converted = self._reformatter.reformat(
                        frame, format="bgr24", src_colorspace=colorspace,
                        dst_colorspace=colorspace, src_color_range=color_range,
                        dst_color_range=ColorRange.JPEG, interpolation=Interpolation.BICUBIC,
                        threads=1,
                    ).to_ndarray()
                planes = []
                for plane in frame.planes:
                    samples = np.frombuffer(plane, dtype=np.uint8).reshape(
                        plane.height, plane.line_size
                    )[:, :plane.width]
                    samples.setflags(write=False)
                    planes.append(samples)
                self.native_samples = NativeSamples(
                    frame, *planes, matrix, range_name, self.chroma_location)
                self._next_index = index + 1
                self._seek_pending = False
                return True, converted
            if self._timestamps and index < self._frame_count:
                raise InputDecodeError("The decoder ended before the indexed frame.")
            return False, None
        except (MissingColorMetadata, InputCompatibilityError):
            self._seek_pending = True
            raise
        except (av.FFmpegError, OSError, ValueError, InputDecodeError) as error:
            self._seek_pending = True
            raise InputDecodeError(f"PyAV frame decoding/conversion failed: {error}") from error


def reject_hdr_side_data(records):
    for record in records:
        name = record.get("side_data_type", "").lower().replace("_", " ")
        if any(token in name for token in ("mastering", "content light", "hdr", "dovi", "dolby")):
            raise InputDecodeError("Unsupported HDR/Dolby Vision side data; SDR assumptions cannot override it.")
