# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Optional convenience audio; never drives video selection or processing."""

import math
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaMetaData, QMediaPlayer

from playback_timing import PlaybackClock

SYNC_TOLERANCE_MS = 150
FIRST_RETRY_S = .75
STALLED_RETRY_S = 3.0
MAX_RETRY_S = 30.0
MAX_LEAD_MS = 1000


class AudioPreview(QObject):
    notice = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None
        self.output = None
        self.clock = None
        self.offset = 0.0
        self.failed = False
        self.volume = 50
        self.muted = False
        self._cycle = None
        self._last_seek = 0.0
        self._lead = 0
        self._seek_target = None
        self._learn = False
        self._last_position = None
        self._rising = 0
        self._retry = FIRST_RETRY_S
        self._speed_blocked = False
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.synchronize)

    def load(self, path, source_offset):
        self.clear()
        if source_offset is None or not math.isfinite(source_offset):
            self._fail("Audio unavailable: source timing cannot be aligned.")
            return
        if QMediaDevices.defaultAudioOutput().isNull():
            self._fail("Audio unavailable: no output device.")
            return
        self.offset = source_offset
        self.output = QAudioOutput(self)
        self.output.setVolume(self.volume / 100)
        self.output.setMuted(self.muted)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(self._error)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.playbackStateChanged.connect(self._emit_status)
        self.player.tracksChanged.connect(self._emit_status)
        self.player.activeTracksChanged.connect(self._emit_status)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._emit_status()

    def clear(self):
        self.pause()
        if self.player is not None:
            self.player.errorOccurred.disconnect(self._error)
            self.player.mediaStatusChanged.disconnect(self._status)
            self.player.playbackStateChanged.disconnect(self._emit_status)
            self.player.tracksChanged.disconnect(self._emit_status)
            self.player.activeTracksChanged.disconnect(self._emit_status)
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None
        if self.output is not None:
            self.output.deleteLater()
            self.output = None
        self.failed = False
        self._cycle = None
        self._lead = 0
        self._seek_target = None
        self.notice.emit("")
        self._emit_status()

    def set_volume(self, volume):
        self.volume = volume
        if self.output is not None:
            self.output.setVolume(volume / 100)

    def available_tracks(self):
        """Qt-discovered tracks, not an assumed mapping to container stream indices."""
        if self.player is None:
            return []
        return [metadata.stringValue(QMediaMetaData.Key.AudioCodec) or "unknown codec"
                for metadata in self.player.audioTracks()]

    def active_track(self):
        return self.player.activeAudioTrack() if self.player is not None else -1

    def select_track(self, index):
        if not 0 <= index < len(self.available_tracks()):
            raise ValueError("The selected Qt audio track is unavailable.")
        if self.failed:
            # One track's decoder error must not lock out the file's other tracks. Qt ignores
            # reselecting the active track, and the media-level playable/seekable check still applies.
            if (index == self.active_track() or not self.player.hasAudio()
                    or not self.player.isSeekable()):
                raise ValueError("The selected Qt audio track is unavailable.")
            self.failed = False
            self.notice.emit("")
        self.player.setActiveAudioTrack(index)
        self._cycle = None
        self.synchronize()
        self._emit_status()

    def set_muted(self, muted):
        self.muted = muted
        if self.output is not None:
            self.output.setMuted(muted)
        self._emit_status()

    def pause(self):
        self.timer.stop()
        self.clock = None
        self._speed_blocked = False
        self._last_position, self._rising = None, 0
        if self.player is not None:
            self.player.pause()
        self._emit_status()

    def follow(self, clock: PlaybackClock, source_offset: float):
        self.pause()
        if self.player is None or self.failed:
            return
        if clock.speed != 1:
            self._speed_blocked = True
            self._emit_status()
            return
        self.offset = source_offset
        self.clock = clock
        self._cycle = None
        self._retry = FIRST_RETRY_S
        self.timer.start()
        self.synchronize()

    def _status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            if not self.player.hasAudio() or not self.player.isSeekable():
                self._fail("Audio unavailable: no playable, seekable audio track.")
            elif self.clock is not None:
                self.synchronize()
        self._emit_status()

    def _error(self, error, message):
        self._fail(f"Audio unavailable: {message or error.name}")

    def _fail(self, message):
        self.failed = True
        self.pause()
        self.notice.emit(message)

    def status_text(self):
        """Actual audio-output state; never inferred from stream content."""
        if self.failed:
            return "Unavailable"
        if self.player is None:
            return "Not loaded"
        if self.player.mediaStatus() in (QMediaPlayer.MediaStatus.NoMedia,
                                         QMediaPlayer.MediaStatus.LoadingMedia):
            return "Loading"
        if self._speed_blocked:
            state = "Off (1x playback only)"
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            state = "Playing"
        elif self.clock is not None:
            state = "Not playing (outside audio track)"
        else:
            state = "Paused"
        return f"{state}, muted" if self.muted else state

    def _emit_status(self, *_):
        self.status.emit(self.status_text())

    def synchronize(self):
        player, clock = self.player, self.clock
        if player is None or clock is None or self.failed:
            return
        if player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.BufferedMedia, QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            return
        now = time.perf_counter()
        source = clock.source_origin + (now - clock.origin)
        cycle = math.floor(source / clock.timeline.duration)
        position = round((self.offset + source % clock.timeline.duration) * 1000)
        if position < 0 or (player.duration() > 0 and position >= player.duration()):
            player.pause()
            self._cycle = None
            return
        reported, previous = player.position(), self._last_position
        self._last_position = reported
        self._rising = self._rising + 1 if previous is not None and reported > previous else 0
        if cycle != self._cycle:
            self._cycle = cycle
            self._seek(position, now)
        elif self._seek_target is not None:
            # Until a seek's audio is playing, Qt reports the seek target or a stale pre-seek
            # position. Re-seeking then restarts the output again: the audible skip loop.
            if (self._rising >= 2 and previous >= self._seek_target
                    and reported <= self._seek_target + (now - self._last_seek) * 1000 + 250):
                if self._learn:
                    # Learn the output's seek-to-sound latency once per seek, never from later stalls.
                    self._lead = min(max(self._lead - (reported - position), 0), MAX_LEAD_MS)
                self._seek_target = None
            elif now - self._last_seek >= (self._retry if self._rising >= 2
                                            else max(self._retry, STALLED_RETRY_S)):
                self._correct(position, now)
        elif abs(reported - position) <= SYNC_TOLERANCE_MS:
            self._retry = FIRST_RETRY_S
        elif now - self._last_seek >= self._retry:
            self._correct(position, now)
        if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            player.play()

    def _correct(self, position, now):
        self._retry = min(self._retry * 2, MAX_RETRY_S)
        self._seek(position, now)

    def _seek(self, position, now):
        target = position + self._lead
        if self.player.duration() > 0 and target >= self.player.duration():
            target = position
        # Only a seek made while audio is already flowing measures the seek latency; starting or
        # resuming playback adds a one-time device/decoder start that corrections do not repeat.
        self._learn = (self._rising >= 2 and self.player.playbackState()
                       == QMediaPlayer.PlaybackState.PlayingState)
        # State first: setPosition synchronously re-enters synchronize via mediaStatusChanged.
        self._seek_target, self._last_seek = target, now
        self._last_position, self._rising = None, 0
        self.player.setPosition(target)
