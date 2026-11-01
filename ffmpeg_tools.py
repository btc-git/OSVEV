# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Locate the FFmpeg/FFprobe executables OSVEV runs.

Displayed reproducible commands keep the conventional program name ``ffmpeg``
so they can be run with any compatible FFmpeg.  OSVEV itself executes the
bundled copy in a packaged build, then the copy beside the active Python
environment, and only then PATH.
"""

from functools import lru_cache
import os
from pathlib import Path
import shutil
import sys

TOOL_NAMES = ("ffmpeg", "ffprobe")


def _candidate_directories() -> list[Path]:
    directories = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        # Beside the bundled FFmpeg DLLs so Windows resolves them from the executable's folder.
        directories.append(Path(bundle))
    override = os.environ.get("OSVEV_FFMPEG_DIR")
    if override:
        directories.append(Path(override))
    prefix = Path(sys.prefix)
    directories += [prefix / "Library" / "bin", prefix / "bin"]
    return directories


@lru_cache(maxsize=None)
def tool_path(name: str) -> str:
    """Absolute path to ``ffmpeg`` or ``ffprobe``; falls back to the bare name."""
    if name not in TOOL_NAMES:
        raise ValueError(f"Unknown FFmpeg tool: {name}")
    executable = name + (".exe" if sys.platform == "win32" else "")
    for directory in _candidate_directories():
        candidate = directory / executable
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or name


def resolve_arguments(arguments):
    """Replace a conventional leading ``ffmpeg``/``ffprobe`` with the resolved path."""
    arguments = list(arguments)
    if arguments and arguments[0] in TOOL_NAMES:
        arguments[0] = tool_path(arguments[0])
    return arguments


def is_bundled(name: str = "ffmpeg") -> bool:
    bundle = getattr(sys, "_MEIPASS", None)
    return bool(bundle) and Path(tool_path(name)).parent == Path(bundle)
