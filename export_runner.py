# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Cancellable FFmpeg execution and independent per-frame export audit."""

from contextlib import AbstractContextManager, ExitStack
from datetime import datetime
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import tempfile

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from crop_utils import ROTATION_LABELS, rotation_filter
from ffmpeg_export import ExportCancelled, ExportError, ExportPlan, powershell_command
from ffmpeg_tools import resolve_arguments, tool_path
from frame_processor import FrameProcessor
from processing_filters import PIPELINE_ID
from version import __version__, git_commit


def check_cancelled(cancelled) -> None:
    if cancelled():
        raise ExportCancelled("Cancelled; partial output is not verified. No processing log was created.")


def rotation_report(rotation: int) -> dict:
    return {
        "clockwise_degrees": rotation,
        "ffmpeg_filter": rotation_filter(rotation) or None,
        "description": (
            f"{ROTATION_LABELS[rotation]}; exact 90-degree pixel rotation after crop and "
            "before export scaling, no interpolation; independently verified per frame"
            if rotation else "0° (Original); not rotated"
        ),
    }


def file_sha256(path: Path, cancelled) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check_cancelled(cancelled)
            digest.update(block)
    return digest.hexdigest()


class FFmpegProcess(AbstractContextManager):
    """Spool errors without pipe deadlock; watchdog interrupts blocking reads."""

    def __init__(self, arguments: list[str], cancelled):
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.error_stream = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                resolve_arguments(arguments), stdout=subprocess.PIPE, stderr=self.error_stream,
                startupinfo=self._startupinfo(),
            )
        except OSError:
            self.error_stream.close()
            raise
        self.watch_thread = threading.Thread(target=self._watch, daemon=True)
        self.watch_thread.start()

    @staticmethod
    def _startupinfo():
        from frame_info import FrameInfoHandler
        return FrameInfoHandler._startupinfo()

    def _watch(self):
        while not self.stop.wait(0.05):
            if self.cancelled() and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                return

    def read_exact(self, size: int) -> bytes:
        chunks = []
        while size:
            check_cancelled(self.cancelled)
            block = self.process.stdout.read(size)
            if not block:
                self.finish()
                raise ExportError("FFmpeg ended before a complete selected frame.")
            chunks.append(block)
            size -= len(block)
        return b"".join(chunks)

    def finish(self):
        self.process.wait(timeout=30)
        check_cancelled(self.cancelled)
        if self.process.returncode:
            self.error_stream.seek(0, 2)
            length = self.error_stream.tell()
            self.error_stream.seek(max(0, length - 65536))
            raise ExportError("FFmpeg failed:\n" + self.error_stream.read().decode(errors="replace"))

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.watch_thread.join(timeout=3)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdout.close()
        self.error_stream.close()
        return False


def _verification_summary(report: dict) -> list[str]:
    comparison = report["comparison"]
    frames = comparison["frames_compared"]
    if report["status"] == "verified_lossless":
        result = (f"Result: VERIFIED lossless - all {frames} output frames are pixel-identical "
                  "to OSVEV's processed frames and to the independent FFmpeg filter output")
    else:
        result = (f"Result: processing verified for all {frames} frames; LOSSY output compared, "
                  f"not pixel-identical ({comparison['output_mismatch_frames']} frames differ, "
                  f"max channel difference {comparison['output_max_channel_difference']})")
    lines = [result]
    timing = report.get("timing")
    if timing:
        lines.append(
            f"Playback timing: {'verified' if timing['verified'] else 'NOT verified'} - "
            f"{timing.get('frames_verified', frames)} frame timestamps, duration "
            f"{timing['output_duration_seconds']:.6f} s (expected {timing['expected_duration_seconds']:.6f} s)"
        )
    lines.append(f"Original source unchanged: {report['source_unchanged']} (SHA-256 rechecked after export)")
    return lines


def processing_log(report: dict) -> str:
    """Render the verified audit without decoding or hashing again.

    Per-frame hashes, verification statistics, hash commands and library diagnostics stay
    in the audit report, saved only when detailed diagnostics are requested.
    """
    selection = report["selection"]
    lines = [
        "OSVEV verified export processing log",
        f"OSVEV version: {report['osvev_version']} (commit {report['osvev_commit']})",
        f"Created: {report['created_at']}",
        f"Verification status: {report['status']}",
        *_verification_summary(report),
        f"Encoding: {report['encoding']}",
        "",
        f"Source: {report['source']}",
        f"Source SHA-256: {report['source_sha256_before']}",
        f"Output: {report['output']}",
        "Output file SHA-256:",
    ]
    for output in report["outputs"]:
        lines += [f"  {output['sha256']}  {output['path']}"]
    lines += [
        "",
        f"Source dimensions: {report['source_dimensions']}",
        f"Output dimensions: {report['dimensions']}",
        f"Frames (1-based, inclusive): {selection['first_index'] + 1}-{selection['last_index_inclusive'] + 1}",
        f"Selected frames: {selection['count']} / {selection['source_frame_count']}",
    ]
    if "loaded_range" in selection:
        loaded = selection["loaded_range"]
        lines.append(
            f"Loaded time range: source PTS {loaded['start_seconds']}-{loaded['end_seconds']} s "
            f"(source frames {loaded['first_index'] + 1}-{loaded['last_index_inclusive'] + 1}; "
            "frame numbers refer to the unchanged original file)"
        )
    if report.get("source_frame_count_discrepancy") is not None:
        discrepancy = report["source_frame_count_discrepancy"]
        lines += [
            "",
            "Source frame-count discrepancy:",
            f"  Container-declared frames: {discrepancy['declared_count']}",
            f"  Demuxed packets: {discrepancy['packet_count']}",
            f"  Decoded/presented frames: {discrepancy['decoded_count']}",
            "  Frame numbers above refer to decoded presentation order, not container sample indices.",
            *("  " + explanation for explanation in discrepancy["assumptions"]),
            "  Verification covers the selected decoded frames, not proof of recording completeness.",
        ]
    lines += [
        f"Pipeline: {report['processing_pipeline']}",
        "Order: brightness/contrast (eq) -> sharpening (unsharp) -> crop -> rotation -> export scaling",
        "Processing settings:",
    ]
    lines += [f"  {key}: {value}" for key, value in report["settings"].items()]
    lines += [
        f"Crop (pixel edges): {report['crop']}",
        f"Rotation: {report['rotation']['description']}",
        f"Color decoding: matrix {report['matrix']}; range {report['source_range']}",
        f"Source chroma location (reported): {report.get('source_chroma_location')}",
        ("Export speed: not applicable (PNG has no intrinsic timing)"
         if report["output_stream"]["codec"] == "png" else
         f"Export speed: {report['export_playback_speed']:g}x"),
        f"Export zoom: {report['export_zoom_level']}x",
        f"Export interpolation: {report['export_interpolation']} (FFmpeg scaling; "
        "not claimed pixel-identical to either viewer's display)",
        "Excluded: audio, JPEG compression simulation, pixel highlighting, Magnified View, Additional Zoom, overlays",
    ]
    if report.get("color_interpretation") is not None:
        audit = report["color_interpretation"]
        lines += ["", "Source color interpretation:", audit["source_color_intent"],
                  "Original native frame/stream declarations: "
                  + json.dumps(audit["original_native_and_stream_metadata"], sort_keys=True)]
        for field, value in audit["interpretation"].items():
            lines.append(
                f"  {field}: reported frame values "
                f"{audit['original_frame_metadata'][field]}; effective {value['effective']} "
                f"({value['provenance']})"
            )
        lines += ["  Policy: " + json.dumps(audit["policy"], sort_keys=True)]
    if not report["encoding_command_executed_by_osvev"]:
        lines += [
            "Command executed by OSVEV: False (existing output verified only)",
            report["external_output_provenance"],
        ]
    if report.get("duplicate_pts"):
        lines += ["", "Duplicate source presentation timestamps:",
                  "Frames sharing PTS have no independently established display interval.",
                  "Frame identity is source ordinal plus timestamp occurrence, never PTS alone.",
                  report["timestamp_policy"]]
        for group in report["duplicate_pts"]:
            lines.append(f"  PTS {group['pts']} at time base {report['source_time_base']}: "
                         f"source frames (1-based) {group['source_frames']}")
        lines.append("Selected source timestamps (original integer PTS, unchanged):")
        lines += [f"  Source frame {entry['source_frame']}: PTS {entry['pts']}; "
                  f"occurrence {entry['occurrence']}"
                  for entry in report["original_timestamps"]]
    lines += [
        "",
        "Complete reproducible command (PowerShell):",
        report["powershell_command"],
        "",
        "FFmpeg version:",
        "\n".join(report["ffmpeg_version"].splitlines()).rstrip(),
        "",
        (f"Detailed diagnostics: {report['detailed_report']} and {report['frame_records']}"
         if report["detailed_diagnostics"] else
         "Detailed diagnostics (per-frame hashes, verification statistics, hash commands, "
         "library versions): not saved; enable them in the export dialog to keep them."),
    ]
    return "\n".join(lines) + "\n"


def execute_export(plan: ExportPlan, metadata: list[dict], cancelled=lambda: False,
                   progress=lambda percent, message: None, *, verify_only=False,
                   detailed_diagnostics=False) -> dict:
    """Encode once, then compare FFmpeg filter/output bytes with the existing processor."""
    plan.check_destination(verify_only=verify_only)
    check_cancelled(cancelled)
    report = {
        "schema": "osvev-export-1", "status": "running",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": plan.source, "output": plan.output_pattern,
        "selection": {"first_index": plan.first, "last_index_inclusive": plan.last,
                      "count": plan.count, "source_frame_count": plan.frame_count},
        "source_frame_count_discrepancy": plan.count_discrepancy,
        "settings": plan.settings, "decoder": "pyav", "matrix": plan.matrix,
        "source_range": plan.range_name, "source_time_base": plan.time_base,
        "color_interpretation": plan.color_audit,
        "source_chroma_location": metadata[0].get("chroma_location"),
        "source_average_fps": plan.fps,
        "native_pixel_format": metadata[0]["pix_fmt"],
        "native_hash_format": None,
        "hash_format": "SHA-256; tightly packed row-major uint8 B,G,R; width/height and source PTS recorded separately",
        "dimensions": [plan.width, plan.height],
        "source_dimensions": [plan.source_width, plan.source_height],
        "crop": {key: value for key, value in plan.settings.items() if key.startswith("crop_")},
        "rotation": rotation_report(plan.settings.get("rotation", 0)),
        "filter_contract": "exact brightness/contrast/sharpening/crop/rotation/export scaling; verified for every selected frame",
        "processing_pipeline": PIPELINE_ID,
        "encoding": "lossless RGB PNG/FFV1" if plan.lossless else "LOSSY H.264 CRF18 YUV420 limited BT.709",
        "command_arguments": plan.command(), "powershell_command": powershell_command(plan.command()),
        "reference_hash_command": powershell_command(plan.reference_command(hashes=True)),
        "output_hash_command": powershell_command(plan.output_decode_command(hashes=True)),
        "audio": "excluded",
        "display_highlights_magnifier_additional_zoom_overlays_jpeg": "excluded",
        "export_zoom_level": plan.zoom_level, "export_interpolation": plan.interpolation,
        "scaling_contract": "Export-only FFmpeg scaling after crop. Method names match the viewer; "
                            "OpenCV/Qt display interpolation kernels and rounding can differ. "
                            "Scaled exports are not claimed pixel-identical to either viewer.",
        "export_playback_speed": plan.playback_speed,
        "encoding_command_executed_by_osvev": not verify_only,
        "external_output_provenance": (
            "Not independently known; verification tests existing pixels, not how they were produced."
            if verify_only else "Executed displayed argument list without a shell."
        ),
        "frame_records": str(plan.records_path) if detailed_diagnostics else None,
        "detailed_report": str(plan.report_path) if detailed_diagnostics else None,
        "detailed_diagnostics": detailed_diagnostics, "processing_log": str(plan.log_path),
        "osvev_version": __version__, "osvev_commit": git_commit(),
        "outputs": [],
        "timestamp_policy": "All selected frames retained; source PTS rebased and divided by export speed. "
                            "Packet durations scaled without changing PTS/DTS. Matroska quantizes to milliseconds. PNG has no intrinsic timing.",
    }
    if plan.duplicate_groups:
        report["duplicate_pts"] = [{"pts": pts, "source_frames": list(indices)}
                                   for pts, indices in plan.duplicate_groups]
        occurrences = {}
        original_timestamps = []
        for index, entry in enumerate(metadata, plan.source_offset):
            pts = entry["pts"]
            occurrences[pts] = occurrences.get(pts, 0) + 1
            if plan.first <= index <= plan.last:
                original_timestamps.append({"source_frame": index + 1, "pts": pts,
                                            "occurrence": occurrences[pts]})
        report["original_timestamps"] = original_timestamps
    if plan.preserve_source_pts:
        report["timestamp_policy"] = (
            "All selected frames and original source presentation times retained exactly; "
            "no rebasing, replacement PTS or speed change. Output ticks may use a different "
            "time base; integer PTS and source time base are recorded here. "
            "Equal timestamps have no independently established display interval."
        )
    elif plan.kind == "png" and plan.duplicate_groups:
        report["timestamp_policy"] = (
            "PNG has no intrinsic timing. All selected frames are retained in source order; "
            "their original integer source PTS, time base and timestamp occurrences "
            "are preserved in this processing log, not embedded as PNG playback timing."
        )
    if plan.partial_index:
        start_seconds, end_seconds, loaded_count = plan.loaded_range
        report["selection"]["loaded_range"] = {
            "start_seconds": start_seconds, "end_seconds": end_seconds,
            "first_index": plan.source_offset,
            "last_index_inclusive": plan.source_offset + loaded_count - 1,
        }
    with ExitStack() as audit_stack:
        if detailed_diagnostics:
            report_path, records_path = plan.report_path, plan.records_path
        else:
            temporary = Path(audit_stack.enter_context(tempfile.TemporaryDirectory(prefix="osvev-audit-")))
            report_path, records_path = temporary / "audit.json", temporary / "frames.jsonl"
        audit = audit_stack.enter_context(report_path.open("x", encoding="utf-8"))
        try:
            progress(0, "Hashing the original source...")
            report["source_sha256_before"] = file_sha256(Path(plan.source), cancelled)
            with FFmpegProcess(["ffmpeg", "-version"], cancelled) as version_process:
                version = version_process.process.stdout.read().decode(errors="replace")
                version_process.finish()
            report["ffmpeg_version"] = version
            report["ffmpeg_executable"] = tool_path("ffmpeg")
            import av
            report["libraries"] = {"pyav": av.__version__, "pyav_libraries": av.library_versions,
                                   "opencv": cv2.__version__, "numpy": np.__version__}
            if not verify_only:
                progress(5, "Executing the displayed FFmpeg command...")
                with FFmpegProcess(plan.command(), cancelled) as process:
                    for line in process.process.stdout:
                        check_cancelled(cancelled)
                        text = line.decode(errors="replace").strip()
                        if text.startswith("frame="):
                            try:
                                frame = int(text.split("=", 1)[1])
                            except ValueError as error:
                                raise ExportError(f"Invalid FFmpeg progress record: {text}") from error
                            progress(5 + min(40, round(40 * frame / plan.count)),
                                     f"Encoding {min(frame, plan.count)}/{plan.count} frames...")
                    process.finish()
            progress(45, "Comparing independent FFmpeg and OSVEV processed pixels...")
            from pyav_input import PyAVInputCapture
            capture = PyAVInputCapture(plan.source, color_choices=plan.color_choices)
            try:
                ok, first_frame = capture.read()
                if not ok:
                    raise ExportError("Source could not be decoded for verification.")
                capture.configure_index(metadata, partial=plan.partial_index)
                if capture.chroma_location == "center" and not plan.partial_index:
                    ok, first_frame = capture.read()
                    if not ok:
                        raise ExportError("Source first indexed frame could not be decoded.")
                report["native_hash_format"] = capture.native_samples.hash_format
                if plan.first != plan.source_offset:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, plan.first - plan.source_offset)
                size = plan.width * plan.height * 3
                with av.open(str(plan.output_path(0))) as output_header:
                    if len(output_header.streams) != 1 or len(output_header.streams.video) != 1:
                        raise ExportError("Verified exports must contain exactly one video stream and no audio.")
                    stream = output_header.streams.video[0]
                    expected_codec = {"png": "png", "ffv1": "ffv1", "h264": "h264"}[plan.kind]
                    if (
                        stream.codec_context.name != expected_codec
                        or stream.codec_context.width != plan.width
                        or stream.codec_context.height != plan.height
                    ):
                        raise ExportError("Output codec or dimensions do not match the selected format.")
                    if plan.kind == "h264" and (
                        stream.codec_context.format.name != "yuv420p"
                        or stream.codec_context.colorspace != 1
                        or stream.codec_context.color_range != 1
                    ):
                        raise ExportError("Compatibility MP4 requires tagged limited-range BT.709 YUV420.")
                    report["output_stream"] = {
                        "codec": stream.codec_context.name,
                        "pixel_format": stream.codec_context.format.name,
                        "time_base": str(stream.time_base),
                    }
                filter_mismatches = output_mismatches = 0
                processor = FrameProcessor()
                maximum_difference = different_samples = 0
                max_timestamp_error = Fraction(0)
                max_interval_error = Fraction(0)
                previous_output_time = None
                timing_mismatches = 0
                with ExitStack() as output_stack, \
                        records_path.open("x", encoding="utf-8") as records, \
                        FFmpegProcess(plan.reference_command(), cancelled) as reference, \
                        FFmpegProcess(plan.output_decode_command(), cancelled) as decoded:
                    if plan.kind != "png":
                        output_container = output_stack.enter_context(av.open(plan.destination))
                        output_stream = output_container.streams.video[0]
                        output_stream.codec_context.thread_count = 1
                        output_frames = output_container.decode(output_stream)
                        output_time_base = Fraction(output_stream.time_base)
                        precision_limit = Fraction(1, 1000 if plan.kind == "ffv1" else 1000000)
                        if not 0 < output_time_base <= precision_limit:
                            raise ExportError(
                                "Output time base is too coarse for verified export timing: "
                                f"{output_stream.time_base}; required tick <= {precision_limit} seconds."
                            )
                        timing_tolerance = output_time_base + Fraction(3, 1000000)
                        if plan.preserve_source_pts:
                            timing_tolerance = Fraction(0)
                        duration_tolerance = 2 * timing_tolerance
                        if output_container.duration is None:
                            raise ExportError("Output has no measurable container duration.")
                        measured_duration = Fraction(output_container.duration, av.time_base)
                        duration_error = abs(measured_duration - plan.expected_duration)
                        report["timing"] = {
                            "verified": False,
                            "speed": plan.playback_speed,
                            "expected_duration_seconds": float(plan.expected_duration),
                            "output_duration_seconds": float(measured_duration),
                            "duration_error_seconds": float(duration_error),
                            "pts_tolerance_seconds": float(timing_tolerance),
                            "duration_tolerance_seconds": float(duration_tolerance),
                            "basis": "Source integer PTS/time base; explicit final source-frame duration.",
                            "container_time_base": str(output_stream.time_base),
                        }
                    for ordinal in range(plan.count):
                        check_cancelled(cancelled)
                        if ordinal == 0 and plan.first == 0 and not plan.partial_index:
                            frame = first_frame
                        else:
                            ok, frame = capture.read()
                            if not ok:
                                raise ExportError("Source ended before the selected frame range.")
                        processed = processor.process(frame, **plan.settings)
                        intended = processor.scale_for_export(
                            processed, plan.zoom_level, plan.interpolation
                        ).tobytes()
                        filtered = reference.read_exact(size)
                        output = decoded.read_exact(size)
                        same_filter, same_output = intended == filtered, intended == output
                        filter_mismatches += not same_filter
                        output_mismatches += not same_output
                        if not same_output:
                            delta = np.abs(np.frombuffer(intended, np.uint8).astype(np.int16)
                                           - np.frombuffer(output, np.uint8).astype(np.int16))
                            maximum_difference = max(maximum_difference, int(delta.max()))
                            different_samples += int(np.count_nonzero(delta))
                        native = capture.native_samples
                        record = {
                            "output_ordinal": ordinal + 1, "source_index": plan.first + ordinal,
                            "source_pts": native.frame.pts, "source_time_base": str(native.frame.time_base),
                            ("native_rgb_sha256" if native.matrix == "rgb" else "native_yuv_sha256"):
                                hashlib.sha256(native.packed_bytes()).hexdigest(),
                            "intended_bgr_sha256": hashlib.sha256(intended).hexdigest(),
                            "ffmpeg_filter_bgr_sha256": hashlib.sha256(filtered).hexdigest(),
                            "output_bgr_sha256": hashlib.sha256(output).hexdigest(),
                            "filter_matches": same_filter, "output_matches": same_output,
                        }
                        if plan.kind != "png":
                            output_frame = next(output_frames, None)
                            if output_frame is None:
                                raise ExportError("Output decoder ended before the selected range.")
                            record["output_pts"] = output_frame.pts
                            record["output_time_base"] = str(output_frame.time_base)
                            if output_frame.pts is None or output_frame.time_base is None:
                                raise ExportError("Output frame has no presentation timestamp.")
                            actual_time = output_frame.pts * Fraction(output_frame.time_base)
                            expected_time = plan.expected_times[ordinal]
                            error = abs(actual_time - expected_time)
                            max_timestamp_error = max(max_timestamp_error, error)
                            if previous_output_time is not None:
                                if (actual_time < previous_output_time
                                        or (actual_time == previous_output_time
                                            and not plan.preserve_source_pts)):
                                    raise ExportError("Output presentation timestamps are not strictly increasing.")
                                interval_error = abs(
                                    actual_time - previous_output_time
                                    - (expected_time - plan.expected_times[ordinal - 1])
                                )
                                max_interval_error = max(max_interval_error, interval_error)
                            previous_output_time = actual_time
                            timing_mismatches += error > timing_tolerance
                            record["expected_output_time_seconds"] = float(expected_time)
                            record["output_time_seconds"] = float(actual_time)
                            record["timestamp_error_seconds"] = float(error)
                        else:
                            record["output_pts"] = None
                            record["output_time_base"] = None
                        records.write(json.dumps(record) + "\n")
                        progress(45 + round(45 * (ordinal + 1) / plan.count),
                                 f"Verifying {ordinal + 1}/{plan.count} frames...")
                    if reference.process.stdout.read(1) or decoded.process.stdout.read(1):
                        raise ExportError("Reference or output contains additional frames.")
                    if plan.kind != "png" and next(output_frames, None) is not None:
                        raise ExportError("Output contains additional presentation frames.")
                    reference.finish()
                    decoded.finish()
                    if plan.kind != "png":
                        report["timing"].update({
                            "frames_verified": plan.count,
                            "timestamp_mismatch_frames": timing_mismatches,
                            "max_timestamp_error_seconds": float(max_timestamp_error),
                            "max_interval_error_seconds": float(max_interval_error),
                        })
                        if timing_mismatches or duration_error > duration_tolerance:
                            raise ExportError("Output playback timing/duration does not match the selected speed.")
                        report["timing"]["verified"] = True
            finally:
                capture.release()
            report["comparison"] = {
                "frames_compared": plan.count, "filter_mismatch_frames": filter_mismatches,
                "output_mismatch_frames": output_mismatches,
                "output_max_channel_difference": maximum_difference,
                "output_different_channel_samples": different_samples,
            }
            progress(90, "Hashing outputs and rechecking source preservation...")
            outputs = [plan.output_path(index) for index in range(plan.count)] if plan.sequence else [Path(plan.destination)]
            for path in outputs:
                report["outputs"].append({"path": str(path), "sha256": file_sha256(path, cancelled)})
            report["source_sha256_after"] = file_sha256(Path(plan.source), cancelled)
            report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
            report["frame_records_sha256"] = file_sha256(records_path, cancelled)
            if not report["source_unchanged"]:
                raise ExportError("The source file changed during export.")
            if filter_mismatches:
                raise ExportError("FFmpeg processing does not match OSVEV for the selected frames.")
            if plan.lossless and output_mismatches:
                raise ExportError("Lossless output does not preserve the intended processed BGR pixels.")
            report["status"] = "verified_lossless" if plan.lossless else "verified_lossy_comparison"
            check_cancelled(cancelled)
        except ExportCancelled as error:
            report["status"], report["error"] = "cancelled_not_verified", str(error)
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            report["status"], report["error"] = "failed_not_verified", str(error)
            raise
        finally:
            audit.write(json.dumps(report, indent=2))
    text = processing_log(report)
    log = plan.log_path.open("x", encoding="utf-8")
    try:
        with log:
            log.write(text)
    except OSError:
        plan.log_path.unlink()
        raise
    progress(100, "Lossless pixels verified." if plan.lossless else "Lossy output comparison recorded; no pixel-identity claim.")
    return report


class ExportWorker(QThread):
    progressChanged = pyqtSignal(int, str)

    def __init__(self, plan: ExportPlan, metadata: list[dict], parent=None, *, verify_only=False,
                 detailed_diagnostics=False):
        super().__init__(parent)
        self.plan, self.metadata = plan, metadata
        self.verify_only = verify_only
        self.detailed_diagnostics = detailed_diagnostics
        self.report = None
        self.error = None
        self.cancelled = False

    def run(self):
        try:
            self.report = execute_export(
                self.plan, self.metadata, self.isInterruptionRequested,
                self.progressChanged.emit,
                verify_only=self.verify_only,
                detailed_diagnostics=self.detailed_diagnostics,
            )
        except ExportCancelled:
            self.cancelled = True
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.error = str(error)
