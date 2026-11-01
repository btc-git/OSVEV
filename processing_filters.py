# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Shared, explicit BGR24 enhancement contract for libavfilter and FFmpeg."""

# Earlier development logs used "osvev-eq-unsharp-experiment-1" for this same filter contract.
PIPELINE_ID = "osvev-eq-unsharp-1"


def validate_processing_settings(brightness: int, contrast: int, sharpness: int,
                                 compression: int = 0) -> None:
    for name, value, low, high in (
        ("brightness", brightness, -100, 100), ("contrast", contrast, -100, 100),
        ("sharpness", sharpness, 0, 100), ("compression", compression, 0, 99),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid {name} setting: {value}")


def enhancement_stages(brightness: int = 0, contrast: int = 0,
                       sharpness: int = 0) -> tuple[tuple[str, str], ...]:
    """Spatial filters only; every push has exactly one immediate BGR output."""
    validate_processing_settings(brightness, contrast, sharpness)
    if not (brightness or contrast or sharpness):
        return ()
    conversion = (
        "in_color_matrix=bt709:out_color_matrix=bt709:"
        "in_range=full:out_range=full:flags=bicubic"
    )
    to_yuv = (("scale", conversion), ("format", "pix_fmts=yuv444p"))
    to_bgr = (("scale", conversion), ("format", "pix_fmts=bgr24"))
    stages = list(to_yuv)
    if brightness or contrast:
        stages.append(("eq", f"brightness={brightness / 100:.2f}:contrast={1 + contrast / 100:.2f}"))
    if sharpness:
        stages.append(("unsharp", f"5:5:{sharpness / 100:.2f}:5:5:0"))
    return tuple(stages) + to_bgr


def enhancement_filter(brightness: int = 0, contrast: int = 0,
                       sharpness: int = 0) -> str:
    return ",".join(f"{name}={options}" for name, options in
                    enhancement_stages(brightness, contrast, sharpness)) or "null"
