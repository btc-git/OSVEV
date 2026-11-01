# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Persistent single-frame enhancement processing, with no media cache."""

from fractions import Fraction

import cv2
import numpy as np

from processing_filters import enhancement_stages, validate_processing_settings
from crop_utils import crop_rectangle, rotate_image, validate_rotation
from export_scaling import scaling_stages


class FrameProcessor:
    """Private enhancement graph; export verification also owns a scaling graph."""

    def __init__(self):
        self._graph = None
        self._key = None
        self._sequence = 0
        self._scaling_graph = None
        self._scaling_key = None

    def close(self):
        self._graph = None
        self._key = None
        self._scaling_graph = None
        self._scaling_key = None

    def scale_for_export(self, frame, zoom_level=1, interpolation="Nearest"):
        """Verify export scaling with libavfilter, not the display-only OpenCV resize."""
        stages = scaling_stages(frame.shape[1], frame.shape[0], zoom_level, interpolation)
        if not stages:
            return frame
        import av
        incoming = av.VideoFrame.from_ndarray(frame, format="bgr24")
        incoming.time_base = Fraction(1, 1000)
        incoming.pts = self._sequence
        self._sequence += 1
        key = (frame.shape, zoom_level, interpolation)
        try:
            if self._scaling_graph is None or self._scaling_key != key:
                graph = av.filter.Graph()
                graph.threads = 1
                graph.link_nodes(
                    graph.add_buffer(template=incoming),
                    *(graph.add(name, options) for name, options in stages),
                    graph.add("buffersink"),
                )
                graph.configure()
                self._scaling_graph, self._scaling_key = graph, key
            self._scaling_graph.push(incoming)
            return np.ascontiguousarray(self._scaling_graph.pull().to_ndarray(format="bgr24"))
        except (av.FFmpegError, ValueError) as error:
            self._scaling_graph = self._scaling_key = None
            raise RuntimeError(f"Export scaling failed: {error}") from error

    def process(self, frame, brightness=0, contrast=0, sharpness=0, compression=0,
                crop_top=0, crop_bottom=0, crop_left=0, crop_right=0, rotation=0):
        validate_processing_settings(brightness, contrast, sharpness, compression)
        validate_rotation(rotation)
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or (
            frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) < 1
        ):
            raise ValueError("Processing requires a nonempty uint8 BGR24 image.")
        crop = crop_rectangle(frame.shape[1], frame.shape[0], crop_top, crop_bottom,
                              crop_left, crop_right)
        stages = enhancement_stages(brightness, contrast, sharpness)
        if not stages:
            result = frame.copy()
            self._graph = self._key = None
        else:
            try:
                import av
            except ImportError as error:
                raise RuntimeError(
                    "PyAV is required for reproducible enhancements, including with "
                    "OpenCV input. Use the isolated GPL environment-eq.yml environment; "
                    "no legacy algorithm is substituted."
                ) from error
            try:
                incoming = av.VideoFrame.from_ndarray(frame, format="bgr24")
                incoming.time_base = Fraction(1, 1000)
                incoming.pts = self._sequence
                self._sequence += 1
                key = (frame.shape, brightness, contrast, sharpness)
                if self._graph is None or self._key != key:
                    graph = av.filter.Graph()
                    graph.threads = 1
                    source = graph.add_buffer(template=incoming)
                    graph.link_nodes(
                        source, *(graph.add(name, options) for name, options in stages),
                        graph.add("buffersink"),
                    )
                    graph.configure()
                    self._graph, self._key = graph, key
                self._graph.push(incoming)
                # Filter frames may have padded rows; the viewer requires tight BGR24.
                result = np.ascontiguousarray(self._graph.pull().to_ndarray(format="bgr24"))
            except (av.FFmpegError, ValueError) as error:
                self.close()
                raise RuntimeError(f"Reproducible enhancement processing failed: {error}") from error
        result = np.ascontiguousarray(result[
            crop.top:crop.top + crop.height, crop.left:crop.left + crop.width
        ])
        if rotation:
            result = rotate_image(result, rotation)
        if compression:
            success, encoded = cv2.imencode(
                ".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 100 - compression]
            )
            if not success:
                raise RuntimeError("Could not encode the JPEG teaching simulation.")
            result = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if result is None:
                raise RuntimeError("Could not decode the JPEG teaching simulation.")
        return result

    @staticmethod
    def process_frame(frame, brightness=0, contrast=0, sharpness=0, compression=0,
                      crop_top=0, crop_bottom=0, crop_left=0, crop_right=0, rotation=0):
        """Compatibility entry point; streaming callers own a persistent instance."""
        return FrameProcessor().process(frame, brightness, contrast, sharpness, compression,
                                        crop_top, crop_bottom, crop_left, crop_right, rotation)
