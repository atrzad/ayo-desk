"""GStreamer playback: local files and streams, gapless transitions, no video window.

All callbacks run on the GLib main context. `playbin` signals that fire on streaming threads
only read values prepared beforehand by the main thread.
"""
import os
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstAudio", "1.0")
from gi.repository import GLib, Gst, GstAudio

PLAY_FLAGS_AUDIO = 2 | 16  # GST_PLAY_FLAG_AUDIO | GST_PLAY_FLAG_SOFT_VOLUME
MIN_RATE, MAX_RATE = 0.5, 2.0
FADE_STEP_MS = 40


def uri_for(location):
    location = str(location)
    if "://" in location:
        return location
    return Path(location).resolve().as_uri()


def element(name, **properties):
    item = Gst.ElementFactory.make(name)
    if item is not None:
        for key, value in properties.items():
            item.set_property(key, value)
    return item


class Player:
    """One playbin. `changed()` after state changes, `started(path)` when a new track begins
    (including gapless switches), `error(text)` and `eos()` when the queue ran out."""

    def __init__(self, changed, error, eos, *, started=None, sink=None):
        Gst.init(None)
        self.changed, self.error, self.eos = changed, error, eos
        self.started = started or (lambda path: None)
        self.playbin = Gst.ElementFactory.make("playbin", "ayo-player")
        self.fixed_sink = sink or self._sink_from_environment()
        self.available = bool(self.playbin and (self.fixed_sink or Gst.ElementFactory.find("autoaudiosink")))
        self.playing = False
        self.path = None
        self.title = "Nenhuma música selecionada"
        self.artist = "Adicione arquivos à sua biblioteca para começar."
        self.stream_tags = {}
        self.next_path = None      # prepared by the main thread for gapless playback
        self.rate = 1.0
        self.output = None         # PulseAudio/PipeWire sink name, None = system default
        self._pending = None       # path handed to playbin in about-to-finish
        self._pending_seek = None
        self._needs_rate = False
        self._volume = 1.0         # what the user chose (0..1, cubic)
        self._fade = 1.0           # temporary multiplier for fades
        self._fade_source = 0
        self.bus = None
        self.handler = None
        if not self.available:
            return
        self.playbin.set_property("flags", PLAY_FLAGS_AUDIO)
        if self.fixed_sink:
            self.playbin.set_property("audio-sink", self.fixed_sink)
        self.filters = self._build_filters()
        if self.filters is not None:
            self.playbin.set_property("audio-filter", self.filters)
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

    def _build_filters(self):
        """audioconvert → scaletempo → audioconvert: speed changes keep the original pitch."""
        chain = [element("audioconvert"), element("scaletempo"), element("audioconvert")]
        if any(item is None for item in chain):
            return None
        bin_ = Gst.Bin.new("ayo-filters")
        for item in chain:
            bin_.add(item)
        for left, right in zip(chain, chain[1:]):
            left.link(right)
        bin_.add_pad(Gst.GhostPad.new("sink", chain[0].get_static_pad("sink")))
        bin_.add_pad(Gst.GhostPad.new("src", chain[-1].get_static_pad("src")))
        return bin_

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
        self._needs_rate = self.rate != 1.0
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

    def toggle(self, fade=0.0):
        if not self.available or not self.path:
            return
        self.set_playing(not self.playing, fade)

    def set_playing(self, playing, fade=0.0):
        """Play or pause; with `fade` seconds the volume glides instead of cutting."""
        if not self.available or not self.path:
            return
        self.playing = playing
        if playing:
            if fade:
                self._set_fade(0.0)
            self.playbin.set_state(Gst.State.PLAYING)
            if fade:
                self.fade_to(1.0, fade)
        elif fade:
            self.fade_to(0.0, fade, lambda: self._pause_after_fade())
        else:
            self.playbin.set_state(Gst.State.PAUSED)
        self.changed()

    def _pause_after_fade(self):
        if not self.playing:
            self.playbin.set_state(Gst.State.PAUSED)
        self._set_fade(1.0)

    def stop(self):
        self._cancel_fade()
        self._set_fade(1.0)
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        self.playing = False
        self.path = None
        self._pending = None
        self.title, self.artist = "Nenhuma música selecionada", "Selecione uma faixa da biblioteca."
        self.changed()

    # ── volume and fades ───────────────────────────────────────────────────
    def volume(self, percent):
        """0–100 on a perceptual (cubic) scale, like desktop volume sliders."""
        self._volume = max(0, min(100, percent)) / 100
        self._apply_volume()

    def _apply_volume(self):
        if self.playbin:
            cubic = self._volume * self._fade
            linear = GstAudio.StreamVolume.convert_volume(GstAudio.StreamVolumeFormat.CUBIC,
                                                          GstAudio.StreamVolumeFormat.LINEAR, cubic)
            self.playbin.set_property("volume", linear)

    def _set_fade(self, value):
        self._fade = max(0.0, min(1.0, value))
        self._apply_volume()

    def _cancel_fade(self):
        if self._fade_source:
            GLib.source_remove(self._fade_source)
            self._fade_source = 0

    def fade_to(self, target, seconds, done=None):
        """Glide the fade multiplier to `target` (0..1) over `seconds`, then call `done`."""
        self._cancel_fade()
        start, steps = self._fade, max(1, int(seconds * 1000 / FADE_STEP_MS))
        state = {"step": 0}

        def step():
            state["step"] += 1
            self._set_fade(start + (target - start) * state["step"] / steps)
            if state["step"] >= steps:
                self._fade_source = 0
                if done:
                    done()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self._fade_source = GLib.timeout_add(FADE_STEP_MS, step)

    def set_muted(self, muted):
        if self.playbin:
            self.playbin.set_property("mute", bool(muted))

    # ── position, speed and output ─────────────────────────────────────────
    def position(self):
        if not self.available or not self.path:
            return 0, 0
        ok_pos, position = self.playbin.query_position(Gst.Format.TIME)
        ok_dur, duration = self.playbin.query_duration(Gst.Format.TIME)
        return (position / Gst.SECOND if ok_pos else 0, duration / Gst.SECOND if ok_dur else 0)

    def seek(self, seconds):
        """Seek keeping the current speed (seek_simple would reset it to 1×)."""
        if not (self.available and self.path):
            return False
        return self.playbin.seek(self.rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                 Gst.SeekType.SET, int(max(0, seconds) * Gst.SECOND), Gst.SeekType.NONE, -1)

    def set_rate(self, rate):
        self.rate = max(MIN_RATE, min(MAX_RATE, round(float(rate), 2)))
        if self.path:
            self.seek(self.position()[0])

    def set_output(self, device):
        """Switch the output device (a pulsesink name, or None for the system default)."""
        self.output = device or None
        if not self.available or self.fixed_sink is not None:
            return
        sink = element("pulsesink", device=self.output) if self.output else None
        path, playing, position = self.path, self.playing, self.position()[0]
        self.playbin.set_state(Gst.State.NULL)
        self.playbin.set_property("audio-sink", sink)
        if path:
            self.load(path, play=playing, start=position)

    # ── bus ────────────────────────────────────────────────────────────────
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
                if self.rate != 1.0:
                    self.seek(0)
                self.started(self.path)
                self.changed()
        elif kind == Gst.MessageType.ASYNC_DONE and (self._pending_seek is not None or self._needs_rate):
            seconds = self._pending_seek if self._pending_seek is not None else self.position()[0]
            self._pending_seek, self._needs_rate = None, False
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
        self._cancel_fade()
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        if self.bus:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.bus = None
