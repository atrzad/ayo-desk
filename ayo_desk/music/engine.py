"""GStreamer playback: local files and streams, gapless transitions, no video window.

All callbacks run on the GLib main context. `playbin` signals that fire on streaming threads
only read values prepared beforehand by the main thread.
"""
import os
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstAudio", "1.0")
from gi.repository import Gst, GstAudio

PLAY_FLAGS_AUDIO = 2 | 16  # GST_PLAY_FLAG_AUDIO | GST_PLAY_FLAG_SOFT_VOLUME


def uri_for(location):
    location = str(location)
    if "://" in location:
        return location
    return Path(location).resolve().as_uri()


class Player:
    """One playbin. `changed()` after state changes, `started(path)` when a new track begins
    (including gapless switches), `error(text)` and `eos()` when the queue ran out."""

    def __init__(self, changed, error, eos, *, started=None, sink=None):
        Gst.init(None)
        self.changed, self.error, self.eos = changed, error, eos
        self.started = started or (lambda path: None)
        self.playbin = Gst.ElementFactory.make("playbin", "ayo-player")
        sink = sink or self._sink_from_environment()
        self.available = bool(self.playbin and (sink or Gst.ElementFactory.find("autoaudiosink")))
        self.playing = False
        self.path = None
        self.title = "Nenhuma música selecionada"
        self.artist = "Adicione arquivos à sua biblioteca para começar."
        self.stream_tags = {}
        self.next_path = None      # prepared by the main thread for gapless playback
        self._pending = None       # path handed to playbin in about-to-finish
        self._pending_seek = None
        self._volume = 1.0
        self.bus = None
        self.handler = None
        if not self.available:
            return
        self.playbin.set_property("flags", PLAY_FLAGS_AUDIO)
        if sink:
            self.playbin.set_property("audio-sink", sink)
        self.playbin.connect("about-to-finish", self._about_to_finish)
        self.bus = self.playbin.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect("message", self._message)

    @staticmethod
    def _sink_from_environment():
        # Tests and the smoke run use AYO_MUSIC_SINK=fakesink to stay silent.
        name = os.environ.get("AYO_MUSIC_SINK")
        if not name:
            return None
        sink = Gst.ElementFactory.make(name)
        if sink is not None and sink.find_property("sync"):
            sink.set_property("sync", True)
        return sink

    def load(self, path, play=True, start=0.0):
        if not self.available:
            raise ValueError("Instale gst-plugins-base e gst-plugins-good para habilitar a reprodução.")
        remote = "://" in str(path)
        if not remote:
            file = Path(path)
            if not file.is_file():
                raise ValueError("O arquivo não está mais disponível. Remova-o da biblioteca ou reconecte a unidade.")
            path = str(file.resolve())
        self.playbin.set_state(Gst.State.NULL)
        self.path = str(path)
        self._pending = None
        self.stream_tags = {}
        self.title, self.artist = (Path(path).stem, Path(path).parent.name) if not remote else (str(path), "")
        self.playbin.set_property("uri", uri_for(path))
        self._pending_seek = start if start and start > 0 else None
        result = self.playbin.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)
        if result == Gst.StateChangeReturn.FAILURE:
            self.playing = False
            raise ValueError("Não foi possível reproduzir este arquivo.")
        self.playing = play
        self.changed()

    def set_next(self, path):
        """Track that should follow without a gap (None = stop at the end)."""
        self.next_path = str(path) if path else None

    def _about_to_finish(self, playbin):
        # Streaming thread: only hand over what the main thread already decided.
        upcoming = self.next_path
        if upcoming and ("://" in upcoming or os.path.isfile(upcoming)):
            self._pending = upcoming
            playbin.set_property("uri", uri_for(upcoming))

    def toggle(self):
        if not self.available or not self.path:
            return
        self.set_playing(not self.playing)

    def set_playing(self, playing):
        if not self.available or not self.path:
            return
        self.playing = playing
        self.playbin.set_state(Gst.State.PLAYING if playing else Gst.State.PAUSED)
        self.changed()

    def stop(self):
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        self.playing = False
        self.path = None
        self._pending = None
        self.title, self.artist = "Nenhuma música selecionada", "Selecione uma faixa da biblioteca."
        self.changed()

    def volume(self, percent):
        """0–100 on a perceptual (cubic) scale, like desktop volume sliders."""
        self._volume = max(0, min(100, percent)) / 100
        if self.playbin:
            linear = GstAudio.StreamVolume.convert_volume(GstAudio.StreamVolumeFormat.CUBIC,
                                                          GstAudio.StreamVolumeFormat.LINEAR, self._volume)
            self.playbin.set_property("volume", linear)

    def set_muted(self, muted):
        if self.playbin:
            self.playbin.set_property("mute", bool(muted))

    def position(self):
        if not self.available or not self.path:
            return 0, 0
        ok_pos, position = self.playbin.query_position(Gst.Format.TIME)
        ok_dur, duration = self.playbin.query_duration(Gst.Format.TIME)
        return (position / Gst.SECOND if ok_pos else 0, duration / Gst.SECOND if ok_dur else 0)

    def seek(self, seconds):
        if self.available and self.path:
            return self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                            int(max(0, seconds) * Gst.SECOND))
        return False

    def _message(self, _bus, message):
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.playbin.set_state(Gst.State.NULL)
            self.playing = False
            self._pending = None
            self.error(str(error))
            self.changed()
        elif kind == Gst.MessageType.EOS:
            self.playbin.set_state(Gst.State.NULL)
            self.playing = False
            self.eos()
        elif kind == Gst.MessageType.STREAM_START:
            if self._pending:
                # playbin moved to the prepared track without stopping.
                self.path, self._pending = self._pending, None
                self.stream_tags = {}
                self.title, self.artist = Path(self.path).stem, Path(self.path).parent.name
                self.started(self.path)
                self.changed()
        elif kind == Gst.MessageType.ASYNC_DONE and self._pending_seek is not None:
            seconds, self._pending_seek = self._pending_seek, None
            self.seek(seconds)
        elif kind == Gst.MessageType.TAG:
            tags = message.parse_tag()
            for tag, attribute in ((Gst.TAG_TITLE, "title"), (Gst.TAG_ARTIST, "artist"),
                                   (Gst.TAG_ORGANIZATION, "organization")):
                found, value = tags.get_string(tag)
                if found:
                    self.stream_tags[attribute] = value
                    if attribute in ("title", "artist"):
                        setattr(self, attribute, value)
            self.changed()

    def close(self):
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        if self.bus:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.bus = None
