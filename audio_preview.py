# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Optional convenience audio; never drives video selection or processing."""

import math
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaMetaData, QMediaPlayer

from playback_timing import PlaybackClock


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
        if self.failed or not 0 <= index < len(self.available_tracks()):
            raise ValueError("The selected Qt audio track is unavailable.")
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
        if (cycle != self._cycle or
                (now - self._last_seek >= 0.75 and abs(player.position() - position) > 150)):
            self._cycle = cycle
            self._last_seek = now
            player.setPosition(position)
        if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            player.play()
