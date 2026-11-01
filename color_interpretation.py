# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""User-authorized defaults fill unspecified fields, never explicit declarations."""

from dataclasses import dataclass


class InputDecodeError(RuntimeError):
    """Input fidelity or decoding failed; never substitute another decoder."""


class InputCompatibilityError(InputDecodeError):
    """A recognized compatibility failure, not an I/O or runtime decoder error."""


class MissingColorMetadata(InputDecodeError):
    """A structurally supported source needs explicit user authorization."""


FIELDS = ("color_space", "color_range", "color_primaries", "color_transfer", "chroma_location")
SUPPORTED = {
    "color_space": ("bt709", "smpte170m"),
    "color_range": ("tv", "pc"),
    "color_primaries": ("bt709", "smpte170m"),
    "color_transfer": ("bt709", "smpte170m"),
    "chroma_location": ("left", "center"),
}
UNSPECIFIED = (None, "unknown", "unspecified")
NATIVE_FIELDS = (
    ("color_space", "colorspace", 2, {1: "bt709", 6: "smpte170m"}),
    ("color_range", "color_range", 0, {1: "tv", 2: "pc"}),
    ("color_primaries", "color_primaries", 2, {1: "bt709", 6: "smpte170m"}),
    ("color_transfer", "color_trc", 2, {1: "bt709", 6: "smpte170m"}),
)


@dataclass(frozen=True)
class ColorChoices:
    matrix: str = "smpte170m"
    range_name: str = "tv"

    def __post_init__(self):
        if self.matrix not in SUPPORTED["color_space"] or self.range_name not in SUPPORTED["color_range"]:
            raise ValueError("Unsupported assumed matrix/range.")

    def defaults(self):
        return {
            "color_space": self.matrix, "color_range": self.range_name,
            "color_primaries": self.matrix, "color_transfer": self.matrix,
            "chroma_location": "left",
        }


def resolve_reported(reported, choices, *, include_chroma=True):
    fields = FIELDS if include_chroma else FIELDS[:-1]
    missing = []
    result = {}
    for field in fields:
        value = reported.get(field)
        if value in UNSPECIFIED:
            missing.append(field)
        elif value not in SUPPORTED[field]:
            raise InputDecodeError(f"Unsupported explicit source {field}={value!r}; color assumptions cannot override it.")
        else:
            result[field] = value
    if missing and choices is None:
        raise MissingColorMetadata(
            "Unsupported or ambiguous native input: unspecified color metadata: "
            + ", ".join(missing)
        )
    if choices is not None:
        defaults = choices.defaults()
        # Missing SDR tags follow the effective matrix, not an ignored matrix selection.
        matrix = result.get("color_space", defaults["color_space"])
        defaults["color_primaries"] = defaults["color_transfer"] = matrix
        for field in missing:
            result[field] = defaults[field]
    return result


def native_reported(frame, context):
    result = {}
    for field, attr, unspecified, supported in NATIVE_FIELDS:
        value, stream = getattr(frame, attr), getattr(context, attr)
        for explicit in (value, stream):
            if explicit != unspecified and explicit not in supported:
                raise ValueError(
                    f"Unsupported or ambiguous native input: explicit {field}={explicit}; "
                    "assumptions cannot override unsupported/HDR metadata."
                )
        if stream != unspecified and value != unspecified and stream != value:
            raise ValueError(f"Stream and frame {field} metadata contradict each other.")
        # Explicit stream information is not an assumption.
        result[field] = supported.get(value if value != unspecified else stream)
    return result


def conversion_options(effective):
    if effective["chroma_location"] != "left":
        raise InputDecodeError("Centered chroma requires the complete conversion_stages graph.")
    matrix = "bt709" if effective["color_space"] == "bt709" else "bt601"
    range_name = "limited" if effective["color_range"] == "tv" else "full"
    return (
        f"in_color_matrix={matrix}:out_color_matrix={matrix}:"
        f"in_range={range_name}:out_range=full:flags=bicubic:"
        "in_h_chr_pos=0:in_v_chr_pos=128"
    )


def output_color_options(effective):
    return ("range=full:colorspace=gbr:"
            f"color_primaries={effective['color_primaries']}:"
            f"color_trc={effective['color_transfer']}")


def conversion_stages(effective):
    """Center needs explicit 4:4:4 upsampling: packed-RGB fast paths ignore siting."""
    if effective["chroma_location"] == "center":
        matrix = "bt709" if effective["color_space"] == "bt709" else "bt601"
        range_name = "limited" if effective["color_range"] == "tv" else "full"
        stages = [
            ("scale", f"in_color_matrix={matrix}:out_color_matrix={matrix}:"
             f"in_range={range_name}:out_range={range_name}:flags=bicubic:"
             "in_h_chr_pos=128:in_v_chr_pos=128:out_h_chr_pos=0:out_v_chr_pos=0"),
            ("format", "pix_fmts=yuv444p"),
            ("scale", f"in_color_matrix={matrix}:out_color_matrix={matrix}:"
             f"in_range={range_name}:out_range=full:flags=bicubic"),
        ]
    else:
        stages = [("scale", conversion_options(effective))]
    return [*stages, ("format", "pix_fmts=bgr24"),
            ("setparams", output_color_options(effective))]


def color_audit(metadata, choices, native_source):
    """Retain all distinct original frame declarations without duplicating the index."""
    originals = {field: sorted({str(entry.get(field)) for entry in metadata}) for field in FIELDS}
    effective = resolve_reported(metadata[0], choices)
    values = {}
    for field in FIELDS:
        missing = any(entry.get(field) in UNSPECIFIED for entry in metadata)
        origin = "reported"
        if missing:
            origin = ("user-selected (fills unspecified values only)" if field in FIELDS[:2]
                      else "assumed SDR" if field != "chroma_location" else "assumed left chroma")
        values[field] = {"effective": effective[field], "provenance": origin}
    return {
        "source_color_intent": (
            "Not established from source metadata; user authorized assumptions."
            if choices is not None else "Explicit supported source color metadata; no assumptions."),
        "original_frame_metadata": originals,
        "original_native_and_stream_metadata": native_source,
        "interpretation": values,
        "policy": ({"missing_matrix": choices.matrix, "missing_range": choices.range_name,
                    "missing_SDR_tags": "follow effective matrix", "missing_chroma_location": "left"}
                   if choices is not None else {}),
    }
