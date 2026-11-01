# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Read-only hashes and decoder comparison for a small source-frame sample."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from frame_processor import FrameProcessor


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_digest(frame):
    """Hash row-major BGR bytes; dimensions and dtype are recorded separately."""
    return hashlib.sha256(frame.tobytes()).hexdigest()


def inspect_fidelity(path, frame_limit=8):
    if not 1 <= frame_limit <= 32:
        raise ValueError("Choose between 1 and 32 frames for the bounded comparison.")
    path = Path(path).resolve(strict=True)
    before = file_digest(path)
    capture = cv2.VideoCapture(str(path))
    frames = []
    try:
        if not capture.isOpened():
            raise ValueError(f"OpenCV could not open {path}")
        backend = capture.getBackendName()
        for _ in range(frame_limit):
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    if not frames:
        raise ValueError("OpenCV returned no video frames.")

    version = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True, check=True,
    ).stdout.splitlines()[0]
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt,width,height,color_range,color_space,"
        "color_transfer,color_primaries,avg_frame_rate",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    reference = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-frames:v", str(len(frames)), "-fps_mode", "passthrough",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
    ], capture_output=True, check=True)
    if len(reference.stdout) != sum(frame.nbytes for frame in frames):
        raise ValueError("FFmpeg returned a different frame count or frame size.")

    records = []
    offset = 0
    for index, frame in enumerate(frames):
        decoded = np.frombuffer(
            reference.stdout, dtype=np.uint8, count=frame.size, offset=offset
        ).reshape(frame.shape)
        offset += frame.nbytes
        processed = FrameProcessor.process_frame(frame)
        difference = np.abs(frame.astype(np.int16) - decoded.astype(np.int16))
        records.append({
            "frame_index": index, "shape": list(frame.shape), "dtype": str(frame.dtype),
            "opencv_bgr_sha256": frame_digest(frame),
            "neutral_processed_bgr_sha256": frame_digest(processed),
            "ffmpeg_bgr_sha256": frame_digest(decoded),
            "differing_channel_samples": int(np.count_nonzero(difference)),
            "maximum_channel_difference": int(difference.max()),
        })
    after = file_digest(path)
    return {
        "source": str(path),
        "source_sha256_before": before, "source_sha256_after": after,
        "opencv_version": cv2.__version__, "opencv_backend": backend,
        "numpy_version": np.__version__, "ffmpeg_version": version,
        "source_stream": json.loads(probe.stdout),
        "scope": "Sampled 8-bit BGR bytes, not native-YUV or whole-video equivalence.",
        "source_file_unchanged": before == after,
        "neutral_processing_pixel_identical": all(
            row["opencv_bgr_sha256"] == row["neutral_processed_bgr_sha256"]
            for row in records
        ),
        "opencv_ffmpeg_pixel_identical": all(
            row["opencv_bgr_sha256"] == row["ffmpeg_bgr_sha256"]
            for row in records
        ),
        "frames": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--frames", type=int, default=8, help="Sample 1-32 frames (default: 8)")
    args = parser.parse_args()
    try:
        report = inspect_fidelity(args.video, args.frames)
    except (OSError, ValueError, cv2.error, subprocess.SubprocessError) as error:
        print(f"Fidelity check failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if (
        report["source_file_unchanged"] and report["neutral_processing_pixel_identical"]
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
