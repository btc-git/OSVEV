# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Exact integer pixel cropping and 90-degree rotation; never normalize dimensions."""

from dataclasses import dataclass


CROP_KEYS = ("crop_top", "crop_bottom", "crop_left", "crop_right")


@dataclass(frozen=True)
class CropRectangle:
    left: int
    top: int
    width: int
    height: int

    @property
    def filter(self) -> str:
        return f"crop=w={self.width}:h={self.height}:x={self.left}:y={self.top}:exact=1"


def crop_rectangle(width: int, height: int, crop_top: int = 0, crop_bottom: int = 0,
                   crop_left: int = 0, crop_right: int = 0) -> CropRectangle:
    for name, value in zip(CROP_KEYS, (crop_top, crop_bottom, crop_left, crop_right)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer pixel count.")
    remaining_width, remaining_height = width - crop_left - crop_right, height - crop_top - crop_bottom
    if remaining_width < 1 or remaining_height < 1:
        raise ValueError("Crop must leave at least one pixel of width and height; no dimensions are adjusted.")
    return CropRectangle(crop_left, crop_top, remaining_width, remaining_height)


# Clockwise degrees; exact 90-degree pixel transposes only, never interpolated.
ROTATIONS = (0, 90, 180, 270)
ROTATION_LABELS = {0: "0° (Original)", 90: "90° clockwise", 180: "180°", 270: "270° clockwise"}
_ROTATION_FILTERS = {90: "transpose=dir=clock", 180: "hflip,vflip", 270: "transpose=dir=cclock"}


def validate_rotation(rotation) -> int:
    if type(rotation) is not int or rotation not in ROTATIONS:
        raise ValueError(f"Rotation must be one of {ROTATIONS} degrees clockwise: {rotation}")
    return rotation


def rotate_image(image, rotation: int = 0):
    """Exact clockwise rotation of a cropped image; returns a contiguous copy."""
    import numpy as np
    validate_rotation(rotation)
    return np.ascontiguousarray(np.rot90(image, k=-(rotation // 90)))


def rotation_filter(rotation: int = 0) -> str:
    """Lossless FFmpeg equivalent of rotate_image; empty for 0 degrees."""
    return _ROTATION_FILTERS.get(validate_rotation(rotation), "")


def rotated_size(width: int, height: int, rotation: int = 0) -> tuple[int, int]:
    return (height, width) if validate_rotation(rotation) in (90, 270) else (width, height)


def unrotate_point(x: int, y: int, width: int, height: int, rotation: int = 0) -> tuple[int, int]:
    """Map pixel (x, y) of the rotated image to the cropped width x height image."""
    validate_rotation(rotation)
    if rotation == 90:
        return y, height - 1 - x
    if rotation == 180:
        return width - 1 - x, height - 1 - y
    if rotation == 270:
        return width - 1 - y, x
    return x, y
