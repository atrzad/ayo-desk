"""GStreamer playback: gapless, crossfade, loudness leveling, equalizer, speed and spectrum.

Two "decks" (playbins) exist so one track can fade into the next; only the primary deck drives
the UI. All callbacks run on the GLib main context. `playbin` signals that fire on streaming
threads only read values prepared beforehand by the main thread.
"""
import math
import os
from pathlib import Path
import re
import threading
import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstAudio", "1.0")
from gi.repository import GLib, Gst, GstAudio

PLAY_FLAGS_AUDIO = 2 | 16  # GST_PLAY_FLAG_AUDIO | GST_PLAY_FLAG_SOFT_VOLUME
MIN_RATE, MAX_RATE = 0.5, 2.0
FADE_STEP_MS = 40
EQ_BANDS = (29, 59, 119, 237, 474, 947, 1900, 3800, 7500, 15000)  # equalizer-10bands centres (Hz)
SPECTRUM_BANDS = 48
MAGNITUDES = re.compile(r"magnitude=\(float\)\{([^}]*)\}")


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


def db_to_factor(db):
    return 10 ** (db / 20)


class Deck:
    """A playbin and its filter chain:
    audioconvert → volume (loudness) → rglimiter → audioconvert → equalizer-10bands → audioconvert
    → scaletempo → audioconvert → spectrum."""

    def __init__(self, player, index, sink):
        self.player = player
        self.index = index
        self.playbin = Gst.ElementFactory.make("playbin", f"ayo-deck-{index}")
        self.playbin.set_property("flags", PLAY_FLAGS_AUDIO)
        if sink is not None:
            self.playbin.set_property("audio-sink", sink)
        self.gain = self.eq = self.spectrum = None
        self.level = 1.0      # crossfade multiplier for this deck
        self.path = None
        self._build_filters()
        self.playbin.connect("about-to-finish", player._about_to_finish, self)
        self.bus = self.playbin.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect("message", player._message, self)

    def _build_filters(self):
        names = [("audioconvert", None), ("volume", "gain"), ("rglimiter", None), ("audioconvert", None),
                 ("equalizer-10bands", "eq"), ("audioconvert", None), ("scaletempo", None),
                 ("audioconvert", None), ("spectrum", "spectrum")]
        chain = []
        for factory, attribute in names:
            item = Gst.ElementFactory.make(factory)
            if item is None:
                continue  # optional effects degrade gracefully when a plugin is missing
            chain.append(item)
            if attribute:
                setattr(self, attribute, item)
        if len(chain) < 2:
            return
        if self.spectrum is not None:
            self.spectrum.set_property("bands", SPECTRUM_BANDS)
            self.spectrum.set_property("threshold", -70)
            self.spectrum.set_property("interval", 40 * Gst.MSECOND)
            self.spectrum.set_property("post-messages", False)
        bin_ = Gst.Bin.new(f"ayo-filters-{self.index}")
        for item in chain:
            bin_.add(item)
        for left, right in zip(chain, chain[1:]):
            left.link(right)
        bin_.add_pad(Gst.GhostPad.new("sink", chain[0].get_static_pad("sink")))
        bin_.add_pad(Gst.GhostPad.new("src", chain[-1].get_static_pad("src")))
        self.playbin.set_property("audio-filter", bin_)

    def set_gain(self, factor):
        if self.gain is not None:
            self.gain.set_property("volume", max(0.0, min(10.0, factor)))

    def set_eq(self, bands):
        if self.eq is not None:
            for number, value in enumerate(bands[:10]):
                self.eq.set_property(f"band{number}", max(-24.0, min(12.0, float(value))))

    def stop(self):
        self.playbin.set_state(Gst.State.NULL)
        self.path = None

    def close(self):
        self.stop()
        if self.bus is not None:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.bus = None


class Player:
    """Primary deck plays; the other one is used during crossfades.

    Callbacks: `changed()` after state changes, `started(path)` when a gapless switch starts a
    new track, `error(text)`, `eos()` when nothing follows, and optionally `spectrum(levels)`.
    """

    def __init__(self, changed, error, eos, *, started=None, sink=None, spectrum=None):
        Gst.init(None)
        self.changed, self.error, self.eos = changed, error, eos
        self.started = started or (lambda path: None)
        self.spectrum_callback = spectrum
        self.fixed_sink = sink if sink is not None else self._sink_from_environment()
        can_play = Gst.ElementFactory.find("playbin") is not None
        self.available = bool(can_play and (self.fixed_sink or Gst.ElementFactory.find("autoaudiosink")))
        self.playing = False
        self.path = None
        self.title = "Nenhuma música selecionada"
        self.artist = "Adicione arquivos à sua biblioteca para começar."
        self.stream_tags = {}
        self.next_path = None      # prepared by the main thread for gapless playback
        self.next_gain = 1.0
        self.rate = 1.0
        self.output = None         # PulseAudio/PipeWire sink name, None = system default
        self.eq_bands = [0.0] * 10
        self._pending = None       # path handed to playbin in about-to-finish
        self._pending_gain = 1.0
        # A flushing seek while playbin switches tracks gaplessly deadlocks GStreamer. The lock makes
        # the two exclusive: the switch is skipped during a seek, and seeks wait for the switch.
        self._switch_lock = threading.Lock()
        self._switching = False
        self._deferred_seek = None
        self._pending_seek = None
        self._needs_rate = False
        self._volume = 1.0         # what the user chose (0..1, cubic)
        self._fade = 1.0           # pause/sleep fades
        self._fade_source = 0
        self._xfade = None         # {"old": Deck, "start": t, "seconds": s}
        self._xfade_source = 0
        self._spectrum_on = False
        self.decks = []
        self.deck = None
        if not self.available:
            self.playbin = None
            return
        self.decks = [Deck(self, 0, self._new_sink()), Deck(self, 1, self._new_sink())]
        self.deck = self.decks[0]

    @property
    def playbin(self):
        return self.deck.playbin if self.deck else None

    @playbin.setter
    def playbin(self, _value):
        pass  # kept for compatibility: the primary deck owns the playbin

    @staticmethod
    def _sink_from_environment():
        # Tests and the smoke run use AYO_MUSIC_SINK=fakesink to stay silent.
        return os.environ.get("AYO_MUSIC_SINK") or None

    def _new_sink(self):
        """Each deck needs its own sink; a given element is cloned by factory for the second deck."""
        wanted = self.fixed_sink
        if wanted is None:
            return element("pulsesink", device=self.output) if self.output else None
        if isinstance(wanted, Gst.Element):
            if not getattr(self, "_sink_used", False):
                self._sink_used = True
                return wanted
            factory = wanted.get_factory().get_name()
            clone = element(factory)
            if clone is not None and clone.find_property("sync"):
                clone.set_property("sync", wanted.get_property("sync"))
            return clone
        clone = element(str(wanted))
        if clone is not None and clone.find_property("sync"):
            clone.set_property("sync", True)
        return clone

    def _other(self):
        return self.decks[1] if self.deck is self.decks[0] else self.decks[0]

    # ── loading and transport ──────────────────────────────────────────────
    def load(self, path, play=True, start=0.0, gain=1.0):
        if not self.available:
            raise ValueError("Instale gst-plugins-base e gst-plugins-good para habilitar a reprodução.")
        remote = "://" in str(path)
        if not remote:
            file = Path(path)
            if not file.is_file():
                raise ValueError("O arquivo não está mais disponível. Remova-o da biblioteca ou reconecte a unidade.")
            path = str(file.resolve())
        self._finish_crossfade()
        for deck in self.decks:
            deck.stop()
        deck = self.deck
        deck.level = 1.0
        deck.path = self.path = str(path)
        deck.set_gain(gain)
        deck.set_eq(self.eq_bands)
        self._reset_switch()
        self.stream_tags = {}
        self.title, self.artist = (Path(path).stem, Path(path).parent.name) if not remote else (str(path), "")
        deck.playbin.set_property("uri", uri_for(path))
        self._pending_seek = start if start and start > 0 else None
        self._needs_rate = self.rate != 1.0
        self._apply_volume()
        result = deck.playbin.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)
        if result == Gst.StateChangeReturn.FAILURE:
            self.playing = False
            raise ValueError("Não foi possível reproduzir este arquivo.")
        self.playing = play
        self.changed()

    def set_next(self, path, gain=1.0):
        """Track that should follow without a gap (None = stop at the end, or crossfade instead)."""
        self.next_path = str(path) if path else None
        self.next_gain = gain

    def _about_to_finish(self, playbin, deck):
        # Streaming thread: only hand over what the main thread already decided.
        upcoming = self.next_path
        # At a speed other than 1× the next track needs a seek to apply the rate; those transitions
        # use a normal load instead of a gapless switch.
        if self.rate != 1.0 or deck is not self.deck or self._xfade is not None:
            return
        if not upcoming or not ("://" in upcoming or os.path.isfile(upcoming)):
            return
        if not self._switch_lock.acquire(blocking=False):
            return  # a seek is running on the main thread: this track ends normally instead
        try:
            self._pending, self._pending_gain = upcoming, self.next_gain
            self._switching = True
            playbin.set_property("uri", uri_for(upcoming))
        finally:
            self._switch_lock.release()

    def _switch_settled(self):
        """A moment after the new stream started, seeking is safe again."""
        self._switching = False
        seconds, self._deferred_seek = self._deferred_seek, None
        if seconds is not None and self.path:
            self.seek(seconds)
        return False

    def _reset_switch(self):
        self._pending = None
        self._switching = False
        self._deferred_seek = None

    def toggle(self, fade=0.0):
        if not self.available or not self.path:
            return
        self.set_playing(not self.playing, fade)

    def set_playing(self, playing, fade=0.0):
        """Play or pause; with `fade` seconds the volume glides instead of cutting."""
        if not self.available or not self.path:
            return
        self._finish_crossfade()
        self.playing = playing
        playbin = self.deck.playbin
        if playing:
            if fade:
                self._set_fade(0.0)
            playbin.set_state(Gst.State.PLAYING)
            if fade:
                self.fade_to(1.0, fade)
        elif fade:
            self.fade_to(0.0, fade, self._pause_after_fade)
        else:
            playbin.set_state(Gst.State.PAUSED)
        self.changed()

    def _pause_after_fade(self):
        if not self.playing:
            self.deck.playbin.set_state(Gst.State.PAUSED)
        self._set_fade(1.0)

    def stop(self):
        self._cancel_fade()
        self._finish_crossfade()
        self._set_fade(1.0)
        for deck in self.decks:
            deck.stop()
        self.playing = False
        self.path = None
        self._reset_switch()
        self.title, self.artist = "Nenhuma música selecionada", "Selecione uma faixa da biblioteca."
        self.changed()

    # ── crossfade ──────────────────────────────────────────────────────────
    def crossfade_to(self, path, seconds, gain=1.0):
        """Start `path` on the other deck and fade between them (equal-power curve)."""
        if not self.available or not self.path or seconds <= 0:
            self.load(path, gain=gain)
            return
        if "://" not in str(path) and not Path(path).is_file():
            raise ValueError("O arquivo não está mais disponível. Remova-o da biblioteca ou reconecte a unidade.")
        self._finish_crossfade()
        old, new = self.deck, self._other()
        new.stop()
        new.level = 0.0
        new.set_gain(gain)
        new.set_eq(self.eq_bands)
        new.path = self.path = str(Path(path).resolve()) if "://" not in str(path) else str(path)
        new.playbin.set_property("uri", uri_for(new.path))
        self.deck = new
        self._reset_switch()
        self.stream_tags = {}
        self.title, self.artist = Path(self.path).stem, Path(self.path).parent.name
        self._needs_rate = self.rate != 1.0
        self._apply_volume()
        if new.playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.deck = old
            self.path = old.path
            raise ValueError("Não foi possível reproduzir este arquivo.")
        self.playing = True
        self._xfade = {"old": old, "start": time.monotonic(), "seconds": float(seconds)}
        self._xfade_source = GLib.timeout_add(FADE_STEP_MS, self._crossfade_step)
        self._set_spectrum(self._spectrum_on)
        self.changed()

    def _crossfade_step(self):
        fade = self._xfade
        if fade is None:
            self._xfade_source = 0
            return GLib.SOURCE_REMOVE
        progress = min(1.0, (time.monotonic() - fade["start"]) / fade["seconds"])
        fade["old"].level = math.cos(progress * math.pi / 2)
        self.deck.level = math.sin(progress * math.pi / 2)
        self._apply_volume()
        if progress >= 1.0:
            self._xfade_source = 0
            self._finish_crossfade()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _finish_crossfade(self):
        fade, self._xfade = self._xfade, None
        if self._xfade_source:
            GLib.source_remove(self._xfade_source)
            self._xfade_source = 0
        if fade is not None:
            fade["old"].stop()
            fade["old"].level = 1.0
            if self.deck:
                self.deck.level = 1.0
            self._apply_volume()

    @property
    def crossfading(self):
        return self._xfade is not None

    # ── volume, loudness, equalizer and fades ──────────────────────────────
    def volume(self, percent):
        """0–100 on a perceptual (cubic) scale, like desktop volume sliders."""
        self._volume = max(0, min(100, percent)) / 100
        self._apply_volume()

    def _apply_volume(self):
        for deck in self.decks:
            cubic = self._volume * self._fade * deck.level
            linear = GstAudio.StreamVolume.convert_volume(GstAudio.StreamVolumeFormat.CUBIC,
                                                          GstAudio.StreamVolumeFormat.LINEAR, cubic)
            deck.playbin.set_property("volume", linear)

    def set_gain(self, factor):
        """Loudness leveling for the current track (1.0 = unchanged)."""
        if self.deck:
            self.deck.set_gain(factor)

    def set_equalizer(self, bands):
        """Ten gains in dB (−24..+12), from 29 Hz to 15 kHz; all zeros is flat."""
        self.eq_bands = [float(value) for value in (list(bands) + [0.0] * 10)[:10]]
        for deck in self.decks:
            deck.set_eq(self.eq_bands)

    def set_spectrum(self, active):
        self._spectrum_on = bool(active)
        self._set_spectrum(self._spectrum_on)

    def _set_spectrum(self, active):
        for deck in self.decks:
            if deck.spectrum is not None:
                deck.spectrum.set_property("post-messages", bool(active) and deck is self.deck)

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
        for deck in self.decks:
            deck.playbin.set_property("mute", bool(muted))

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
        self._finish_crossfade()
        with self._switch_lock:
            if self._switching:
                self._deferred_seek = seconds  # applied by _switch_settled
                return True
            return self.playbin.seek(self.rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                     Gst.SeekType.SET, int(max(0, seconds) * Gst.SECOND), Gst.SeekType.NONE, -1)

    def set_rate(self, rate):
        self.rate = max(MIN_RATE, min(MAX_RATE, round(float(rate), 2)))
        if self.path:
            self.seek(self.position()[0])

    def running_time(self):
        """Current running time of the primary deck (to line spectrum frames up with the sound)."""
        clock = self.playbin.get_clock() if self.playbin else None
        if clock is None:
            return None
        return clock.get_time() - self.playbin.get_base_time()

    def set_output(self, device):
        """Switch the output device (a pulsesink name, or None for the system default)."""
        self.output = device or None
        if not self.available or self.fixed_sink is not None:
            return
        path, playing, position = self.path, self.playing, self.position()[0]
        gain = self.deck.gain.get_property("volume") if self.deck.gain is not None else 1.0
        self._finish_crossfade()
        for deck in self.decks:
            deck.stop()
            deck.playbin.set_property("audio-sink", self._new_sink())
        if path:
            self.load(path, play=playing, start=position, gain=gain)

    # ── bus ────────────────────────────────────────────────────────────────
    def _message(self, _bus, message, deck):
        kind = message.type
        primary = deck is self.deck
        if kind == Gst.MessageType.ELEMENT and primary and self.spectrum_callback:
            structure = message.get_structure()
            if structure is not None and structure.get_name() == "spectrum":
                found = MAGNITUDES.search(structure.to_string())
                if found:
                    levels = [float(value) for value in found[1].split(",") if value.strip()]
                    ok, end = structure.get_clock_time("endtime")
                    self.spectrum_callback(levels, end if ok else None)
            return
        if not primary:
            if kind in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
                deck.stop()  # the fading-out track ended early or failed: just let it go
            return
        if kind == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            deck.stop()
            self.playing = False
            self._reset_switch()
            self.error(str(error))
            self.changed()
        elif kind == Gst.MessageType.EOS:
            deck.playbin.set_state(Gst.State.NULL)
            self.playing = False
            self._reset_switch()
            self.eos()
        elif kind == Gst.MessageType.STREAM_START:
            if self._pending:
                # playbin moved to the prepared track without stopping.
                self.path, self._pending = self._pending, None
                deck.path = self.path
                deck.set_gain(self._pending_gain)
                self.stream_tags = {}
                self.title, self.artist = Path(self.path).stem, Path(self.path).parent.name
                GLib.timeout_add(300, self._switch_settled)
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
        self._finish_crossfade()
        for deck in self.decks:
            deck.close()
