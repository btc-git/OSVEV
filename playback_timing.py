# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Source-time deadlines, independent of timer rounding and decoder choice."""

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
from statistics import median


@dataclass(frozen=True)
class PlaybackTimeline:
    times: tuple[float, ...]
    duration: float
    mode: str
    preserve_order: bool = False

    @classmethod
    def from_metadata(cls, metadata: list[dict], count: int, fps: float, *,
                      time_base: Fraction | None = None):
        if count <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("Playback requires a positive frame count and finite FPS.")
        if len(metadata) == count:
            pts = tuple(entry.get("pts") for entry in metadata)
            tied = (all(type(value) is int for value in pts)
                    and any(b == a for a, b in zip(pts, pts[1:])))
            if tied:
                if time_base is None or time_base <= 0 or any(
                    b < a for a, b in zip(pts, pts[1:])
                ):
                    raise ValueError("Duplicate-PTS playback requires verified nondecreasing PTS and a time base.")
                try:
                    tail = Fraction(metadata[-1].get(
                        "duration_time", metadata[-1].get("pkt_duration_time", "")))
                except (TypeError, ValueError, ZeroDivisionError) as error:
                    raise ValueError("Duplicate-PTS playback requires an explicit final frame duration.") from error
                if tail <= 0:
                    raise ValueError("Duplicate-PTS playback requires a positive final frame duration.")
                times = tuple(float((value - pts[0]) * time_base) for value in pts)
                return cls(times, times[-1] + float(tail), "source timestamps (ordered duplicate PTS)", True)
            try:
                raw = tuple(float(entry["best_effort_timestamp_time"]) for entry in metadata)
                if not all(math.isfinite(value) for value in raw):
                    raise ValueError("Non-finite source timestamp.")
                if any(b <= a for a, b in zip(raw, raw[1:])):
                    raise ValueError("Non-increasing source timestamps.")
            except (KeyError, TypeError, ValueError):
                raw = ()
            if raw:
                times = tuple(value - raw[0] for value in raw)
                fallback = median(b - a for a, b in zip(times, times[1:])) if count > 1 else 1 / fps
                try:
                    tail = float(metadata[-1].get(
                        "duration_time", metadata[-1].get("pkt_duration_time", fallback)
                    ))
                except (TypeError, ValueError):
                    tail = fallback
                if not math.isfinite(tail) or tail <= 0:
                    tail = fallback
                return cls(times, times[-1] + tail, "source timestamps")
        return cls(tuple(index / fps for index in range(count)), count / fps,
                   "average FPS fallback (missing/invalid timestamps)")

    def source_time(self, position: int) -> float:
        cycle, index = divmod(position, len(self.times))
        return cycle * self.duration + self.times[index]

    def position_at(self, source_time: float) -> int:
        cycle = math.floor(source_time / self.duration)
        within = source_time - cycle * self.duration
        return cycle * len(self.times) + bisect_right(self.times, within) - 1


@dataclass(frozen=True)
class PlaybackTarget:
    position: int
    index: int
    deadline: float
    intentional_skips: int
    late_skips: int


class PlaybackClock:
    def __init__(self, timeline: PlaybackTimeline, index: int, speed: float, now: float):
        if not math.isfinite(speed) or speed <= 0 or not 0 <= index < len(timeline.times):
            raise ValueError("Invalid playback speed or starting index.")
        self.timeline = timeline
        self.speed = speed
        self.stride = 1 if timeline.preserve_order else max(1, round(speed))
        self.origin = now
        self.anchor = index
        self.presented_position = index
        self.next_position = index + self.stride
        self.source_origin = timeline.source_time(index)

    def deadline(self, position: int) -> float:
        return self.origin + (self.timeline.source_time(position) - self.source_origin) / self.speed

    def delay_ms(self, now: float) -> int:
        # Ceil prevents early delivery; absolute deadlines retain fractional milliseconds.
        return max(1, math.ceil((self.deadline(self.next_position) - now) * 1000))

    def due(self, now: float) -> PlaybackTarget | None:
        if now < self.deadline(self.next_position):
            return None
        if self.timeline.preserve_order:
            return PlaybackTarget(
                self.next_position, self.next_position % len(self.timeline.times),
                self.deadline(self.next_position), 0, 0,
            )
        source_now = self.source_origin + (now - self.origin) * self.speed
        latest = self.timeline.position_at(source_now)
        position = self.anchor + ((latest - self.anchor) // self.stride) * self.stride
        position = max(self.next_position, position)
        late_slots = (position - self.next_position) // self.stride
        advances = (position - self.presented_position) // self.stride
        return PlaybackTarget(
            position, position % len(self.timeline.times), self.deadline(position),
            advances * (self.stride - 1), late_slots,
        )

    def presented(self, target: PlaybackTarget) -> None:
        self.presented_position = target.position
        self.next_position = target.position + self.stride
