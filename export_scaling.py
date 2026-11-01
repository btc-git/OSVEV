# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Export-only scaling; viewer OpenCV/Qt resizing remains unchanged."""

INTERPOLATION_FLAGS = {
    "Nearest": "neighbor",
    "Bilinear": "bilinear",
    "Bicubic": "bicubic",
    "Lanczos": "lanczos",
}


def scaling_stages(width: int, height: int, zoom_level: int, interpolation: str):
    if type(zoom_level) is not int or zoom_level < 1:
        raise ValueError("Export zoom must be a positive integer.")
    if interpolation not in INTERPOLATION_FLAGS:
        raise ValueError(f"Unsupported export interpolation: {interpolation}")
    if zoom_level == 1:
        return []
    return [
        ("scale", f"w={width * zoom_level}:h={height * zoom_level}:"
         f"flags={INTERPOLATION_FLAGS[interpolation]}:"
         "in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=full"),
        ("format", "pix_fmts=bgr24"),
    ]


def scaling_filter(width: int, height: int, zoom_level: int, interpolation: str) -> str:
    return ",".join(f"{name}={options}" for name, options in
                    scaling_stages(width, height, zoom_level, interpolation))
