# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Opt-in, separately decoded and independently audited video-only derivatives."""

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import av
import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from color_interpretation import (
    ColorChoices, FIELDS, UNSPECIFIED, conversion_stages, native_reported, resolve_reported,
)
from export_runner import FFmpegProcess, check_cancelled, file_sha256
from ffmpeg_export import ExportCancelled, ExportError, powershell_command
from ffmpeg_tools import tool_path
from frame_info import FrameInfoHandler
from playback_timing import PlaybackTimeline
from pyav_input import PyAVInputCapture, reject_hdr_side_data


class WorkingCopyError(RuntimeError):
    """A derivative cannot meet the conversion contract."""


@dataclass(frozen=True)
class WorkingSource:
    path: str
    sha256: str
    stream: dict
    streams: list[dict]
    frames: list[dict]
    time_base: Fraction
    times: tuple[Fraction, ...]
    durations: tuple[Fraction, ...]
    derived_timing: bool
    missing_color: tuple[str, ...]
    declared_count: int | None = None
    count_audit: dict | None = None
    # When set, frames/times/durations are in output (PTS) order and order[i]
    # is the 0-based decoder-output position of output frame i.
    order: tuple[int, ...] | None = None
    reorder_audit: dict | None = None

    @property
    def reordered(self):
        return self.order is not None

    def decode_index(self, index):
        return index if self.order is None else self.order[index]

    @property
    def decode_positions(self):
        """Output position of each decoder-output frame, in decoder order."""
        if self.order is None:
            return range(len(self.frames))
        positions = [0] * len(self.order)
        for output, decoded in enumerate(self.order):
            positions[decoded] = output
        return positions

    @property
    def count_mismatch(self):
        return self.declared_count is not None and self.declared_count != len(self.frames)

    @property
    def count_label(self):
        return "AVI" if self.count_audit is None or self.count_audit["avi"] else "The container"

    @property
    def needs_rounding(self):
        return any((value * 1000).denominator != 1
                   for values in (self.times, self.durations) for value in values)


def millisecond(value):
    """FFmpeg's nearest rounding, with positive half ties away from zero."""
    scaled = value * 1000 + Fraction(1, 2)
    return Fraction(scaled.numerator // scaled.denominator, 1000)


MINIMUM_DURATION = Fraction(1, 1000)


def raised_duration(duration):
    """A positive duration below 0.5 ms is stored as 1 ms (deviation < 1 ms), never as zero."""
    return duration > 0 and millisecond(duration) == 0


def precision_plan(source):
    times = tuple(millisecond(value) for value in source.times)
    for index, (stamp, duration) in enumerate(zip(times, source.durations)):
        if duration <= 0 or (raised_duration(duration) and MINIMUM_DURATION - duration > MINIMUM_DURATION):
            raise WorkingCopyError("A source duration cannot be represented within the one-millisecond policy.")
        if index and source.times[index] > source.times[index - 1] and stamp <= times[index - 1]:
            raise WorkingCopyError("Millisecond rounding would create a new timestamp collision; conversion refused.")
    return times


def default_duration_bounds(output_rate):
    """Millisecond packet durations FFmpeg's Matroska muxer elides for this DefaultDuration.

    Mirrors matroskaenc: packets equal to the truncated or ceiled DefaultDuration
    get no BlockDuration, so the demuxer reports DefaultDuration for both.
    """
    if not output_rate or output_rate <= 0:
        return set()
    default = 1000 / Fraction(output_rate)
    low = default.numerator // default.denominator
    return {Fraction(value, 1000) for value in {low, low + (default.denominator != 1)} if value > 0}


def audit_precision(source, metadata, base, endpoint, rounding, cancelled=lambda: False, output_rate=None):
    """Audit rational times before publishing; tolerances never apply to pixels."""
    if len(metadata) != len(source.frames):
        raise WorkingCopyError("Converted frame count differs from the complete source index.")
    expected = precision_plan(source) if rounding else source.times
    elided = default_duration_bounds(output_rate) if rounding else set()
    default_reported = []
    max_pts = max_duration = max_interval = Fraction(0)
    changed_pts = changed_durations = source_gaps = 0
    previous = None
    for index, record in enumerate(metadata):
        check_cancelled(cancelled)
        pts = record.get("pts")
        if type(pts) is not int:
            raise WorkingCopyError("Converted frame lacks integer PTS.")
        stamp = pts * base
        duration = frame_duration(record, base)
        error = abs(stamp - source.times[index])
        duration_error = abs(duration - source.durations[index])
        # Matroska may report DefaultDuration truncated to stream ticks, or the
        # encoder's rounded packet duration. Neither is an inferred frame interval.
        allowed_durations = {source.durations[index]}
        if rounding:
            ticks = source.durations[index] * 1000
            allowed_durations = {
                millisecond(source.durations[index]),
                Fraction(ticks.numerator // ticks.denominator, 1000),
            } - {Fraction(0)}
            if raised_duration(source.durations[index]):
                allowed_durations = {MINIMUM_DURATION}
            elif millisecond(source.durations[index]) in elided:
                allowed_durations |= elided
        if (stamp != expected[index] or duration not in allowed_durations
                or error > (Fraction(1, 2000) if rounding else 0)
                or duration_error > (Fraction(1, 1000) if rounding else 0)):
            raise WorkingCopyError(f"Frame {index + 1}: original/authorized timing or duration changed beyond policy.")
        if rounding and duration != millisecond(source.durations[index]) and duration in elided:
            default_reported.append({
                "frame": index + 1, "original_duration_seconds": str(source.durations[index]),
                "output_duration_seconds": str(duration),
                "deviation_seconds": str(duration - source.durations[index])})
        if previous is not None:
            interval_error = abs((stamp - previous) - (source.times[index] - source.times[index - 1]))
            max_interval = max(max_interval, interval_error)
            if interval_error > (Fraction(1, 1000) if rounding else 0):
                raise WorkingCopyError("Unexplained output timestamp discontinuity.")
            source_gaps += source.times[index] != source.times[index - 1] + source.durations[index - 1]
        previous = stamp
        changed_pts += error != 0
        changed_durations += duration_error != 0
        max_pts, max_duration = max(max_pts, error), max(max_duration, duration_error)
    source_end = source.times[-1] + source.durations[-1]
    expected_endpoints = {expected[-1] + duration for duration in allowed_durations}
    drift = endpoint - source_end
    if endpoint not in expected_endpoints or abs(drift) > (Fraction(1, 1000) if rounding else 0):
        raise WorkingCopyError("Converted container end time does not match the bounded timing policy.")
    span_drift = (endpoint - expected[0]) - (source_end - source.times[0])
    if abs(span_drift) > (Fraction(1, 1000) if rounding else 0):
        raise WorkingCopyError("Overall presentation span drift exceeds one millisecond.")
    return {
        "authorized_millisecond_rounding": rounding,
        "rounding": "Each absolute PTS independently: nearest millisecond, positive half ties upward; no interval accumulation."
                    if rounding else "None: exact rational times and durations required.",
        "duration_policy": "Positive reported durations must equal millisecond floor or nearest; a positive duration "
                           "that would round to zero is stored as 1 ms (deviation below 1 ms); never inferred from "
                           "FPS or adjacent PTS." if rounding else "Exact source durations.",
        "durations_raised_to_one_millisecond": [
            {"frame": index + 1, "original_duration_seconds": str(duration), "output_duration_seconds": "1/1000",
             "deviation_seconds": str(MINIMUM_DURATION - duration)}
            for index, duration in enumerate(source.durations) if rounding and raised_duration(duration)],
        "matroska_default_duration": {
            "output_frame_rate": str(output_rate) if output_rate else None,
            "elided_millisecond_durations_seconds": sorted(str(value) for value in elided),
            "explanation": "FFmpeg's Matroska muxer writes no BlockDuration for packets equal to the truncated or "
                           "ceiled DefaultDuration; such frames read back as DefaultDuration. Accepted only within "
                           "the 1 ms duration limit; PTS are unaffected.",
            "frames_reported_as_default_duration": default_reported,
        } if rounding else None,
        "limits_seconds": {"pts": "1/2000" if rounding else "0",
                           "duration_interval_endpoint_span": "1/1000" if rounding else "0"},
        "max_pts_deviation_seconds": str(max_pts),
        "max_duration_deviation_seconds": str(max_duration),
        "max_interval_deviation_seconds": str(max_interval),
        "changed_pts_count": changed_pts, "changed_duration_count": changed_durations,
        "source_endpoint_seconds": str(source_end), "output_endpoint_seconds": str(endpoint),
        "container_final_duration_seconds": str(endpoint - expected[-1]),
        "container_final_duration_deviation_seconds": str(endpoint - expected[-1] - source.durations[-1]),
        "endpoint_drift_seconds": str(drift), "presentation_span_drift_seconds": str(span_drift),
        "source_gap_or_overlap_count": source_gaps,
        "endpoint_policy": "Rounded last PTS plus permitted floor/nearest final duration; packet/container tail may differ from DefaultDuration."
                           if rounding else "Exact final PTS plus source duration.",
        "notice": "Working-copy timing is quantized, NOT original precision. Source gaps/overlaps are not repaired."
                  if rounding else "Selected source timing precision preserved; provenance is recorded separately.",
    }


def scan_source(path, cancelled, progress):
    arguments = [
        tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_frames", "-show_streams", "-of", "json", str(path),
    ]
    text = FrameInfoHandler._scan_with_progress(
        arguments, cancelled, lambda stage, count, total: progress(0, f"{stage}: {count} frames"), 0,
    )
    data = json.loads(text)
    header = json.loads(FrameInfoHandler._scan_probe(
        [tool_path("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(path)], cancelled))
    if (not isinstance(data, dict) or not isinstance(header, dict)
            or not isinstance(data.get("frames"), list)
            or not all(isinstance(record, dict) for record in data["frames"])
            or not isinstance(data.get("streams"), list)
            or not all(isinstance(record, dict) for record in data["streams"])
            or not isinstance(header.get("streams"), list)
            or not all(isinstance(record, dict) for record in header["streams"])):
        raise WorkingCopyError("FFprobe returned an invalid decoded-frame/stream index.")
    return data, header["streams"]


def frame_duration(record, base):
    ticks = record.get("duration", record.get("pkt_duration"))
    try:
        duration = (ticks * base if type(ticks) is int else Fraction(
            record.get("duration_time", record.get("pkt_duration_time", ""))))
    except (ValueError, TypeError, ZeroDivisionError) as error:
        raise WorkingCopyError("Every source frame requires an explicit positive duration.") from error
    if duration <= 0:
        raise WorkingCopyError("Every source frame requires an explicit positive duration.")
    return duration


def reported_color(frame, stream):
    result = {}
    for field in FIELDS:
        value, header = frame.get(field), stream.get(field)
        if value not in UNSPECIFIED and header not in UNSPECIFIED and value != header:
            raise WorkingCopyError(f"Conflicting stream/frame {field}; conversion refused.")
        result[field] = value if value not in UNSPECIFIED else header
    return result


def prepare_source(path, cancelled=lambda: False, progress=lambda percent, text: None, *,
                   allow_precision_rounding=False):
    path = Path(path).absolute()
    progress(0, "Hashing the original (it will not be modified)...")
    before = file_sha256(path, cancelled)
    data, streams = scan_source(path, cancelled, progress)
    if len(data.get("streams", [])) != 1 or not data.get("frames"):
        raise WorkingCopyError("A decodable first video stream and complete frame metadata are required.")
    stream, frames = data["streams"][0], data["frames"]
    declared_count = stream.get("nb_frames")
    declared_count = None if declared_count in (None, "N/A") else int(declared_count)
    mismatch = declared_count is not None and declared_count != len(frames)
    if stream.get("codec_name") not in ("mpeg4", "h264", "hevc", "ffv1"):
        raise WorkingCopyError("Working copies currently support MPEG-4 Part 2, H.264, HEVC and YUV FFV1 only.")
    if stream.get("disposition", {}).get("attached_pic"):
        raise WorkingCopyError("Attached-picture streams are not supported.")
    FrameInfoHandler.validate_display_transforms(stream)
    reject_hdr_side_data(stream.get("side_data_list", []))
    try:
        base = Fraction(stream["time_base"])
        rate = Fraction(stream["avg_frame_rate"])
    except (KeyError, ValueError, ZeroDivisionError) as error:
        raise WorkingCopyError("Explicit stream time base and average rate are required; no FPS is assumed.") from error
    if base <= 0 or rate <= 0:
        raise WorkingCopyError("Explicit positive stream time base and average rate are required.")
    derived = any(type(frame.get("pts")) is not int for frame in frames)
    times, durations, missing = [], [], set()
    shape = (frames[0].get("width"), frames[0].get("height"), frames[0].get("pix_fmt"))
    for index, frame in enumerate(frames):
        check_cancelled(cancelled)
        FrameInfoHandler.validate_display_transforms(frame)
        reject_hdr_side_data(frame.get("side_data_list", []))
        width, height, pixel_format = (frame.get("width"), frame.get("height"), frame.get("pix_fmt"))
        if ((width, height, pixel_format) != shape or type(width) is not int
                or type(height) is not int or width <= 0 or height <= 0
                or width % 2 or height % 2 or pixel_format not in ("yuv420p", "yuvj420p")
                or frame.get("interlaced_frame") != 0):
            raise WorkingCopyError("Only constant, even-sized, progressive 8-bit YUV420 SDR sources are supported.")
        pts = frame.get("best_effort_timestamp") if derived else frame.get("pts")
        if type(pts) is not int:
            raise WorkingCopyError("Missing original PTS require complete integer FFmpeg best-effort timestamps; no FPS reconstruction is available.")
        if derived and frame.get("pts") is not None and frame["pts"] != pts:
            raise WorkingCopyError("Best-effort timing conflicts with existing original PTS.")
        stamp = pts * base
        duration = frame_duration(frame, base)
        if stamp < 0:
            raise WorkingCopyError("Negative or decreasing presentation times cannot be preserved by this feature.")
        if (not allow_precision_rounding
                and ((stamp * 1000).denominator != 1 or (duration * 1000).denominator != 1)):
            raise WorkingCopyError(
                "Source timestamps/durations are not exactly representable in Matroska milliseconds. "
                "Conversion refused rather than rounding timing.")
        times.append(stamp)
        durations.append(duration)
        reported = reported_color(frame, stream)
        missing.update(field for field in FIELDS if reported[field] in UNSPECIFIED)
        # Validate explicit properties even before asking permission for missing ones.
        resolve_reported(reported, ColorChoices())
        if pixel_format == "yuvj420p" and reported["color_range"] not in (*UNSPECIFIED, "pc"):
            raise WorkingCopyError("YUVJ pixel format contradicts the reported range.")
        if index and reported != reported_color(frames[0], stream):
            raise WorkingCopyError("Changing source color declarations are not supported.")
    order = reorder_audit = None
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        if derived:
            raise WorkingCopyError("Negative or decreasing presentation times cannot be preserved by this feature.")
        order, reorder_audit = plan_pts_reordering(frames, times, durations)
        frames = [frames[index] for index in order]
        times = [times[index] for index in order]
        durations = [durations[index] for index in order]
    count_audit = (audit_count_discrepancy(path, frames, declared_count, cancelled)
                   if mismatch else None)
    source = WorkingSource(str(path), before, stream, streams, frames, base,
                           tuple(times), tuple(durations), derived, tuple(sorted(missing)),
                           declared_count, count_audit, order, reorder_audit)
    if source.needs_rounding:
        precision_plan(source)
    if mismatch or source.reordered:
        corroborate_recovered_frames(source, cancelled, progress)
        if file_sha256(path, cancelled) != before:
            raise WorkingCopyError("The original changed during recovered-frame preflight.")
    return source


COUNT_LIMITATION = "Verification covers every recovered frame, not proof that the original recording is complete."

# H.264/HEVC decoded picture buffers hold at most 16 frames; larger displacement is not local.
REORDER_LIMIT = 16


def plan_pts_reordering(frames, times, durations):
    """Plan presentation of decoded frames by their unique original PTS; never alter a timestamp.

    Only local, unambiguous reversals qualify: every frame has a unique integer PTS,
    no frame moves more than REORDER_LIMIT positions, and the displaced frames
    (complement of the longest increasing run in decoder order) are themselves in
    increasing PTS order.
    """
    refusal = "Decreasing presentation times cannot be reordered reliably: "
    if len(set(times)) != len(times):
        raise WorkingCopyError(refusal + "duplicate presentation times make the frame order ambiguous.")
    order = tuple(sorted(range(len(times)), key=times.__getitem__))
    displacement = max(abs(output - decoded) for output, decoded in enumerate(order))
    if displacement > REORDER_LIMIT:
        raise WorkingCopyError(
            refusal + f"a frame would move {displacement} positions (limit {REORDER_LIMIT}); "
            "the reversal is not local.")
    # Deterministic longest strictly increasing subsequence (patience sorting).
    tails, links = [], [None] * len(times)
    for index, stamp in enumerate(times):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if times[tails[middle]] < stamp:
                low = middle + 1
            else:
                high = middle
        links[index] = tails[low - 1] if low else None
        tails[low:low + 1] = [index]
    kept, cursor = set(), tails[-1]
    while cursor is not None:
        kept.add(cursor)
        cursor = links[cursor]
    moved = [index for index in range(len(times)) if index not in kept]
    if any(times[later] <= times[earlier] for earlier, later in zip(moved, moved[1:])):
        raise WorkingCopyError(refusal + "displaced frames are not themselves in presentation order.")
    positions = {decoded: output for output, decoded in enumerate(order)}
    audit = {
        "method": "Stable presentation order by unique original container PTS; decoder output order otherwise retained.",
        "decoded_count": len(times),
        "reversal_count": sum(later < earlier for earlier, later in zip(times, times[1:])),
        "reversals": [
            {"source_frames": [index + 1, index + 2],
             "pts": [frames[index]["pts"], frames[index + 1]["pts"]],
             "times_seconds": [str(times[index]), str(times[index + 1])]}
            for index in range(len(times) - 1) if times[index + 1] < times[index]],
        "moved_decode_indices": moved,
        "moved_frames": [
            {"source_frame": index + 1, "output_frame": positions[index] + 1,
             "pts": frames[index]["pts"], "time_seconds": str(times[index]),
             "duration_seconds": str(durations[index])}
            for index in moved],
        "repositioned_frame_count": sum(output != decoded for output, decoded in enumerate(order)),
        "max_displacement_frames": displacement, "displacement_limit_frames": REORDER_LIMIT,
        "assumptions": [
            "Each frame's original container PTS is authoritative for its presentation position; "
            "decoder output order disagrees locally.",
            "Pixels, PTS and durations of every decoded frame are kept exactly; no frame is added, "
            "duplicated, dropped or retimed.",
            "FFmpeg CLI best-effort timestamps are replaced by each frame's original PTS after "
            "decoder-order runs are concatenated in PTS order, then independently verified against "
            "PyAV native PTS.",
        ],
    }
    return order, audit


def audit_count_discrepancy(path, frames, declared, cancelled):
    """Explain a declared/decoded count gap from demuxed packets; never repair or infer frames.

    AVI keeps its established recovered-frame policy. Other containers must have a
    self-consistent packet index whose undecoded packets are fully explained by
    demuxer discard flags (e.g. MP4/MOV edit lists) or by leading packets that
    precede the first keyframe and therefore have no decodable reference.
    """
    packets = []
    with av.open(str(path), options={"fflags": "-genpts"}) as container:
        format_name = container.format.name
        for packet in container.demux(container.streams.video[0]):
            check_cancelled(cancelled)
            if packet.size:
                packets.append((packet.pts, packet.is_discard, packet.is_keyframe, packet.is_corrupt))
    audit = {
        "container_format": format_name, "avi": format_name == "avi",
        "declared_count": declared, "packet_count": len(packets),
        "decoded_count": len(frames), "decoded_minus_declared": len(frames) - declared,
        "decoded_minus_packets": len(frames) - len(packets),
    }
    if audit["avi"]:
        audit["cause"] = "Not established automatically; header/empty-slot semantics or missing data remain possible."
        audit["assumptions"] = []
        return audit
    refusal = "Conversion refused: reliable verification of the recovered sequence is not possible. "
    if any(corrupt or type(pts) is not int for pts, _, _, corrupt in packets):
        raise WorkingCopyError(refusal + "Packets are corrupt or lack integer timestamps (unreliable packet index).")
    if len(packets) != declared:
        raise WorkingCopyError(
            refusal + f"The container declares {declared} frames but {len(packets)} packets were demuxed "
            f"and {len(frames)} frames decoded; the container index itself is inconsistent.")
    packet_pts = [pts for pts, _, _, _ in packets]
    decoded_pts = [frame.get("pts") for frame in frames]
    if len(set(packet_pts)) != len(packet_pts):
        raise WorkingCopyError(refusal + "Duplicate packet timestamps prevent frame-to-packet correspondence.")
    if (any(type(pts) is not int for pts in decoded_pts) or len(set(decoded_pts)) != len(decoded_pts)
            or not set(decoded_pts) <= set(packet_pts)):
        raise WorkingCopyError(
            refusal + "Decoded frames lack original PTS or do not correspond one-to-one with demuxed packets.")
    decoded = set(decoded_pts)
    keyframes = [index for index, (_, _, key, _) in enumerate(packets) if key]
    first_key = keyframes[0] if keyframes else len(packets)
    discarded, leading = [], []
    for index, (pts, discard, _, _) in enumerate(packets):
        if pts in decoded:
            if discard:
                raise WorkingCopyError(refusal + "A discard-flagged packet was presented by the decoder.")
        elif discard:
            discarded.append(pts)
        elif index < first_key:
            leading.append(pts)
        else:
            raise WorkingCopyError(
                refusal + f"Packet PTS {pts} (decode position {index + 1}) after the first keyframe was neither "
                "decoded nor discard-flagged by the demuxer; the missing frame is unexplained.")
    assumptions = []
    if discarded:
        assumptions.append(
            f"{len(discarded)} packet(s) flagged discard by the demuxer (e.g. MP4/MOV edit list) are "
            "intentionally not presented by FFmpeg/PyAV and are excluded, not reconstructed.")
    if leading:
        assumptions.append(
            f"{len(leading)} leading packet(s) precede the first keyframe and have no decodable reference; "
            "FFmpeg/PyAV output no frame for them and their content is not represented.")
    assumptions.append("The declared count reflects stored samples/packets, not presented frames.")
    audit.update({
        "discard_flagged_undecoded_count": len(discarded),
        "discard_flagged_undecoded_pts": discarded,
        "pre_keyframe_undecoded_count": len(leading),
        "pre_keyframe_undecoded_pts": leading,
        "first_keyframe_decode_position": first_key + 1 if keyframes else None,
        "cause": "Every undecoded packet is accounted for by demuxer discard flags or by leading "
                 "packets preceding the first keyframe; no other packet is missing.",
        "assumptions": assumptions,
    })
    return audit


def corroborate_recovered_frames(source, cancelled, progress):
    """Verify a recovered or reordered sequence, never infer its recording completeness."""
    arguments = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
        "-noautorotate", "-threads", "1", "-err_detect", "explode",
        "-i", source.path, "-map", "0:v:0", "-an", "-sn", "-dn",
        # This decoder-order pixel stream carries no timing evidence; PTS are checked
        # natively below. Sequential numbering keeps reversed best-effort stamps muxable.
        *(["-vf", "setpts=N"] if source.reordered else []),
        "-fps_mode", "passthrough", "-threads", "1",
        "-f", "rawvideo", "-pix_fmt", source.frames[0]["pix_fmt"], "pipe:1",
    ]
    with av.open(source.path, options={"fflags": "-genpts"}) as container, \
            FFmpegProcess(arguments, cancelled) as reference:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        stream.codec_context.options = {"err_detect": "explode"}
        decoded = container.decode(stream)
        for index, position in enumerate(source.decode_positions):
            check_cancelled(cancelled)
            record = source.frames[position]
            frame = next(decoded, None)
            if (frame is None or frame.is_corrupt or frame.width != record["width"]
                    or frame.height != record["height"] or frame.format.name != record["pix_fmt"]
                    or frame.interlaced_frame or frame.time_base != source.time_base
                    or (record.get("pts") is not None and frame.pts != record["pts"])
                    or (frame.pts is not None
                        and frame.pts * source.time_base != source.times[position])):
                raise WorkingCopyError("Independent decoding cannot establish a consistent recovered frame sequence.")
            pixels = frame.to_ndarray().tobytes()
            if pixels != reference.read_exact(frame.width * frame.height * 3 // 2):
                raise WorkingCopyError("Independent native pixels/order disagree for recovered frames.")
            progress(0, f"Corroborating recovered frame {index + 1}/{len(source.frames)}...")
        if next(decoded, None) is not None or reference.process.stdout.read(1):
            raise WorkingCopyError("Independent recovered frame counts disagree.")
        finish_recovered_decode(reference)


def finish_recovered_decode(process):
    process.finish()
    process.error_stream.seek(0, 2)
    length = process.error_stream.tell()
    if length:
        process.error_stream.seek(max(0, length - 65536))
        diagnostics = process.error_stream.read(65536)
        raise WorkingCopyError(
            "Recovered-frame conversion has unexplained FFmpeg error diagnostics; conversion refused:\n"
            + diagnostics.decode(errors="replace"))


def interpretation(source, choices):
    effective = resolve_reported(reported_color(source.frames[0], source.stream), choices)
    if source.frames[0]["pix_fmt"] == "yuvj420p" and effective["color_range"] != "pc":
        raise WorkingCopyError("YUVJ requires full-range interpretation; conflicting assumptions refused.")
    return effective


def reordering_graph(source):
    """Concatenate decoder-order runs in PTS order, then restore each original PTS.

    concat keeps the shared stream frame rate (needed for Matroska durations) but
    rewrites PTS in microseconds, and the FFmpeg CLI stamps reversed frames with
    best-effort DTS. Anchored frames receive their probed PTS; every other frame keeps
    its exact decoded PTS spacing from the preceding output frame. settb then returns
    to the source time base, which round-trips exactly because its tick exceeds 1 us.
    """
    if source.time_base <= Fraction(1, 1_000_000):
        raise WorkingCopyError("PTS reordering requires a source time base coarser than one microsecond.")
    order = source.order
    runs = []
    for position, index in enumerate(order):
        if runs and index == runs[-1][1] + 1:
            runs[-1][1] = index
        else:
            runs.append([index, index, position])
    anchors = {start for _, _, start in runs}
    for position, index in enumerate(order):
        record = source.frames[position]
        if record.get("best_effort_timestamp") != record["pts"]:
            anchors.update({position, position + 1})
    expression = "PREV_OUTPTS+PTS-PREV_INPTS"
    for position in sorted((p for p in anchors if p < len(order)), reverse=True):
        exact = source.frames[position]["pts"] * source.time_base * 1_000_000
        rounded = int(exact + Fraction(1, 2)) if exact >= 0 else -int(-exact + Fraction(1, 2))
        expression = f"if(eq(N,{position}),{rounded},{expression})"
    segments = [
        f"[run{number}]trim=start_frame={first}:end_frame={last + 1}[segment{number}]"
        for number, (first, last, _) in enumerate(runs)
    ]
    return (f"split={len(runs)}" + "".join(f"[run{number}]" for number in range(len(runs))) + ";"
            + ";".join(segments) + ";"
            + "".join(f"[segment{number}]" for number in range(len(runs)))
            + f"concat=n={len(runs)}:v=1:a=0,setpts='{expression}',"
            f"settb={source.time_base.numerator}/{source.time_base.denominator}")


def conversion_command(source, output, effective):
    graph = ",".join(f"{name}={options}" for name, options in conversion_stages(effective))
    if not source.reordered:
        video = ["-noautorotate", "-threads", "1", "-err_detect", "explode", "-copyts",
                 "-i", source.path, "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                 "-vf", graph]
    else:
        video = ["-noautorotate", "-threads", "1", "-err_detect", "explode", "-copyts",
                 "-i", source.path, "-map", "[video]", "-an", "-sn", "-dn", "-filter_threads", "1",
                 "-filter_complex", f"[0:v:0]{reordering_graph(source)},{graph}[video]"]
    raised = [millisecond(stamp) for stamp, duration in zip(source.times, source.durations)
              if raised_duration(duration)]
    # FFmpeg rescales these frames' durations to 0 ms; mark only those packets 1 ms.
    durations = (["-bsf:v", "setts=duration='if(" + "+".join(
        f"eq(PTS,{int(stamp * 1000)})" for stamp in raised) + ",1,DURATION)'"] if raised else [])
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
        *video, "-fps_mode", "passthrough", "-enc_time_base", "1:1000",
        "-threads", "1", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", *durations,
        "-map_metadata", "-1", "-n", "-progress", "pipe:1", "-nostats", str(output),
    ]


def destination_paths(source, destination):
    destination = Path(destination).absolute()
    log = Path(str(destination) + ".conversion.jsonl")
    if destination.suffix.lower() != ".mkv" or not destination.parent.is_dir():
        raise WorkingCopyError("Choose a new .mkv file in an existing folder.")
    if os.path.normcase(str(destination)) == os.path.normcase(source.path):
        raise WorkingCopyError("The working copy cannot replace the original.")
    if destination.exists() or log.exists():
        raise WorkingCopyError("Existing media or conversion logs are never overwritten.")
    return destination, log


def create_working_copy(source, destination, *, choices=None, authorize_derived=False,
                        authorize_count_mismatch=False,
                        authorize_precision_rounding=False,
                        authorize_pts_reordering=False,
                        cancelled=lambda: False, progress=lambda percent, text: None):
    check_cancelled(cancelled)
    if source.reordered and not authorize_pts_reordering:
        raise WorkingCopyError(
            f"Explicit authorization is required: {source.reorder_audit['reversal_count']} presentation-time "
            "reversal(s) require presenting decoded frames in original PTS order instead of decoder output order.")
    if source.needs_rounding and not authorize_precision_rounding:
        raise WorkingCopyError("Explicit authorization is required for bounded millisecond rounding timing changes.")
    rounding = source.needs_rounding and authorize_precision_rounding
    if rounding:
        precision_plan(source)
    if source.count_mismatch and not authorize_count_mismatch:
        packets = "" if source.count_audit is None else f" in {source.count_audit['packet_count']} packets"
        raise WorkingCopyError(
            f"Explicit authorization is required: {source.count_label} declares {source.declared_count} frames{packets}, "
            f"but {len(source.frames)} were independently recovered. This does not prove recording completeness.")
    if source.derived_timing and not authorize_derived:
        raise WorkingCopyError("Explicit authorization is required for derived best-effort timing, not original timestamps.")
    effective = interpretation(source, choices)
    destination, log_path = destination_paths(source, destination)
    # Deliberately conservative scratch-space check, not a prediction of compression.
    frame_bytes = source.frames[0]["width"] * source.frames[0]["height"] * 4
    if shutil.disk_usage(destination.parent).free < frame_bytes * 4 + 16 * 1024 * 1024:
        raise WorkingCopyError("Insufficient free space for conversion buffers and verification.")
    if file_sha256(Path(source.path), cancelled) != source.sha256:
        raise WorkingCopyError("The original changed after preflight; conversion refused.")
    with tempfile.TemporaryDirectory(prefix=".osvev-working-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "working.mkv"
        staged_log = Path(temporary) / "conversion.jsonl"
        command = conversion_command(source, staged, effective)
        progress(0, "Converting video only; audio and other streams are excluded...")
        with FFmpegProcess(command, cancelled) as process:
            for line in process.process.stdout:
                check_cancelled(cancelled)
                if line.startswith(b"frame="):
                    count = int(line.split(b"=", 1)[1])
                    progress(min(45, round(45 * count / len(source.frames))),
                             f"Converting {count}/{len(source.frames)} frames...")
            if source.count_mismatch or source.reordered:
                finish_recovered_decode(process)
            else:
                process.finish()
        progress(45, "Validating the complete working-copy index...")
        metadata = FrameInfoHandler.get_frame_metadata(str(staged), cancelled)
        if len(metadata) != len(source.frames):
            raise WorkingCopyError("Converted frame count differs from the independently scanned source.")
        with FFmpegProcess(["ffmpeg", "-version"], cancelled) as version_process:
            version = version_process.process.stdout.read().decode(errors="replace")
            version_process.finish()
        published_command = conversion_command(source, destination, effective)
        header = {
            "schema": "osvev-working-copy-1", "status": "verified",
            "created_at": datetime.now().astimezone().isoformat(),
            "source": source.path, "output": str(destination),
            "source_sha256": source.sha256, "output_sha256": file_sha256(staged, cancelled),
            "source_sha256_after": source.sha256,
            "ffmpeg_version": version, "ffmpeg_executable": tool_path("ffmpeg"),
            "independent_decoder": {"pyav": av.__version__, "libraries": av.library_versions},
            "command": powershell_command(published_command),
            "executed_command": powershell_command(command),
            "source_streams": source.streams,
            "color": {"reported": reported_color(source.frames[0], source.stream),
                      "effective": effective, "assumed_fields": source.missing_color,
                      "provenance": {
                          field: ("reported" if field not in source.missing_color else
                                  "user-selected" if field in FIELDS[:2] else
                                  "assumed left" if field == "chroma_location" else
                                  "assumed SDR matching effective matrix")
                          for field in FIELDS},
                      "choices": None if choices is None else choices.defaults(),
                      "notice": "Missing fields are authorized assumptions, not verified source color intent."
                                if source.missing_color else "Explicit supported source declarations."},
            "timing_policy": ("User-authorized bounded millisecond quantization; output timing is NOT original precision."
                              if rounding else
                              "User-authorized FFmpeg best-effort timing: reconstructed/derived, not original PTS."
                              if source.derived_timing else "Original absolute presentation times, no rebasing or rate change."),
            "derived_timing_authorized": source.derived_timing and authorize_derived,
            "precision_rounding_authorized": rounding,
            "timing_provenance": "FFprobe decoded-frame best_effort_timestamp only where original PTS are absent; "
                                 "all existing original PTS must agree. Independent native decoding and "
                                 "FFmpeg BGR conversion corroborate frame order. PyAV generation of PTS "
                                 "is explicitly disabled in this isolated verifier; numeric native PTS must agree.",
            "source_time_base": str(source.time_base), "frame_count": len(source.frames),
            "pixel_contract": "Exact BGR24 under the recorded interpretation, not native YUV preservation.",
            "omitted_streams": [s for s in source.streams if s["index"] != source.stream["index"]],
            "notice": "Video-only converted working copy. Audio, subtitles, data and other video streams excluded.",
            "discrepancies": [], "source_unchanged": True,
        }
        audit = source.count_audit or {}
        header["frame_count_policy"] = {
            "original_declared_count": source.declared_count,
            "packet_count": audit.get("packet_count"),
            "decoded_recovered_count": len(source.frames),
            "mismatch_authorized": source.count_mismatch and authorize_count_mismatch,
            "limitation": COUNT_LIMITATION,
        }
        if source.count_mismatch:
            header["frame_count_policy"]["audit"] = audit
            header["discrepancies"].append({
                "kind": ("AVI declared/recovered frame count mismatch" if audit.get("avi", True)
                         else "Container declared/packet/decoded frame count mismatch"),
                "container_format": audit.get("container_format"),
                "declared": source.declared_count, "packets": audit.get("packet_count"),
                "recovered": len(source.frames),
                "recovered_minus_declared": len(source.frames) - source.declared_count,
                "cause": audit.get("cause"),
                "assumptions": audit.get("assumptions", []),
                "frames_added_or_removed_to_match_declared_count": 0,
                "timestamps_normalized": False,
                "verification": "Strict independent FFmpeg/PyAV native sequence corroborated in preflight; "
                                "all recovered frames, converted pixels, timing and durations verified before publication.",
                "authorization": "Explicit user permission to convert recovered frames despite count discrepancy.",
            })
        if rounding:
            header["discrepancies"].append({
                "kind": "Explicitly authorized millisecond timing quantization",
                "notice": "All original rational times/durations and signed output deviations are recorded per frame.",
            })
        if source.reordered:
            header["frame_order_policy"] = {
                "pts_reordering_authorized": authorize_pts_reordering, **source.reorder_audit,
            }
            header["discrepancies"].append({
                "kind": "Local presentation-timestamp reversals; frames presented in original PTS order",
                "reversals": source.reorder_audit["reversals"],
                "moved_frames": source.reorder_audit["moved_frames"],
                "assumptions": source.reorder_audit["assumptions"],
                "frames_added_duplicated_or_removed": 0, "timestamps_normalized": False,
                "verification": "Strict independent FFmpeg/PyAV native sequence corroborated in decoder order; "
                                "every output frame's pixels, PTS and duration verified against its mapped source "
                                "frame (per-frame source_frame/output_frame records).",
                "authorization": "Explicit user permission to present decoded frames in original PTS order.",
            })
        _verify(source, staged, metadata, effective, command, staged_log, header, cancelled, progress)
        check_cancelled(cancelled)
        destination_paths(source, destination)
        progress(90, "Publishing verified media and conversion log (additional disk space required)...")
        # Exclusive creation prevents overwriting a file created after the initial check.
        published = []
        try:
            for original, target in ((staged, destination), (staged_log, log_path)):
                with target.open("xb") as sink:
                    published.append(target)
                    with original.open("rb") as reader:
                        while block := reader.read(1024 * 1024):
                            check_cancelled(cancelled)
                            sink.write(block)
            check_cancelled(cancelled)
        except (OSError, ExportCancelled):
            for target in published:
                target.unlink()
            raise
    progress(100, "Verified video-only working copy and conversion log created.")
    return str(destination)


def _verify(source, staged, metadata, effective, command, staged_log, header, cancelled, progress):
    reference = command[:command.index("-enc_time_base")] + [
        "-threads", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    width, height = source.frames[0]["width"], source.frames[0]["height"]
    graph = None
    with ExitStack() as stack:
        output = PyAVInputCapture(str(staged))
        stack.callback(output.release)
        if not output.read()[0]:
            raise WorkingCopyError("The completed copy has no decodable first frame.")
        output.configure_index(metadata, cancelled=cancelled)
        PlaybackTimeline.from_metadata(metadata, len(metadata), output.get(cv2.CAP_PROP_FPS),
                                       time_base=output.native_samples.frame.time_base)
        output.set(cv2.CAP_PROP_POS_FRAMES, 0)
        container = stack.enter_context(av.open(source.path, options={"fflags": "-genpts"}))
        with av.open(str(staged)) as completed:
            if len(completed.streams) != 1 or len(completed.streams.video) != 1:
                raise WorkingCopyError("The working copy must contain exactly one video stream and no audio.")
            if completed.duration is None:
                raise WorkingCopyError("Converted container has no independently established endpoint.")
            header["precision_audit"] = audit_precision(
                source, metadata, Fraction(completed.streams.video[0].time_base),
                Fraction(completed.duration, av.time_base), header["precision_rounding_authorized"], cancelled,
                output_rate=completed.streams.video[0].average_rate)
            header["output_stream"] = {
                "codec": completed.streams.video[0].codec_context.name,
                "pixel_format": completed.streams.video[0].codec_context.format.name,
                "time_base": str(completed.streams.video[0].time_base),
                "container_end_seconds": str(Fraction(completed.duration, av.time_base)),
            }
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        stream.codec_context.options = {"err_detect": "explode"}
        decoded = container.decode(stream)
        reference_process = stack.enter_context(FFmpegProcess(reference, cancelled))
        log = stack.enter_context(staged_log.open("x", encoding="utf-8"))
        # The header is only published if every record and final checks succeed.
        log.write(json.dumps(header) + "\n")
        pending, decoded_count = {}, 0
        for index, record in enumerate(source.frames):
            check_cancelled(cancelled)
            wanted = source.decode_index(index)
            while wanted not in pending and len(pending) <= REORDER_LIMIT:
                frame = next(decoded, None)
                if frame is None:
                    break
                pending[decoded_count] = frame
                decoded_count += 1
            frame = pending.pop(wanted, None)
            if (frame is None or frame.is_corrupt or frame.width != width or frame.height != height
                    or frame.format.name != record["pix_fmt"] or frame.interlaced_frame
                    or frame.time_base != source.time_base):
                raise WorkingCopyError("Independent decoder disagrees with source frame format/count/time base.")
            FrameInfoHandler.validate_display_transforms(record)
            reject_hdr_side_data([{"side_data_type": side.type.name} for side in frame.side_data])
            declared = native_reported(frame, stream.codec_context)
            for field, value in declared.items():
                if value not in UNSPECIFIED and value != effective[field]:
                    raise WorkingCopyError("Native decoder and probed color declarations disagree.")
            native_time = None if frame.pts is None else frame.pts * source.time_base
            if record.get("pts") is not None and frame.pts != record["pts"]:
                raise WorkingCopyError("Independent native decoder disagrees with original integer PTS.")
            missing_native = (native_time is None and source.derived_timing and record.get("pts") is None)
            if not missing_native and native_time != source.times[index]:
                raise WorkingCopyError("Independent PyAV presentation order/timing does not corroborate the chosen FFmpeg timeline.")
            if graph is None:
                graph = av.filter.Graph()
                graph.threads = 1
                graph.link_nodes(graph.add_buffer(template=frame),
                                 *(graph.add(name, options) for name, options in conversion_stages(effective)),
                                 graph.add("buffersink"))
                graph.configure()
            graph.push(frame)
            pixels = graph.pull().to_ndarray(format="bgr24").tobytes()
            independent = reference_process.read_exact(width * height * 3)
            ok, converted = output.read()
            if not ok or converted.tobytes() != pixels or independent != pixels:
                raise WorkingCopyError(f"Frame {index + 1}: independent BGR pixels/order do not match.")
            native = output.native_samples.frame
            stamp = native.pts * Fraction(native.time_base)
            duration = frame_duration(metadata[index], Fraction(native.time_base))
            if (stamp != metadata[index]["pts"] * Fraction(native.time_base)
                    or native.duration * Fraction(native.time_base) != duration
                    or (not header["precision_rounding_authorized"]
                        and (stamp != source.times[index] or duration != source.durations[index]))):
                raise WorkingCopyError(f"Frame {index + 1}: original/authorized timing or duration changed; copy refused.")
            log.write(json.dumps({
                "frame": index + 1, "source_frame": source.decode_index(index) + 1,
                "output_frame": index + 1, "original_pts": record.get("pts"),
                "best_effort_timestamp": record.get("best_effort_timestamp"),
                "reported_packet_dts": record.get("pkt_dts"),
                "independent_native_pts": frame.pts,
                "reported_frame_color": {field: record.get(field) for field in FIELDS},
                "source_time_base": str(source.time_base),
                "selected_time_seconds": str(source.times[index]),
                "duration_seconds": str(source.durations[index]),
                "output_pts": native.pts, "output_time_base": str(native.time_base),
                "output_time_seconds": str(stamp),
                "output_duration_seconds": str(duration),
                "pts_deviation_seconds": str(stamp - source.times[index]),
                "duration_deviation_seconds": str(duration - source.durations[index]),
                "source_interval_seconds": None if not index else str(source.times[index] - source.times[index - 1]),
                "output_interval_seconds": None if not index else str(
                    stamp - metadata[index - 1]["pts"] * Fraction(native.time_base)),
                "source_gap_after_previous_seconds": None if not index else str(
                    source.times[index] - source.times[index - 1] - source.durations[index - 1]),
                "native_source_sha256": hashlib.sha256(frame.to_ndarray().tobytes()).hexdigest(),
                "bgr24_sha256": hashlib.sha256(pixels).hexdigest(),
                "independent_bgr24_sha256": hashlib.sha256(independent).hexdigest(),
                "output_bgr24_sha256": hashlib.sha256(converted.tobytes()).hexdigest(),
            }) + "\n")
            progress(45 + round(45 * (index + 1) / len(source.frames)),
                     f"Verifying {index + 1}/{len(source.frames)} frames...")
        if (pending or next(decoded, None) is not None or output.read()[0]
                or reference_process.process.stdout.read(1)):
            raise WorkingCopyError("Unexpected additional source or converted frames.")
        if source.count_mismatch or source.reordered:
            finish_recovered_decode(reference_process)
        else:
            reference_process.finish()
        progress(90, "Rechecking original-file preservation...")
        if file_sha256(Path(source.path), cancelled) != source.sha256:
            raise WorkingCopyError("The original file changed during conversion.")


class WorkingCopyWorker(QThread):
    progress = pyqtSignal(int, str)

    def __init__(self, source, parent=None, *, destination=None, choices=None, authorize_derived=False,
                 authorize_count_mismatch=False, authorize_precision_rounding=False,
                 authorize_pts_reordering=False):
        super().__init__(parent)
        self.source, self.destination = source, destination
        self.choices, self.authorize_derived = choices, authorize_derived
        self.authorize_count_mismatch = authorize_count_mismatch
        self.authorize_precision_rounding = authorize_precision_rounding
        self.authorize_pts_reordering = authorize_pts_reordering
        self.result = None
        self.error = None
        self.cancelled = False

    def run(self):
        from frame_info import MetadataCancelled
        try:
            if self.destination is None:
                self.result = prepare_source(self.source, self.isInterruptionRequested, self.progress.emit,
                                             allow_precision_rounding=True)
            else:
                self.result = create_working_copy(
                    self.source, self.destination, choices=self.choices,
                    authorize_derived=self.authorize_derived,
                    authorize_count_mismatch=self.authorize_count_mismatch,
                    authorize_precision_rounding=self.authorize_precision_rounding,
                    authorize_pts_reordering=self.authorize_pts_reordering,
                    cancelled=self.isInterruptionRequested, progress=self.progress.emit)
        except (ExportCancelled, MetadataCancelled):
            self.cancelled = True
        except (OSError, ValueError, RuntimeError, av.FFmpegError) as error:
            self.error = str(error)
