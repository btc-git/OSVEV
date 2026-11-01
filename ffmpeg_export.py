# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""One ordinary FFmpeg plan for selected, reproducible source-video exports."""

from dataclasses import dataclass
from collections import defaultdict
from color_interpretation import ColorChoices, conversion_stages
from fractions import Fraction
import math
from pathlib import Path
import re

import cv2

from processing_filters import enhancement_filter, validate_processing_settings
from crop_utils import (CROP_KEYS, crop_rectangle, rotated_size, rotation_filter,
                        validate_rotation)
from export_scaling import scaling_filter


class ExportError(RuntimeError):
    """An export cannot meet its declared contract."""


class ExportCancelled(RuntimeError):
    """Export or verification was explicitly interrupted."""


def powershell_command(arguments: list[str] | tuple[str, ...]) -> str:
    """Literal PowerShell argument quoting; execution itself never uses a shell."""
    return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)


def validate_settings(settings: dict) -> dict[str, int]:
    result = {}
    for name, low, high in (
        ("brightness", -100, 100), ("contrast", -100, 100),
        ("sharpness", 0, 100), ("compression", 0, 99),
        *((name, 0, 100000) for name in CROP_KEYS),
    ):
        value = settings.get(name, 0)
        if type(value) is not int or not low <= value <= high:
            raise ExportError(f"Invalid {name} setting: {value}")
        result[name] = value
    try:
        result["rotation"] = validate_rotation(settings.get("rotation", 0))
    except ValueError as error:
        raise ExportError(str(error)) from error
    validate_processing_settings(*(result[name] for name in (
        "brightness", "contrast", "sharpness", "compression"
    )))
    if result["compression"]:
        raise ExportError(
            "JPEG compression is an OpenCV teaching simulation, not an exact FFmpeg "
            "codec equivalent. Set compression to zero to export the reproducible "
            "brightness/contrast/sharpening pipeline; it will not be silently omitted."
        )
    return result


@dataclass(frozen=True)
class ExportPlan:
    source: str
    destination: str
    kind: str
    first: int
    last: int
    frame_count: int
    width: int
    height: int
    matrix: str
    range_name: str
    primaries: str
    transfer: str
    time_base: str
    fps: float
    settings: dict[str, int]
    filter_graph: str
    source_width: int
    source_height: int
    playback_speed: float
    expected_times: tuple[Fraction, ...]
    expected_duration: Fraction
    encoder_last_pts: int
    zoom_level: int
    interpolation: str
    source_offset: int = 0
    partial_index: bool = False
    loaded_range: tuple = ()  # (start seconds, end seconds, loaded frame count) for partial index
    color_choices: ColorChoices | None = None
    color_audit: dict | None = None
    preserve_source_pts: bool = False
    duplicate_groups: tuple = ()  # (original PTS, 1-based source frame numbers)
    count_discrepancy: dict | None = None
    @property
    def count(self) -> int:
        return self.last - self.first + 1

    @property
    def lossless(self) -> bool:
        return self.kind != "h264"

    @property
    def sequence(self) -> bool:
        return self.kind == "png" and self.count > 1

    @property
    def report_path(self) -> Path:
        return Path(self.destination + ".osvev.json")

    @property
    def log_path(self) -> Path:
        return Path(self.destination + ".txt")

    @property
    def records_path(self) -> Path:
        return Path(self.destination + ".frames.jsonl")

    @property
    def output_pattern(self) -> str:
        path = Path(self.destination)
        return str(path.with_name(path.stem + "-%06d.png")) if self.sequence else self.destination

    def output_path(self, ordinal: int) -> Path:
        if not 0 <= ordinal < self.count:
            raise ValueError("Output ordinal is outside the selection.")
        return Path(self.output_pattern.replace("%06d", f"{ordinal + 1:06d}"))

    def source_arguments(self) -> list[str]:
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
            *(["-copyts"] if self.preserve_source_pts else []),
            "-noautorotate", "-threads", "1", "-err_detect", "explode", "-i", self.source,
            "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", self.filter_graph, "-frames:v", str(self.count),
            "-fps_mode", "passthrough",
        ]

    def command(self) -> list[str]:
        arguments = self.source_arguments() + [
            "-n", "-threads", "1", "-map_metadata", "-1", "-metadata:s:v:0", "rotate=0",
        ]
        if self.kind != "png":
            speed = Fraction(str(self.playback_speed))
            tail = self.expected_duration - self.expected_times[-1]
            duration = (
                f"if(between(PTS,{self.encoder_last_pts - 1},{self.encoder_last_pts + 1}),"
                f"{tail.numerator}/{tail.denominator}/TB,"
                f"DURATION*{speed.denominator}/{speed.numerator})"
            )
            if self.preserve_source_pts:
                duration = f"if(eq(N,{self.count - 1}),{tail.numerator}/{tail.denominator}/TB,DURATION)"
            arguments += [
                "-enc_time_base", "1:1000000",
                "-bsf:v", f"setts=pts=PTS:dts=DTS:duration='{duration}'",
            ]
        if self.kind == "png":
            arguments += ["-c:v", "png", "-pix_fmt", "rgb24"]
            if self.sequence:
                arguments += ["-start_number", "1"]
            else:
                arguments += ["-update", "1"]
        elif self.kind == "ffv1":
            arguments += ["-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0"]
        else:
            arguments += [
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-bf", "0",
                "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", self.primaries, "-color_trc", self.transfer,
                "-movflags", "+faststart",
            ]
            graph = self.filter_graph + (
                ",scale=in_color_matrix=bt709:out_color_matrix=bt709:"
                "in_range=full:out_range=limited:flags=bicubic,format=yuv420p,"
                f"setparams=range=limited:colorspace=bt709:"
                f"color_primaries={self.primaries}:color_trc={self.transfer}"
            )
            arguments[arguments.index("-vf") + 1] = graph
        return arguments + ["-progress", "pipe:1", "-nostats", self.output_pattern]

    def reference_command(self, *, hashes=False) -> list[str]:
        tail = ["-f", "framehash", "-hash", "sha256", "-"] if hashes else ["-f", "rawvideo", "pipe:1"]
        return self.source_arguments() + [
            "-enc_time_base", "1:1000000", "-threads", "1", "-pix_fmt", "bgr24", *tail
        ]

    def output_decode_command(self, *, hashes=False) -> list[str]:
        arguments = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1"]
        if self.sequence:
            arguments += ["-framerate", str(self.fps), "-start_number", "1"]
        arguments += ["-noautorotate", "-i", self.output_pattern, "-map", "0:v:0", "-an"]
        if self.kind == "h264":
            arguments += [
                "-filter_threads", "1", "-vf",
                "scale=in_color_matrix=bt709:out_color_matrix=bt709:"
                "in_range=limited:out_range=full:flags=bicubic",
            ]
        tail = ["-f", "framehash", "-hash", "sha256", "-"] if hashes else ["-f", "rawvideo", "pipe:1"]
        return arguments + [
            "-fps_mode", "passthrough", "-enc_time_base", "1:1000000",
            "-threads", "1", "-pix_fmt", "bgr24", *tail,
        ]

    def check_destination(self, *, check_outputs=True, verify_only=False) -> None:
        destination = Path(self.destination)
        if not destination.parent.is_dir():
            raise ExportError("Select an existing destination folder.")
        paths = [self.log_path, self.report_path, self.records_path]
        if check_outputs and not verify_only:
            paths.append(destination)
        if self.sequence and check_outputs and not verify_only:
            pattern = re.compile(re.escape(destination.stem) + r"-\d{6,}\.png$", re.IGNORECASE)
            if any(pattern.fullmatch(path.name) for path in destination.parent.iterdir()):
                raise ExportError("The PNG sequence prefix already exists. Choose a new name.")
        for path in paths:
            if path.exists():
                raise ExportError(f"Existing files are never overwritten: {path}")
        if destination.resolve() == Path(self.source).resolve():
            raise ExportError("The original input cannot be an output destination.")
        if verify_only:
            outputs = [self.output_path(index) for index in range(self.count)] if self.sequence else [destination]
            if any(not path.is_file() for path in outputs):
                raise ExportError("The selected output does not exist completely; execute the command first.")
            if any(path.resolve() == Path(self.source).resolve()
                   or path.samefile(self.source) for path in outputs):
                raise ExportError("Verification output cannot be the original input or a link to it.")


def build_export_plan(source: str, destination: str, kind: str, first: int, last: int,
                      settings: dict, metadata: list[dict], decoder_backend: str, *,
                      allow_existing_output=False, playback_speed: float = 1.0,
                      zoom_level: int = 1, interpolation: str = "Nearest",
                      source_range=None, color_choices: ColorChoices | None = None) -> ExportPlan:
    """first/last are original source frame indices, also for a partially indexed range."""
    settings = validate_settings(settings)
    if decoder_backend != "pyav":
        raise ExportError(
            "Verified export requires the explicitly controlled PyAV baseline. "
            "Launch with --decoder pyav and reload the source. The legacy OpenCV "
            "BT.709 interpretation is not silently substituted."
        )
    if kind not in ("png", "ffv1", "h264"):
        raise ExportError("Unsupported output format.")
    if (isinstance(playback_speed, bool) or not isinstance(playback_speed, (int, float))
            or not math.isfinite(playback_speed) or not 0.05 <= playback_speed <= 5):
        raise ExportError("Export playback speed must be finite and between 0.05x and 5x.")
    playback_speed = 1.0 if kind == "png" else float(playback_speed)
    offset = 0 if source_range is None else source_range.offset
    frame_count = len(metadata) if source_range is None else source_range.source_frame_count
    if (type(first) is not int or type(last) is not int
            or not offset <= first <= last < offset + len(metadata)):
        raise ExportError("Select an inclusive frame range inside the loaded video.")
    expected_suffix = {"png": ".png", "ffv1": ".mkv", "h264": ".mp4"}[kind]
    path = Path(destination).absolute()
    if path.suffix.lower() != expected_suffix:
        raise ExportError(f"{kind} output requires the {expected_suffix} extension.")
    if kind == "png" and "%" in str(path):
        raise ExportError("PNG output names cannot contain percent signs (sequence placeholder).")
    from pyav_input import PyAVInputCapture
    capture = PyAVInputCapture(str(Path(source).absolute()), color_choices=color_choices)
    try:
        ok, _ = capture.read()
        if not ok or capture.native_samples is None:
            raise ExportError("No validated first input frame.")
        capture.configure_index(metadata, partial=source_range is not None)
        native = capture.native_samples
        width, height = native.frame.width, native.frame.height
        matrix, range_name = native.matrix, native.range_name
        time_base = str(native.frame.time_base)
        fps = capture.get(cv2.CAP_PROP_FPS)
        effective_color = capture.effective_color
        audit = capture.color_audit
        count_discrepancy = capture.count_discrepancy
    finally:
        capture.release()
    if not math.isfinite(fps) or fps <= 0:
        raise ExportError("Invalid source rate.")
    if any(entry.get("rotation", 0) for entry in metadata):
        raise ExportError("Display rotation is not supported for verified export.")
    try:
        crop = crop_rectangle(width, height, **{key: settings[key] for key in CROP_KEYS})
    except ValueError as error:
        raise ExportError(str(error)) from error
    rotated_width, rotated_height = rotated_size(crop.width, crop.height, settings["rotation"])
    try:
        resize_filter = scaling_filter(rotated_width, rotated_height, zoom_level, interpolation)
    except ValueError as error:
        raise ExportError(str(error)) from error
    output_width, output_height = rotated_width * zoom_level, rotated_height * zoom_level
    if kind == "h264" and (output_width % 2 or output_height % 2):
        raise ExportError(
            f"H.264 YUV420 requires even dimensions, but the cropped/rotated/scaled output is {output_width}x{output_height}. "
            "Change the crop or zoom explicitly or choose PNG/FFV1. No pixels are padded, resized or removed automatically."
        )
    speed = Fraction(str(playback_speed))
    base = Fraction(time_base)
    groups = defaultdict(list)
    for index, entry in enumerate(metadata, offset + 1):
        groups[entry["pts"]].append(index)
    duplicate_groups = tuple((pts, tuple(indices)) for pts, indices in groups.items()
                             if len(indices) > 1)
    selected_pts = tuple(metadata[index - offset]["pts"] for index in range(first, last + 1))
    selected_ties = any(a == b for a, b in zip(selected_pts, selected_pts[1:]))
    if selected_ties and kind == "h264":
        raise ExportError("H.264/MP4 cannot verifiably preserve duplicate PTS. Choose PNG or FFV1/MKV.")
    preserve_source_pts = selected_ties and kind == "ffv1"
    if preserve_source_pts and playback_speed != 1:
        raise ExportError("Duplicate-PTS FFV1 requires 1x export speed to preserve original timestamps.")
    origin = metadata[first - offset]["pts"]
    expected_times = tuple((pts - (0 if preserve_source_pts else origin)) * base / speed
                           for pts in selected_pts)
    if preserve_source_pts and any(t < 0 or (t * 1000).denominator != 1 for t in expected_times):
        raise ExportError("Duplicate-PTS FFV1 requires nonnegative original timestamps exactly representable in Matroska milliseconds.")
    expected_duration = Fraction(0)
    final = metadata[last - offset]

    def microsecond_pts(pts):
        ticks = pts * base * 1000000
        return int(ticks + Fraction(1, 2)) if ticks >= 0 else -int(-ticks + Fraction(1, 2))

    encoder_last_pts = int((microsecond_pts(final["pts"])
                            - (0 if preserve_source_pts else microsecond_pts(origin))) / speed)
    if kind != "png":
        try:
            tail = Fraction(final.get("duration_time", final.get("pkt_duration_time", "")))
        except (ValueError, TypeError, ZeroDivisionError) as error:
            raise ExportError("Video timing verification requires an explicit final source-frame duration.") from error
        if tail <= 0:
            raise ExportError("Video timing verification requires a positive final source-frame duration.")
        expected_duration = expected_times[-1] + tail / speed
        if preserve_source_pts and (tail * 1000).denominator != 1:
            raise ExportError("Duplicate-PTS FFV1 requires a final duration exactly representable in Matroska milliseconds.")
        intervals = [b - a for a, b in zip(expected_times, expected_times[1:])
                     if not preserve_source_pts or b != a]
        if any(gap < Fraction(1, 1000) for gap in intervals) and kind == "ffv1":
            raise ExportError("Scaled source timestamps are closer than Matroska's 1 ms precision; choose a slower speed.")
        if any(gap < Fraction(3, 1000000) for gap in intervals):
            raise ExportError("Scaled source timestamps exceed the microsecond encoder precision; choose a slower speed.")
    timing_graph = "setpts=PTS-STARTPTS" if kind == "png" else (
        f"settb=expr=1/1000000,setpts=(PTS-STARTPTS)*{speed.denominator}/{speed.numerator}"
    )
    if preserve_source_pts:
        timing_graph = "settb=expr=1/1000000"
    conversion = "format=bgr24" if matrix == "rgb" else (
        f"scale=in_color_matrix={matrix}:out_color_matrix={matrix}:"
        f"in_range={range_name}:out_range=full:flags=bicubic,format=bgr24"
    )
    if color_choices is not None or (effective_color is not None
                                     and effective_color["chroma_location"] == "center"):
        conversion = ",".join(f"{name}={options}"
                              for name, options in conversion_stages(effective_color))
    graph = f"trim=start_frame={first}:end_frame={last + 1},{timing_graph},{conversion}"
    graph += "," + enhancement_filter(
        settings["brightness"], settings["contrast"], settings["sharpness"]
    )
    if any(settings[key] for key in CROP_KEYS):
        graph += "," + crop.filter
    if settings["rotation"]:
        graph += "," + rotation_filter(settings["rotation"])
    if resize_filter:
        graph += "," + resize_filter
    plan = ExportPlan(
        str(Path(source).absolute()), str(path), kind, first, last, frame_count,
        output_width, output_height, matrix, range_name,
        effective_color["color_primaries"] if color_choices else metadata[0]["color_primaries"],
        effective_color["color_transfer"] if color_choices else metadata[0]["color_transfer"],
        time_base, fps, settings, graph,
        width, height, playback_speed, expected_times, expected_duration, encoder_last_pts,
        zoom_level, interpolation, offset, source_range is not None,
        () if source_range is None else (
            f"{float(source_range.start_seconds):.6f}", f"{float(source_range.end_seconds):.6f}",
            len(metadata),
        ),
        color_choices, audit, preserve_source_pts, duplicate_groups, count_discrepancy,
    )
    plan.check_destination(check_outputs=not allow_existing_output)
    return plan
