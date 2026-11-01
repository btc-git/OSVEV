# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Optional local sample recordings used by some regression tests.

The sample clips are not distributed with the source. Tests that need them
skip when they are absent from the repository root.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_NAMES = ("short-clip.mp4", "gas-station.mp4")


def requires_samples(*names):
    missing = [name for name in names or SAMPLE_NAMES if not (ROOT / name).is_file()]
    return unittest.skipIf(bool(missing),
                           "Local sample recording(s) not present: " + ", ".join(missing))
